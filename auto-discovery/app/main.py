"""
EC2 Auto-Discovery Agent
────────────────────────
Automatically detects new/removed EC2 instances and updates
Prometheus monitoring targets without any manual intervention.

How it works:
  • Runs a discovery scan every DISCOVERY_INTERVAL_MINUTES (default: 2)
  • Checks all running EC2 instances via AWS API (IAM Instance Profile)
  • Tests if Node Exporter (port 9100) is reachable on each instance
  • Updates /etc/prometheus/targets/ec2_nodes.yml automatically
  • Hot-reloads Prometheus (no restart needed)
  • Sends MS Teams notification when instances are added/removed

API Endpoints:
  GET  /health          — liveness probe
  GET  /metrics         — Prometheus self-metrics
  GET  /instances       — list all discovered instances + status
  POST /discover        — trigger an immediate discovery scan
  GET  /status          — last discovery summary
"""

import asyncio
import logging
import os
from datetime import datetime

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response, JSONResponse

from .ec2_discovery import EC2Discovery

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s"
)
logger = logging.getLogger(__name__)

DISCOVERY_INTERVAL = int(os.getenv("DISCOVERY_INTERVAL_MINUTES", "2"))

# ── Prometheus self-metrics ───────────────────────────────────────────
instances_total     = Gauge("autodiscovery_instances_total",    "Total EC2 instances discovered")
instances_monitored = Gauge("autodiscovery_instances_monitored","EC2 instances with Node Exporter UP")
instances_missing   = Gauge("autodiscovery_instances_missing",  "EC2 instances without Node Exporter")
discovery_runs      = Counter("autodiscovery_runs_total",       "Total discovery cycles run")
discovery_errors    = Counter("autodiscovery_errors_total",     "Total discovery errors")
last_discovery_ts   = Gauge("autodiscovery_last_run_timestamp", "Unix timestamp of last discovery run")

# ── App ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="EC2 Auto-Discovery Agent",
    description="Automatically discovers EC2 instances and updates Prometheus targets",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

discovery  = EC2Discovery()
scheduler  = AsyncIOScheduler()
last_result: dict = {}


# ── Discovery runner ──────────────────────────────────────────────────

async def run_discovery_cycle():
    global last_result
    try:
        logger.info("Running EC2 discovery cycle...")
        result = await discovery.run_discovery()
        last_result = result

        # Update Prometheus metrics
        instances_total.set(result["total_discovered"])
        instances_monitored.set(result["total_monitored"])
        instances_missing.set(result["not_reachable"])
        discovery_runs.inc()
        last_discovery_ts.set(datetime.utcnow().timestamp())

        if result["newly_added"]:
            logger.info("🆕 New instances added: %s", result["newly_added"])
        if result["newly_removed"]:
            logger.info("🗑️  Instances removed: %s", result["newly_removed"])

    except Exception as e:
        discovery_errors.inc()
        logger.error("Discovery cycle failed: %s", e)


# ── Lifecycle ─────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    # Run immediately on start
    asyncio.create_task(run_discovery_cycle())

    # Then run on schedule
    scheduler.add_job(
        run_discovery_cycle,
        "interval",
        minutes=DISCOVERY_INTERVAL,
        id="ec2-discovery",
        max_instances=1,
        coalesce=True
    )
    scheduler.start()
    logger.info("EC2 Auto-Discovery Agent started ✓")
    logger.info("Discovery interval: every %d minutes", DISCOVERY_INTERVAL)
    logger.info("Regions: %s", os.getenv("AWS_REGIONS", "us-east-1"))
    logger.info("Auto-install Node Exporter: %s", os.getenv("AUTO_INSTALL_NODE_EXPORTER", "false"))


@app.on_event("shutdown")
async def shutdown():
    scheduler.shutdown(wait=False)


# ── Routes ────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "discovery_interval_minutes": DISCOVERY_INTERVAL,
        "regions": os.getenv("AWS_REGIONS", "us-east-1"),
        "total_monitored": last_result.get("total_monitored", 0)
    }


@app.get("/metrics")
async def metrics():
    """Prometheus scrape endpoint."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/discover")
async def trigger_discovery():
    """Trigger an immediate discovery scan."""
    logger.info("Manual discovery triggered via API")
    asyncio.create_task(run_discovery_cycle())
    return {
        "message": "Discovery triggered",
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/status")
async def status():
    """Return the last discovery summary."""
    return last_result or {
        "message": "No discovery run yet — running now",
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/instances")
async def list_instances():
    """List all discovered EC2 instances with their monitoring status."""
    instances = last_result.get("instances", [])
    return {
        "total":     len(instances),
        "monitored": sum(1 for i in instances if i.get("node_exporter")),
        "missing":   sum(1 for i in instances if not i.get("node_exporter")),
        "instances": instances,
        "last_scan": datetime.utcfromtimestamp(
            last_discovery_ts._value.get() or 0
        ).isoformat() if instances else None
    }


@app.get("/instances/{instance_id}")
async def get_instance(instance_id: str):
    """Get details for a specific EC2 instance."""
    instances = last_result.get("instances", [])
    for inst in instances:
        if inst["id"] == instance_id or inst["name"] == instance_id:
            return inst
    return JSONResponse(status_code=404, content={"error": f"Instance {instance_id} not found"})
