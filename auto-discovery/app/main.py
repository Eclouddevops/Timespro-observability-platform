"""
EC2 Auto-Discovery Agent — Multi-Account / Multi-Region
────────────────────────────────────────────────────────
Automatically discovers ALL AWS services across ALL accounts
and ALL regions every DISCOVERY_INTERVAL_MINUTES minutes.

Schedule:
  - Runs immediately on startup
  - Then repeats every DISCOVERY_INTERVAL_MINUTES (default: 2)
  - Each run scans ALL configured accounts and regions in parallel
  - Detects NEW instances → adds to Prometheus targets immediately
  - Detects REMOVED instances → removes from Prometheus targets
  - Hot-reloads Prometheus after every change (no restart needed)
  - Sends MS Teams notification on any change

Supported services auto-detected:
  EC2, ECS, Lambda, RDS, ALB, API Gateway, ASG, ElastiCache, SQS

API:
  GET  /health       — liveness + next scan countdown
  GET  /metrics      — Prometheus self-metrics
  GET  /status       — last discovery summary
  GET  /schedule     — scan schedule info + next run time
  GET  /services     — all discovered services
  GET  /accounts     — accounts being scanned
  POST /discover     — trigger immediate scan now
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

from .multi_account import MultiAccountDiscovery

# ── Logging ───────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s"
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────
DISCOVERY_INTERVAL = int(os.getenv("DISCOVERY_INTERVAL_MINUTES", "2"))

# ── Prometheus self-metrics ───────────────────────────────────────────
services_total    = Gauge("autodiscovery_services_total",      "Total AWS services discovered", ["service_type"])
accounts_total    = Gauge("autodiscovery_accounts_total",      "Total AWS accounts scanned")
regions_total     = Gauge("autodiscovery_regions_total",       "Total AWS regions scanned")
ec2_monitored     = Gauge("autodiscovery_instances_monitored", "EC2 instances with Node Exporter UP")
ec2_missing       = Gauge("autodiscovery_instances_missing",   "EC2 instances without Node Exporter")
ec2_discovered    = Gauge("autodiscovery_instances_total",     "Total EC2 instances discovered")
discovery_runs    = Counter("autodiscovery_runs_total",        "Total discovery cycles completed")
discovery_errors  = Counter("autodiscovery_errors_total",      "Total discovery errors")
last_discovery_ts = Gauge("autodiscovery_last_run_timestamp",  "Unix timestamp of last run")
next_discovery_ts = Gauge("autodiscovery_next_run_timestamp",  "Unix timestamp of next scheduled run")
discovery_duration= Gauge("autodiscovery_duration_seconds",    "Duration of last discovery cycle")

# ── App ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="AWS Multi-Account Auto-Discovery Agent",
    description=(
        "Automatically discovers ALL AWS services across ALL accounts and regions. "
        f"Scans every {DISCOVERY_INTERVAL} minutes."
    ),
    version="2.0.0"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

discovery    = MultiAccountDiscovery()
scheduler    = AsyncIOScheduler()
last_result: dict = {}
run_count:   int  = 0
next_run_at: datetime = None


# ── Discovery Runner ──────────────────────────────────────────────────

async def run_discovery_cycle():
    global last_result, run_count, next_run_at

    run_count += 1
    start_time = datetime.now(timezone.utc)

    # Calculate next run time
    next_run_at = start_time + timedelta(minutes=DISCOVERY_INTERVAL)
    next_discovery_ts.set(next_run_at.timestamp())

    logger.info("╔══════════════════════════════════════════════════════════╗")
    logger.info("║  🔍 AUTO-DISCOVERY SCAN #%d STARTED                     ", run_count)
    logger.info("║  ⏰ Time       : %s UTC", start_time.strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("║  🔄 Interval  : every %d minutes", DISCOVERY_INTERVAL)
    logger.info("║  ⏭️  Next scan : %s UTC", next_run_at.strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("╚══════════════════════════════════════════════════════════╝")

    try:
        result = await discovery.run()
        last_result = result

        # Calculate duration
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        discovery_duration.set(duration)

        # Update Prometheus metrics
        by_type = result.get("by_type", {})
        for svc_type, count in by_type.items():
            services_total.labels(service_type=svc_type).set(count)

        accounts_total.set(result.get("accounts", 0))
        regions_total.set(result.get("regions_scanned", 0))

        ec2_count  = by_type.get("ec2", 0)
        reachable  = sum(1 for s in discovery.discovered if s.service_type == "ec2" and s.reachable)
        ec2_discovered.set(ec2_count)
        ec2_monitored.set(reachable)
        ec2_missing.set(ec2_count - reachable)

        discovery_runs.inc()
        last_discovery_ts.set(start_time.timestamp())

        # ── Summary log ──────────────────────────────────────────────
        logger.info("╔══════════════════════════════════════════════════════════╗")
        logger.info("║  ✅ SCAN #%d COMPLETE (%.1fs)                            ", run_count, duration)
        logger.info("║  📦 Total services : %d across %d accounts / %d regions",
                    result.get("total", 0),
                    result.get("accounts", 0),
                    result.get("regions_scanned", 0))

        for svc_type, count in sorted(by_type.items()):
            icon = {
                "ec2": "🖥️ ", "ecs": "🐳", "lambda": "λ ",
                "rds": "🗄️ ", "alb": "⚖️ ", "apigateway": "🌐",
                "asg": "📈", "elasticache": "⚡", "sqs": "📨"
            }.get(svc_type, "📦")
            logger.info("║    %s %-15s : %d", icon, svc_type.upper(), count)

        if result.get("new_services"):
            logger.info("║  🆕 NEW services added:")
            for s in result["new_services"]:
                logger.info("║     + %s (%s) — %s / %s",
                            s["name"], s["type"], s["account"], s["region"])

        if result.get("removed_services"):
            logger.info("║  🗑️  Services removed:")
            for s in result["removed_services"]:
                logger.info("║     - %s (%s) — %s / %s",
                            s["name"], s["type"], s["account"], s["region"])

        logger.info("║  ⏭️  Next scan in %d minutes at %s UTC",
                    DISCOVERY_INTERVAL,
                    next_run_at.strftime("%Y-%m-%d %H:%M:%S"))
        logger.info("╚══════════════════════════════════════════════════════════╝")

    except Exception as e:
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        discovery_errors.inc()
        logger.error("╔══════════════════════════════════════════════════════════╗")
        logger.error("║  ❌ SCAN #%d FAILED after %.1fs: %s", run_count, duration, e)
        logger.error("║  ⏭️  Will retry at %s UTC",
                     next_run_at.strftime("%Y-%m-%d %H:%M:%S") if next_run_at else "unknown")
        logger.error("╚══════════════════════════════════════════════════════════╝")
        logger.error("Full error:", exc_info=True)


# ── Lifecycle ─────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    global next_run_at

    now = datetime.now(timezone.utc)
    next_run_at = now + timedelta(minutes=DISCOVERY_INTERVAL)

    logger.info("╔══════════════════════════════════════════════════════════╗")
    logger.info("║       AWS MULTI-ACCOUNT AUTO-DISCOVERY AGENT            ║")
    logger.info("╠══════════════════════════════════════════════════════════╣")
    logger.info("║  ⏰ Started     : %s UTC", now.strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("║  🔄 Interval   : every %d minutes", DISCOVERY_INTERVAL)
    logger.info("║  🌍 Regions    : %s", os.getenv("AWS_REGIONS", "us-east-1"))
    logger.info("║  🏢 Accounts   : %s",
                "primary only" if os.getenv("AWS_ACCOUNTS","[]") == "[]"
                else os.getenv("AWS_ACCOUNTS","[]")[:60])
    logger.info("║  🔍 Services   : EC2 ECS Lambda RDS ALB APIGW ASG SQS  ║")
    logger.info("║  📡 Auto-reload: Prometheus hot-reload after each scan  ║")
    logger.info("╚══════════════════════════════════════════════════════════╝")

    # Schedule recurring discovery
    scheduler.add_job(
        run_discovery_cycle,
        trigger=IntervalTrigger(minutes=DISCOVERY_INTERVAL),
        id="aws-discovery",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.start()

    # Run first scan immediately (don't wait for first interval)
    asyncio.create_task(run_discovery_cycle())
    logger.info("🚀 First scan starting now, then every %d minutes...", DISCOVERY_INTERVAL)


@app.on_event("shutdown")
async def shutdown():
    scheduler.shutdown(wait=False)
    logger.info("Auto-Discovery Agent stopped.")


# ── Routes ────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    now = datetime.now(timezone.utc)
    seconds_until_next = (
        int((next_run_at - now).total_seconds())
        if next_run_at and next_run_at > now else 0
    )
    return {
        "status":                "ok",
        "timestamp":             now.isoformat(),
        "scan_interval_minutes": DISCOVERY_INTERVAL,
        "next_scan_in_seconds":  seconds_until_next,
        "next_scan_at":          next_run_at.isoformat() if next_run_at else None,
        "total_scans_run":       run_count,
        "accounts_monitored":    last_result.get("accounts", 0),
        "total_services":        last_result.get("total", 0),
        "last_scan_at":          last_result.get("timestamp"),
    }


@app.get("/metrics")
async def metrics():
    """Prometheus scrape endpoint."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/schedule")
async def schedule():
    """Show scan schedule details."""
    now = datetime.now(timezone.utc)
    job = scheduler.get_job("aws-discovery")

    seconds_until_next = (
        int((next_run_at - now).total_seconds())
        if next_run_at and next_run_at > now else 0
    )

    return {
        "interval_minutes":      DISCOVERY_INTERVAL,
        "interval_seconds":      DISCOVERY_INTERVAL * 60,
        "next_scan_at":          next_run_at.isoformat() if next_run_at else None,
        "next_scan_in_seconds":  seconds_until_next,
        "next_scan_in_human":    f"{seconds_until_next // 60}m {seconds_until_next % 60}s",
        "total_scans_completed": run_count,
        "last_scan_at":          last_result.get("timestamp"),
        "last_scan_duration_s":  last_result.get("duration_seconds"),
        "scheduler_running":     scheduler.running,
        "job_state":             str(job.next_run_time) if job else "not scheduled",
    }


@app.post("/discover")
async def trigger_discovery():
    """Trigger an immediate discovery scan now."""
    logger.info("🔔 Manual discovery triggered via API")
    asyncio.create_task(run_discovery_cycle())
    return {
        "message":   "Discovery scan triggered — running now",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "note":      f"Regular scans continue every {DISCOVERY_INTERVAL} minutes"
    }


@app.get("/status")
async def status():
    """Return the last full discovery summary."""
    if not last_result:
        return {
            "message":   "No discovery completed yet — scan in progress",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    return last_result


@app.get("/accounts")
async def list_accounts():
    """List all AWS accounts being monitored."""
    return {
        "total":    len(discovery.accounts),
        "accounts": [
            {
                "id":      a.account_id,
                "name":    a.account_name,
                "regions": a.regions,
                "type":    "primary (IAM Instance Profile)" if not a.role_arn else f"cross-account ({a.role_arn})"
            }
            for a in discovery.accounts
        ]
    }


@app.get("/services")
async def list_services(
    service_type: str = None,
    account:      str = None,
    region:       str = None
):
    """List all discovered AWS services with optional filters."""
    svcs = discovery.discovered
    if service_type:
        svcs = [s for s in svcs if s.service_type == service_type]
    if account:
        svcs = [s for s in svcs if s.account_name == account or s.account_id == account]
    if region:
        svcs = [s for s in svcs if s.region == region]

    return {
        "total":    len(svcs),
        "filters":  {"service_type": service_type, "account": account, "region": region},
        "services": [
            {
                "account":    s.account_name,
                "account_id": s.account_id,
                "region":     s.region,
                "type":       s.service_type,
                "id":         s.resource_id,
                "name":       s.resource_name,
                "reachable":  s.reachable,
                "metadata":   s.metadata,
                "tags":       s.tags,
            }
            for s in svcs
        ]
    }


@app.get("/services/{service_type}")
async def get_by_type(service_type: str):
    """Get all discovered services of a specific type."""
    svcs = [s for s in discovery.discovered if s.service_type == service_type]
    return {
        "type":     service_type,
        "total":    len(svcs),
        "services": [
            {
                "account":  s.account_name,
                "region":   s.region,
                "id":       s.resource_id,
                "name":     s.resource_name,
                "metadata": s.metadata,
                "tags":     s.tags,
            }
            for s in svcs
        ]
    }
