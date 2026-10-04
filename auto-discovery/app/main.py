"""
EC2 Auto-Discovery Agent — Multi-Account / Multi-Region
────────────────────────────────────────────────────────
Automatically discovers ALL AWS services across ALL accounts
and ALL regions, then updates Prometheus targets.

Supported: EC2, ECS, Lambda, RDS, ALB, API Gateway, ASG, ElastiCache, SQS

API:
  GET  /health       — liveness
  GET  /metrics      — Prometheus self-metrics
  GET  /status       — last discovery summary
  GET  /services     — all discovered services
  GET  /accounts     — accounts being scanned
  POST /discover     — trigger immediate scan
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

from .multi_account import MultiAccountDiscovery

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s"
)
logger = logging.getLogger(__name__)

DISCOVERY_INTERVAL = int(os.getenv("DISCOVERY_INTERVAL_MINUTES", "2"))

# ── Prometheus self-metrics ───────────────────────────────────────────
services_total      = Gauge("autodiscovery_services_total",     "Total AWS services discovered", ["service_type"])
accounts_total      = Gauge("autodiscovery_accounts_total",     "Total AWS accounts scanned")
regions_total       = Gauge("autodiscovery_regions_total",      "Total AWS regions scanned")
ec2_monitored       = Gauge("autodiscovery_instances_monitored","EC2 instances with Node Exporter UP")
ec2_missing         = Gauge("autodiscovery_instances_missing",  "EC2 instances without Node Exporter")
ec2_discovered      = Gauge("autodiscovery_instances_total",    "Total EC2 instances discovered")
discovery_runs      = Counter("autodiscovery_runs_total",       "Total discovery cycles")
discovery_errors    = Counter("autodiscovery_errors_total",     "Total discovery errors")
last_discovery_ts   = Gauge("autodiscovery_last_run_timestamp", "Unix ts of last run")

app = FastAPI(
    title="AWS Multi-Account Auto-Discovery Agent",
    description="Discovers all AWS services across all accounts and regions",
    version="2.0.0"
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

discovery   = MultiAccountDiscovery()
scheduler   = AsyncIOScheduler()
last_result: dict = {}


async def run_discovery_cycle():
    global last_result
    try:
        logger.info("Starting discovery cycle...")
        result = await discovery.run()
        last_result = result

        # Update metrics
        by_type = result.get("by_type", {})
        for svc_type, count in by_type.items():
            services_total.labels(service_type=svc_type).set(count)

        accounts_total.set(result.get("accounts", 0))
        regions_total.set(result.get("regions_scanned", 0))

        ec2_count = by_type.get("ec2", 0)
        ec2_discovered.set(ec2_count)

        # Count reachable EC2s
        reachable = sum(1 for s in discovery.discovered
                        if s.service_type == "ec2" and s.reachable)
        ec2_monitored.set(reachable)
        ec2_missing.set(ec2_count - reachable)

        discovery_runs.inc()
        last_discovery_ts.set(datetime.utcnow().timestamp())

        if result.get("new_services"):
            logger.info("🆕 New: %s", [s["name"] for s in result["new_services"]])
        if result.get("removed_services"):
            logger.info("🗑️  Removed: %s", [s["name"] for s in result["removed_services"]])

    except Exception as e:
        discovery_errors.inc()
        logger.error("Discovery cycle failed: %s", e, exc_info=True)


@app.on_event("startup")
async def startup():
    asyncio.create_task(run_discovery_cycle())
    scheduler.add_job(
        run_discovery_cycle, "interval",
        minutes=DISCOVERY_INTERVAL,
        id="aws-discovery",
        max_instances=1,
        coalesce=True
    )
    scheduler.start()
    logger.info("=" * 60)
    logger.info("AWS Multi-Account Auto-Discovery Agent started ✓")
    logger.info("Interval   : every %d minutes", DISCOVERY_INTERVAL)
    logger.info("Regions    : %s", os.getenv("AWS_REGIONS", "us-east-1"))
    logger.info("Accounts   : %s", os.getenv("AWS_ACCOUNTS", "[] (primary only)"))


@app.on_event("shutdown")
async def shutdown():
    scheduler.shutdown(wait=False)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "interval_minutes": DISCOVERY_INTERVAL,
        "accounts": last_result.get("accounts", 0),
        "total_services": last_result.get("total", 0),
    }


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/discover")
async def trigger_discovery():
    asyncio.create_task(run_discovery_cycle())
    return {"message": "Discovery triggered", "timestamp": datetime.utcnow().isoformat()}


@app.get("/status")
async def status():
    return last_result or {"message": "No discovery run yet", "timestamp": datetime.utcnow().isoformat()}


@app.get("/accounts")
async def list_accounts():
    return {
        "accounts": [
            {"id": a.account_id, "name": a.account_name, "regions": a.regions}
            for a in discovery.accounts
        ]
    }


@app.get("/services")
async def list_services(service_type: str = None, account: str = None, region: str = None):
    svcs = discovery.discovered
    if service_type:
        svcs = [s for s in svcs if s.service_type == service_type]
    if account:
        svcs = [s for s in svcs if s.account_name == account or s.account_id == account]
    if region:
        svcs = [s for s in svcs if s.region == region]

    return {
        "total": len(svcs),
        "services": [
            {
                "account":      s.account_name,
                "account_id":   s.account_id,
                "region":       s.region,
                "type":         s.service_type,
                "id":           s.resource_id,
                "name":         s.resource_name,
                "reachable":    s.reachable,
                "metadata":     s.metadata,
                "tags":         s.tags,
            }
            for s in svcs
        ]
    }


@app.get("/services/{service_type}")
async def get_services_by_type(service_type: str):
    svcs = [s for s in discovery.discovered if s.service_type == service_type]
    return {"type": service_type, "total": len(svcs), "services": [
        {"account": s.account_name, "region": s.region, "id": s.resource_id,
         "name": s.resource_name, "metadata": s.metadata}
        for s in svcs
    ]}
