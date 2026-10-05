"""
EC2 Auto-Discovery Agent — Zero Touch
──────────────────────────────────────
Automatically shows ANY EC2 instance in the dashboard within 2 minutes
of it being launched in AWS Console.

HOW IT WORKS (no Node Exporter / no SSH / no port 9100):
  Every 2 minutes:
  1. Call AWS EC2 API → get ALL running instances
  2. Write prometheus/targets/ec2_inventory.yml with instance metadata
  3. Update CloudWatch Exporter config with instance IDs
  4. Hot-reload Prometheus → dashboard updates automatically
  5. Notify MS Teams if new/removed instances detected

METRICS (from CloudWatch — available for ALL instances automatically):
  CPU Utilization    ← aws_ec2_cpuutilization_average
  Network In/Out     ← aws_ec2_network_in_sum / network_out_sum
  Status Check       ← aws_ec2_status_check_failed_sum (0=OK, 1=FAIL)
  EBS Read/Write     ← aws_ec2_ebsread_bytes_sum / ebswrite_bytes_sum

API:
  GET  /health     — liveness + next scan
  GET  /metrics    — Prometheus self-metrics
  GET  /status     — last sync summary
  GET  /instances  — all discovered instances
  GET  /schedule   — scan timing info
  GET  /debug      — AWS connectivity diagnostics
  POST /discover   — trigger immediate sync now
"""

import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

from .ec2_state_sync import (
    fetch_all_ec2_instances,
    write_instance_targets,
    write_cloudwatch_config,
    EC2Instance,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s — %(message)s"
)
logger = logging.getLogger(__name__)

DISCOVERY_INTERVAL = int(os.getenv("DISCOVERY_INTERVAL_MINUTES", "2"))
PROMETHEUS_URL     = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
MSTEAMS_WEBHOOK    = os.getenv("MSTEAMS_WEBHOOK_URL", "")

# ── Prometheus self-metrics ───────────────────────────────────────────
instances_total   = Gauge("autodiscovery_instances_total",    "Total EC2 instances")
instances_running = Gauge("autodiscovery_instances_running",  "Running EC2 instances")
instances_stopped = Gauge("autodiscovery_instances_stopped",  "Stopped EC2 instances")
discovery_runs    = Counter("autodiscovery_runs_total",       "Total sync cycles")
discovery_errors  = Counter("autodiscovery_errors_total",     "Sync errors")
last_run_ts       = Gauge("autodiscovery_last_run_timestamp", "Last sync unix timestamp")
next_run_ts       = Gauge("autodiscovery_next_run_timestamp", "Next sync unix timestamp")
sync_duration     = Gauge("autodiscovery_duration_seconds",   "Last sync duration")

app = FastAPI(
    title="EC2 Auto-Discovery (Zero Touch)",
    description="New instances appear in dashboard automatically — no Node Exporter needed",
    version="3.0.0"
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

scheduler       = AsyncIOScheduler()
last_result     = {}
all_instances: list[EC2Instance] = []
previous_ids: set = set()
run_count       = 0
next_run_at     = None


async def reload_prometheus():
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{PROMETHEUS_URL}/-/reload")
            if r.status_code == 200:
                logger.info("✅ Prometheus reloaded")
            else:
                logger.warning("Prometheus reload returned %s", r.status_code)
    except Exception as e:
        logger.error("Prometheus reload failed: %s", e)


async def notify_teams(added: list, removed: list, total: int):
    if not MSTEAMS_WEBHOOK or (not added and not removed):
        return
    lines = []
    for inst in added:
        lines.append(f"✅ **New:** {inst.display_name} ({inst.instance_id}) — {inst.instance_type} — {inst.region}")
    for name in removed:
        lines.append(f"🔴 **Removed:** {name}")

    payload = {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard", "version": "1.4",
                "body": [
                    {"type": "Container", "style": "accent", "items": [
                        {"type": "TextBlock", "weight": "Bolder", "size": "Medium", "wrap": True,
                         "text": f"🔍 EC2 Inventory Update — {total} instances monitored"}
                    ]},
                    {"type": "TextBlock", "text": "\n\n".join(lines), "wrap": True, "spacing": "Medium"}
                ]
            }
        }]
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(MSTEAMS_WEBHOOK, json=payload)
    except Exception as e:
        logger.warning("Teams notify failed: %s", e)


async def run_sync_cycle():
    global last_result, all_instances, previous_ids, run_count, next_run_at

    run_count += 1
    start = datetime.now(timezone.utc)
    next_run_at = start + timedelta(minutes=DISCOVERY_INTERVAL)
    next_run_ts.set(next_run_at.timestamp())

    logger.info("══════════════════════════════════════════════════════")
    logger.info("🔍 EC2 SYNC #%d — %s UTC", run_count, start.strftime("%H:%M:%S"))
    logger.info("   Next sync at: %s UTC", next_run_at.strftime("%H:%M:%S"))
    logger.info("══════════════════════════════════════════════════════")

    try:
        loop = asyncio.get_event_loop()

        # Fetch all EC2 instances from AWS API
        instances = await loop.run_in_executor(None, fetch_all_ec2_instances)
        all_instances = instances

        running = [i for i in instances if i.is_running]
        stopped = [i for i in instances if not i.is_running]

        logger.info("📦 Found %d instances (%d running, %d stopped)",
                    len(instances), len(running), len(stopped))
        for inst in instances:
            icon = "🟢" if inst.is_running else "⚫"
            logger.info("   %s %s (%s) — %s — %s — %s",
                        icon, inst.display_name, inst.instance_id,
                        inst.instance_type, inst.region, inst.state)

        # Detect new / removed
        current_ids = {i.instance_id for i in running}
        new_ids     = current_ids - previous_ids
        removed_ids = previous_ids - current_ids

        newly_added   = [i for i in running if i.instance_id in new_ids]
        newly_removed = list(removed_ids)

        if newly_added:
            logger.info("🆕 NEW instances detected:")
            for inst in newly_added:
                logger.info("   + %s (%s)", inst.display_name, inst.instance_id)

        if newly_removed:
            logger.info("🗑️  Removed instances: %s", newly_removed)

        previous_ids = current_ids

        # Write targets + CloudWatch config
        await loop.run_in_executor(None, write_instance_targets, instances)
        await loop.run_in_executor(None, write_cloudwatch_config, instances)

        # Hot-reload Prometheus
        await reload_prometheus()

        # Notify Teams on changes
        await notify_teams(newly_added, newly_removed, len(running))

        # Update metrics
        instances_total.set(len(instances))
        instances_running.set(len(running))
        instances_stopped.set(len(stopped))
        discovery_runs.inc()
        last_run_ts.set(start.timestamp())
        duration = (datetime.now(timezone.utc) - start).total_seconds()
        sync_duration.set(duration)

        last_result = {
            "total":            len(instances),
            "running":          len(running),
            "stopped":          len(stopped),
            "newly_added":      [i.display_name for i in newly_added],
            "newly_removed":    newly_removed,
            "scan_number":      run_count,
            "timestamp":        start.isoformat(),
            "duration_seconds": round(duration, 2),
            "next_scan_at":     next_run_at.isoformat(),
            "instances": [
                {
                    "name":          i.display_name,
                    "id":            i.instance_id,
                    "type":          i.instance_type,
                    "state":         i.state,
                    "region":        i.region,
                    "az":            i.az,
                    "private_ip":    i.private_ip,
                    "public_ip":     i.public_ip,
                    "os":            i.platform,
                }
                for i in instances
            ]
        }

        logger.info("✅ Sync #%d complete in %.1fs — %d running instances",
                    run_count, duration, len(running))

    except Exception as e:
        discovery_errors.inc()
        logger.error("❌ Sync #%d failed: %s", run_count, e, exc_info=True)


@app.on_event("startup")
async def startup():
    logger.info("╔══════════════════════════════════════════════════════╗")
    logger.info("║   EC2 AUTO-DISCOVERY — ZERO TOUCH v3.0              ║")
    logger.info("║   New instances → dashboard in < 2 minutes          ║")
    logger.info("║   No Node Exporter / No SSH / No manual steps       ║")
    logger.info("╠══════════════════════════════════════════════════════╣")
    logger.info("║  Interval : every %d minutes                        ║", DISCOVERY_INTERVAL)
    logger.info("║  Regions  : %-38s ║", os.getenv("AWS_REGIONS", "us-east-1"))
    logger.info("║  Metrics  : CloudWatch (CPU, Network, StatusCheck)  ║")
    logger.info("╚══════════════════════════════════════════════════════╝")

    scheduler.add_job(
        run_sync_cycle,
        trigger=IntervalTrigger(minutes=DISCOVERY_INTERVAL),
        id="ec2-sync",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.start()

    # Run immediately on start
    asyncio.create_task(run_sync_cycle())


@app.on_event("shutdown")
async def shutdown():
    scheduler.shutdown(wait=False)


@app.get("/health")
async def health():
    now = datetime.now(timezone.utc)
    secs = int((next_run_at - now).total_seconds()) if next_run_at and next_run_at > now else 0
    return {
        "status":               "ok",
        "timestamp":            now.isoformat(),
        "scan_interval_minutes": DISCOVERY_INTERVAL,
        "next_scan_in_seconds": secs,
        "next_scan_at":         next_run_at.isoformat() if next_run_at else None,
        "total_scans":          run_count,
        "instances_running":    last_result.get("running", 0),
        "instances_total":      last_result.get("total", 0),
    }


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/status")
async def status():
    return last_result or {"message": "First scan in progress..."}


@app.get("/instances")
async def list_instances():
    return {
        "total":    len(all_instances),
        "running":  sum(1 for i in all_instances if i.is_running),
        "stopped":  sum(1 for i in all_instances if not i.is_running),
        "instances": [
            {
                "name":       i.display_name,
                "id":         i.instance_id,
                "type":       i.instance_type,
                "state":      i.state,
                "region":     i.region,
                "public_ip":  i.public_ip,
                "private_ip": i.private_ip,
                "os":         i.platform,
            }
            for i in all_instances
        ]
    }


@app.get("/schedule")
async def schedule():
    now  = datetime.now(timezone.utc)
    secs = int((next_run_at - now).total_seconds()) if next_run_at and next_run_at > now else 0
    return {
        "interval_minutes":      DISCOVERY_INTERVAL,
        "next_scan_at":          next_run_at.isoformat() if next_run_at else None,
        "next_scan_in_seconds":  secs,
        "next_scan_in_human":    f"{secs // 60}m {secs % 60}s",
        "total_scans_completed": run_count,
        "scheduler_running":     scheduler.running,
    }


@app.post("/discover")
async def trigger_now():
    asyncio.create_task(run_sync_cycle())
    return {"message": "Sync triggered — check /status in 10 seconds"}


@app.get("/debug")
async def debug():
    """Diagnose AWS connectivity and show what's in the targets file."""
    import os, boto3

    result = {
        "env": {
            "AWS_DEFAULT_REGION": os.getenv("AWS_DEFAULT_REGION", "NOT SET"),
            "AWS_REGIONS":        os.getenv("AWS_REGIONS", "NOT SET"),
            "TARGETS_DIR":        os.getenv("TARGETS_DIR", "NOT SET"),
            "CW_CONFIG_FILE":     os.getenv("CW_CONFIG_FILE", "NOT SET"),
            "TAG_FILTER_KEY":     os.getenv("TAG_FILTER_KEY", "(blank = all instances)"),
        },
        "aws_identity":    None,
        "aws_error":       None,
        "instances_found": [],
    }

    try:
        sts = boto3.client("sts", region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
        identity = sts.get_caller_identity()
        result["aws_identity"] = {
            "account": identity["Account"],
            "arn":     identity["Arn"],
        }
    except Exception as e:
        result["aws_error"] = str(e)

    try:
        region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
        ec2    = boto3.client("ec2", region_name=region)
        resp   = ec2.describe_instances()
        for res in resp["Reservations"]:
            for inst in res["Instances"]:
                tags = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
                result["instances_found"].append({
                    "id":         inst["InstanceId"],
                    "name":       tags.get("Name", inst["InstanceId"]),
                    "state":      inst["State"]["Name"],
                    "type":       inst["InstanceType"],
                    "private_ip": inst.get("PrivateIpAddress", ""),
                    "public_ip":  inst.get("PublicIpAddress", ""),
                })
    except Exception as e:
        result["aws_error"] = (result.get("aws_error") or "") + f" EC2 error: {e}"

    return result
