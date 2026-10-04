"""
AI Observability Agent
─────────────────────
A FastAPI service that wraps an LLM (OpenAI/Anthropic) to provide
intelligent analysis of Prometheus metrics and Grafana dashboards.

Endpoints:
  GET  /health                 — liveness probe
  GET  /metrics                — Prometheus metrics (for scraping)
  POST /analyze                — ad-hoc analysis of a metric query
  POST /investigate-alert      — root-cause analysis for a firing alert
  POST /summarize              — daily/weekly infra health summary
  GET  /daily-report           — trigger a fresh daily report
  POST /chat                   — conversational interface
"""

import asyncio
import logging
import os
from datetime import datetime

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

from .prometheus_client import PrometheusClient
from .ai_engine import AIEngine
from .alert_handler import AlertHandler
from .models import (
    AnalyzeRequest,
    AlertInvestigationRequest,
    SummarizeRequest,
    ChatRequest,
)
from .notifier import Notifier

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Prometheus metrics for self-monitoring ──────────────────────────
ai_requests_total = Counter("ai_agent_requests_total", "Total AI agent requests", ["endpoint", "status"])
ai_latency_seconds = Gauge("ai_agent_latency_seconds", "AI agent response latency", ["endpoint"])
active_alerts_gauge = Gauge("ai_agent_active_alerts", "Currently tracked active alerts")

app = FastAPI(
    title="AI Observability Agent",
    description="Intelligent infrastructure monitoring with LLM-powered analysis",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Services ─────────────────────────────────────────────────────────
prom = PrometheusClient(os.getenv("PROMETHEUS_URL", "http://prometheus:9090"))
ai = AIEngine(
    openai_api_key=os.getenv("OPENAI_API_KEY"),
    anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
)
notifier = Notifier(slack_webhook=os.getenv("SLACK_WEBHOOK_URL"))
alert_handler = AlertHandler(prom, ai, notifier)

scheduler = AsyncIOScheduler()


# ── Routes ────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.get("/metrics")
async def metrics():
    """Prometheus scrape endpoint."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/analyze")
async def analyze(req: AnalyzeRequest):
    """
    Run a PromQL query and return an AI-generated analysis.
    """
    try:
        start = asyncio.get_event_loop().time()
        data = await prom.query(req.query)
        analysis = await ai.analyze_metrics(
            query=req.query,
            data=data,
            context=req.context,
        )
        latency = asyncio.get_event_loop().time() - start
        ai_latency_seconds.labels("analyze").set(latency)
        ai_requests_total.labels("analyze", "success").inc()
        return {"query": req.query, "data": data, "analysis": analysis}
    except Exception as e:
        ai_requests_total.labels("analyze", "error").inc()
        logger.error("analyze error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/investigate-alert")
async def investigate_alert(req: AlertInvestigationRequest):
    """
    Given a firing Alertmanager alert, perform root-cause analysis.
    """
    try:
        result = await alert_handler.investigate(req)
        ai_requests_total.labels("investigate", "success").inc()
        return result
    except Exception as e:
        ai_requests_total.labels("investigate", "error").inc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/summarize")
async def summarize(req: SummarizeRequest):
    """
    Generate a human-readable infrastructure health summary.
    """
    try:
        report = await alert_handler.generate_summary(req.period_hours)
        ai_requests_total.labels("summarize", "success").inc()
        return {"report": report, "generated_at": datetime.utcnow().isoformat()}
    except Exception as e:
        ai_requests_total.labels("summarize", "error").inc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/daily-report")
async def daily_report():
    """Trigger and return a fresh daily report."""
    report = await alert_handler.generate_summary(period_hours=24)
    await notifier.send_slack(f"📊 *Daily Infra Report*\n{report}")
    return {"report": report}


@app.post("/chat")
async def chat(req: ChatRequest):
    """
    Conversational interface — ask anything about your infrastructure.
    """
    try:
        # Pull current metric context
        context_metrics = await prom.get_current_context()
        response = await ai.chat(
            message=req.message,
            history=req.history,
            metrics_context=context_metrics,
        )
        ai_requests_total.labels("chat", "success").inc()
        return {"response": response}
    except Exception as e:
        ai_requests_total.labels("chat", "error").inc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/alertmanager-webhook")
async def alertmanager_webhook(payload: dict):
    """
    Alertmanager webhook receiver — automatically investigates
    critical alerts and posts analysis to Slack.
    """
    alerts = payload.get("alerts", [])
    active_alerts_gauge.set(len([a for a in alerts if a.get("status") == "firing"]))

    for alert in alerts:
        if alert.get("status") == "firing":
            asyncio.create_task(
                alert_handler.auto_investigate_and_notify(alert)
            )
    return {"received": len(alerts)}


# ── Scheduled Tasks ───────────────────────────────────────────────────

@scheduler.scheduled_job("cron", hour=8, minute=0)
async def scheduled_daily_report():
    """Send daily infrastructure health report every morning at 8am."""
    logger.info("Running scheduled daily report...")
    try:
        report = await alert_handler.generate_summary(period_hours=24)
        await notifier.send_slack(f"📊 *Daily Infra Report*\n{report}")
        logger.info("Daily report sent.")
    except Exception as e:
        logger.error("Daily report failed: %s", e)


@scheduler.scheduled_job("interval", minutes=5)
async def check_ssl_expiry():
    """Check SSL certificates and alert if any are expiring soon."""
    try:
        expiring = await prom.query(
            "(ssl_cert_not_after - time()) / 86400 < 30"
        )
        if expiring.get("data", {}).get("result"):
            for cert in expiring["data"]["result"]:
                domain = cert["metric"].get("instance", "unknown")
                days = float(cert["value"][1])
                if days < 7:
                    await notifier.send_slack(
                        f"🚨 *CRITICAL SSL*: `{domain}` expires in *{days:.0f} days*!"
                    )
                elif days < 30:
                    await notifier.send_slack(
                        f"⚠️ *SSL Warning*: `{domain}` expires in *{days:.0f} days*"
                    )
    except Exception as e:
        logger.warning("SSL check failed: %s", e)


@app.on_event("startup")
async def startup():
    scheduler.start()
    logger.info("AI Observability Agent started ✓")


@app.on_event("shutdown")
async def shutdown():
    scheduler.shutdown()


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8888,
        reload=False,
        log_level="info",
    )
