"""
AI Observability Agent
─────────────────────
FastAPI service providing LLM-powered infrastructure analysis.

Endpoints:
  GET  /health                — liveness probe
  GET  /metrics               — Prometheus scrape endpoint
  POST /analyze               — analyze a PromQL query with AI
  POST /investigate-alert     — root-cause analysis for a firing alert
  POST /summarize             — infrastructure health summary
  GET  /daily-report          — trigger a daily report
  POST /chat                  — conversational interface
  POST /alertmanager-webhook  — receive alerts from Alertmanager
"""

import asyncio
import logging
import os
from datetime import datetime

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
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

# ── Logging ───────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# ── Prometheus self-metrics ───────────────────────────────────────────
ai_requests_total = Counter(
    "ai_agent_requests_total",
    "Total AI agent requests",
    ["endpoint", "status"],
)
ai_latency_seconds = Gauge(
    "ai_agent_latency_seconds",
    "AI agent response latency",
    ["endpoint"],
)
active_alerts_gauge = Gauge(
    "ai_agent_active_alerts",
    "Currently tracked active alerts",
)

# ── App ───────────────────────────────────────────────────────────────
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

# ── Services (initialised at module load — safe, no network calls yet) ─
prom = PrometheusClient(
    os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
)
ai = AIEngine(
    openai_api_key=os.getenv("OPENAI_API_KEY"),
    anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
)
notifier = Notifier(slack_webhook=os.getenv("SLACK_WEBHOOK_URL"))
alert_handler = AlertHandler(prom, ai, notifier)
scheduler = AsyncIOScheduler()


# ── Lifecycle ─────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    try:
        scheduler.start()
        logger.info("AI Observability Agent started ✓")
        logger.info("OpenAI  : %s", "configured" if os.getenv("OPENAI_API_KEY") else "not configured")
        logger.info("Anthropic: %s", "configured" if os.getenv("ANTHROPIC_API_KEY") else "not configured")
        logger.info("Slack   : %s", "configured" if os.getenv("SLACK_WEBHOOK_URL") else "not configured")
    except Exception as e:
        logger.error("Startup error: %s", e)


@app.on_event("shutdown")
async def shutdown():
    try:
        scheduler.shutdown(wait=False)
    except Exception:
        pass


# ── Routes ────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "ai_provider": (
            "anthropic" if os.getenv("ANTHROPIC_API_KEY")
            else "openai" if os.getenv("OPENAI_API_KEY")
            else "none — set OPENAI_API_KEY or ANTHROPIC_API_KEY"
        ),
    }


@app.get("/metrics")
async def metrics():
    """Prometheus scrape endpoint."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/analyze")
async def analyze(req: AnalyzeRequest):
    """Run a PromQL query and return AI-generated analysis."""
    try:
        start = asyncio.get_event_loop().time()
        data = await prom.query(req.query)
        analysis = await ai.analyze_metrics(
            query=req.query,
            data=data,
            context=req.context,
        )
        ai_latency_seconds.labels("analyze").set(asyncio.get_event_loop().time() - start)
        ai_requests_total.labels("analyze", "success").inc()
        return {"query": req.query, "data": data, "analysis": analysis}
    except Exception as e:
        ai_requests_total.labels("analyze", "error").inc()
        logger.error("analyze error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/investigate-alert")
async def investigate_alert(req: AlertInvestigationRequest):
    """Root-cause analysis for a firing alert."""
    try:
        result = await alert_handler.investigate(req)
        ai_requests_total.labels("investigate", "success").inc()
        return result
    except Exception as e:
        ai_requests_total.labels("investigate", "error").inc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/summarize")
async def summarize(req: SummarizeRequest):
    """Generate infrastructure health summary."""
    try:
        report = await alert_handler.generate_summary(req.period_hours)
        ai_requests_total.labels("summarize", "success").inc()
        return {"report": report, "generated_at": datetime.utcnow().isoformat()}
    except Exception as e:
        ai_requests_total.labels("summarize", "error").inc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/daily-report")
async def daily_report():
    """Trigger a fresh daily report."""
    report = await alert_handler.generate_summary(period_hours=24)
    await notifier.send_slack(f"📊 *Daily Infra Report*\n{report}")
    return {"report": report}


@app.post("/chat")
async def chat(req: ChatRequest):
    """Conversational interface — ask anything about your infrastructure."""
    try:
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
    """Receive alerts from Alertmanager and auto-investigate."""
    alerts = payload.get("alerts", [])
    firing = [a for a in alerts if a.get("status") == "firing"]
    active_alerts_gauge.set(len(firing))
    for alert in firing:
        asyncio.create_task(alert_handler.auto_investigate_and_notify(alert))
    return {"received": len(alerts), "firing": len(firing)}


# ── Scheduled Jobs ─────────────────────────────────────────────────────

@scheduler.scheduled_job("cron", hour=8, minute=0)
async def scheduled_daily_report():
    logger.info("Running scheduled daily report...")
    try:
        report = await alert_handler.generate_summary(period_hours=24)
        await notifier.send_slack(f"📊 *Daily Infra Report*\n{report}")
    except Exception as e:
        logger.error("Daily report failed: %s", e)


@scheduler.scheduled_job("interval", minutes=5)
async def check_ssl_expiry():
    try:
        expiring = await prom.query("(ssl_cert_not_after - time()) / 86400 < 30")
        for cert in expiring.get("data", {}).get("result", []):
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
