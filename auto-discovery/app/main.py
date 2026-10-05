"""
EC2 Auto-Discovery Agent v4 — Zero Touch
─────────────────────────────────────────
Every 2 minutes:
  1. Calls AWS EC2 + CloudWatch APIs directly
  2. Writes /prometheus_textfiles/ec2_metrics.prom
  3. Prometheus Node Exporter textfile_collector reads it automatically
  4. Metrics appear in Prometheus — dashboard updates

No CloudWatch Exporter needed. No Node Exporter on target instances.
No SSH. No port 9100. Just IAM role on this server.

New instances appear in dashboard within 2 minutes of launch.

API:
  GET  /health    - status + next scan
  GET  /status    - last collection result
  GET  /instances - all discovered instances
  POST /collect   - trigger immediate collection
  GET  /debug     - AWS connectivity test
"""

import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

from .aws_collector import collect_and_write

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s — %(message)s"
)
logger = logging.getLogger(__name__)

INTERVAL   = int(os.getenv("DISCOVERY_INTERVAL_MINUTES", "2"))
PROM_DIR   = os.getenv("PROM_TEXTFILE_DIR", "/prometheus_textfiles")

# ── Self metrics ────────────────────────────────────────────────────
runs_total    = Counter("autodiscovery_runs_total",          "Total collection runs")
errors_total  = Counter("autodiscovery_errors_total",        "Collection errors")
instances_g   = Gauge("autodiscovery_instances_total",       "Total EC2 instances")
running_g     = Gauge("autodiscovery_instances_running",     "Running EC2 instances")
duration_g    = Gauge("autodiscovery_duration_seconds",      "Last collection duration")
last_run_g    = Gauge("autodiscovery_last_run_timestamp",    "Last run unix timestamp")
next_run_g    = Gauge("autodiscovery_next_run_timestamp",    "Next run unix timestamp")

app = FastAPI(title="EC2 Auto-Discovery v4", version="4.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

scheduler    = AsyncIOScheduler()
last_result  = {}
run_count    = 0
next_run_at  = None


async def run_collection():
    global last_result, run_count, next_run_at

    run_count  += 1
    start       = datetime.now(timezone.utc)
    next_run_at = start + timedelta(minutes=INTERVAL)
    next_run_g.set(next_run_at.timestamp())

    logger.info("══════════════════════════════════════════════")
    logger.info("🔍 EC2 COLLECTION #%d — %s UTC", run_count, start.strftime("%H:%M:%S"))
    logger.info("   Next run: %s UTC", next_run_at.strftime("%H:%M:%S"))
    logger.info("══════════════════════════════════════════════")

    try:
        loop   = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, collect_and_write)

        duration = (datetime.now(timezone.utc) - start).total_seconds()
        result["duration_seconds"] = round(duration, 2)
        result["timestamp"]        = start.isoformat()
        result["run_number"]       = run_count
        result["next_run_at"]      = next_run_at.isoformat()
        last_result                = result

        # Update self-metrics
        runs_total.inc()
        instances_g.set(result["total"])
        running_g.set(result["running"])
        duration_g.set(duration)
        last_run_g.set(start.timestamp())

        logger.info("✅ Collection #%d done in %.1fs — %d instances (%d running)",
                    run_count, duration, result["total"], result["running"])

    except Exception as e:
        errors_total.inc()
        logger.error("❌ Collection #%d failed: %s", run_count, e, exc_info=True)
        last_result = {"error": str(e), "run_number": run_count}


@app.on_event("startup")
async def startup():
    os.makedirs(PROM_DIR, exist_ok=True)

    logger.info("╔══════════════════════════════════════════════════╗")
    logger.info("║  EC2 AUTO-DISCOVERY AGENT v4 — ZERO TOUCH       ║")
    logger.info("╠══════════════════════════════════════════════════╣")
    logger.info("║  Interval  : every %d minutes                   ║", INTERVAL)
    logger.info("║  Regions   : %-34s ║", os.getenv("AWS_REGIONS", "us-east-1"))
    logger.info("║  Output    : %s   ║", PROM_DIR)
    logger.info("║  Method    : Direct AWS API → textfile           ║")
    logger.info("╚══════════════════════════════════════════════════╝")

    # Schedule recurring collection
    scheduler.add_job(
        run_collection,
        trigger=IntervalTrigger(minutes=INTERVAL),
        id="ec2-collect",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()

    # Run immediately on startup
    asyncio.create_task(run_collection())
    logger.info("🚀 First collection starting now...")


@app.on_event("shutdown")
async def shutdown():
    scheduler.shutdown(wait=False)


@app.get("/health")
async def health():
    now  = datetime.now(timezone.utc)
    secs = int((next_run_at - now).total_seconds()) if next_run_at and next_run_at > now else 0
    prom_file = os.path.join(PROM_DIR, "ec2_metrics.prom")
    return {
        "status":               "ok",
        "timestamp":            now.isoformat(),
        "interval_minutes":     INTERVAL,
        "next_scan_in_seconds": secs,
        "next_scan_at":         next_run_at.isoformat() if next_run_at else None,
        "total_runs":           run_count,
        "instances_total":      last_result.get("total", 0),
        "instances_running":    last_result.get("running", 0),
        "textfile_exists":      os.path.exists(prom_file),
        "textfile_path":        prom_file,
    }


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/status")
async def status():
    return last_result or {"message": "First collection in progress..."}


@app.get("/instances")
async def instances():
    """Show what's in the textfile Prometheus is reading."""
    prom_file = os.path.join(PROM_DIR, "ec2_metrics.prom")
    if not os.path.exists(prom_file):
        return {"error": "No textfile yet — collection in progress"}
    with open(prom_file) as f:
        content = f.read()
    # Extract instance names from the file
    instances_found = []
    for line in content.splitlines():
        if line.startswith("ec2_instance_info{") and not line.startswith("#"):
            instances_found.append(line)
    return {
        "count":      len(instances_found),
        "instances":  instances_found,
        "file_lines": content.count("\n"),
    }


@app.post("/collect")
async def trigger():
    asyncio.create_task(run_collection())
    return {"message": "Collection triggered — check /status in 15 seconds"}


@app.get("/debug")
async def debug():
    import boto3
    result = {
        "env": {
            "AWS_DEFAULT_REGION": os.getenv("AWS_DEFAULT_REGION", "NOT SET"),
            "AWS_REGIONS":        os.getenv("AWS_REGIONS", "NOT SET"),
            "PROM_TEXTFILE_DIR":  PROM_DIR,
        },
        "textfile": None,
        "aws_identity": None,
        "aws_error":    None,
        "ec2_found":    [],
    }

    # Check textfile
    prom_file = os.path.join(PROM_DIR, "ec2_metrics.prom")
    if os.path.exists(prom_file):
        with open(prom_file) as f:
            content = f.read()
        result["textfile"] = {
            "exists": True,
            "lines": content.count("\n"),
            "preview": content[:500],
        }
    else:
        result["textfile"] = {"exists": False, "path": prom_file}

    # AWS identity
    try:
        sts = boto3.client("sts")
        identity = sts.get_caller_identity()
        result["aws_identity"] = {"account": identity["Account"], "arn": identity["Arn"]}
    except Exception as e:
        result["aws_error"] = str(e)

    # EC2 list
    try:
        ec2 = boto3.client("ec2", region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
        resp = ec2.describe_instances()
        for res in resp["Reservations"]:
            for inst in res["Instances"]:
                tags = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
                result["ec2_found"].append({
                    "id":    inst["InstanceId"],
                    "name":  tags.get("Name", inst["InstanceId"]),
                    "state": inst["State"]["Name"],
                    "type":  inst["InstanceType"],
                })
    except Exception as e:
        result["aws_error"] = (result.get("aws_error") or "") + f" | EC2: {e}"

    return result
