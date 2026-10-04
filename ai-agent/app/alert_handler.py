"""
Alert Handler — investigates alerts and generates reports.
"""
import logging
from typing import Dict

from .ai_engine import AIEngine
from .models import AlertInvestigationRequest
from .prometheus_client import PrometheusClient
from .notifier import Notifier

logger = logging.getLogger(__name__)

# Queries to pull for context when investigating each alert type
ALERT_CONTEXT_QUERIES = {
    "HighCPUUsage": [
        "100 - (avg by(instance) (irate(node_cpu_seconds_total{mode='idle'}[5m])) * 100)",
        "node_load15",
    ],
    "HighMemoryUsage": [
        "(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100",
        "node_memory_MemTotal_bytes",
    ],
    "DiskSpaceCritical": [
        "(1 - (node_filesystem_avail_bytes / node_filesystem_size_bytes)) * 100",
    ],
    "WebsiteDown": [
        "probe_success{job='blackbox-http'}",
        "probe_duration_seconds{job='blackbox-http'}",
    ],
    "SSLCertExpiringCritical": [
        "(ssl_cert_not_after - time()) / 86400",
    ],
    "ECSServiceHighCPU": [
        "aws_ecs_cpuutilization_average",
        "aws_ecs_running_task_count_average",
    ],
    "LambdaHighErrorRate": [
        "rate(aws_lambda_errors_sum[5m])",
        "rate(aws_lambda_invocations_sum[5m])",
        "aws_lambda_throttles_sum",
    ],
    "APIGatewayHighError5xx": [
        "rate(aws_apigateway_5xxerror_sum[5m])",
        "rate(aws_apigateway_count_sum[5m])",
        "aws_apigateway_latency_p99",
    ],
}

DEFAULT_CONTEXT_QUERIES = [
    "up",
    "100 - (avg(irate(node_cpu_seconds_total{mode='idle'}[5m])) * 100)",
]


class AlertHandler:
    def __init__(
        self,
        prom: PrometheusClient,
        ai: AIEngine,
        notifier: Notifier,
    ):
        self.prom = prom
        self.ai = ai
        self.notifier = notifier

    async def investigate(self, req: AlertInvestigationRequest) -> Dict:
        """Run full investigation for an alert."""
        logger.info("Investigating alert: %s", req.alert_name)

        # Pull related metrics
        queries = ALERT_CONTEXT_QUERIES.get(req.alert_name, DEFAULT_CONTEXT_QUERIES)
        related_metrics = {}
        for q in queries:
            try:
                result = await self.prom.get_metric_history(q, hours=1)
                related_metrics[q] = result.get("data", {}).get("result", [])
            except Exception as e:
                logger.warning("Failed to query %s: %s", q, e)
                related_metrics[q] = []

        # Get current infra context
        current_context = await self.prom.get_current_context()

        # Run AI analysis
        analysis = await self.ai.investigate_alert(
            alert_name=req.alert_name,
            labels=req.labels,
            annotations=req.annotations,
            related_metrics=related_metrics,
            current_context=current_context,
        )

        return {
            "alert_name": req.alert_name,
            "labels": req.labels,
            "analysis": analysis,
            "related_metrics_queried": list(related_metrics.keys()),
        }

    async def auto_investigate_and_notify(self, alert: Dict):
        """
        Called automatically from the Alertmanager webhook.
        Investigates the alert and posts result to Slack.
        """
        alert_name = alert.get("labels", {}).get("alertname", "Unknown")
        severity = alert.get("labels", {}).get("severity", "unknown")
        instance = alert.get("labels", {}).get("instance", "")

        logger.info("Auto-investigating: %s [%s]", alert_name, severity)

        try:
            from .models import AlertInvestigationRequest
            req = AlertInvestigationRequest(
                alert_name=alert_name,
                labels=alert.get("labels", {}),
                annotations=alert.get("annotations", {}),
                starts_at=alert.get("startsAt"),
                generator_url=alert.get("generatorURL"),
            )
            result = await self.investigate(req)

            color = "attention" if severity == "critical" else "warning"
            title = f"🤖 AI Alert Analysis: {alert_name}"
            body  = f"**Instance:** {instance}\n\n{result['analysis']}"
            await self.notifier.send_teams(body, title=title, color=color)
        except Exception as e:
            logger.error("Auto-investigation failed for %s: %s", alert_name, e)

    async def generate_summary(self, period_hours: int = 24) -> str:
        """Generate infrastructure health summary."""
        context = await self.prom.get_current_context()
        alerts = await self.prom.get_firing_alerts()
        return await self.ai.generate_infra_summary(context, alerts, period_hours)
