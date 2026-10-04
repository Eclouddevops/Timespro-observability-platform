"""
Prometheus HTTP API client
"""
import logging
from typing import Any, Dict, List

import httpx

logger = logging.getLogger(__name__)


class PrometheusClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def query(self, promql: str) -> Dict[str, Any]:
        """Instant query."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.base_url}/api/v1/query",
                params={"query": promql},
            )
            resp.raise_for_status()
            return resp.json()

    async def query_range(
        self,
        promql: str,
        start: str,
        end: str,
        step: str = "60s",
    ) -> Dict[str, Any]:
        """Range query."""
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                f"{self.base_url}/api/v1/query_range",
                params={"query": promql, "start": start, "end": end, "step": step},
            )
            resp.raise_for_status()
            return resp.json()

    async def get_firing_alerts(self) -> List[Dict]:
        """Fetch all currently firing alerts."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{self.base_url}/api/v1/alerts")
            resp.raise_for_status()
            data = resp.json()
            return [
                a for a in data.get("data", {}).get("alerts", [])
                if a.get("state") == "firing"
            ]

    async def get_current_context(self) -> Dict[str, Any]:
        """
        Pull a snapshot of key metrics to give the AI context
        about the current state of the infrastructure.
        """
        queries = {
            "cpu_usage": "100 - (avg(irate(node_cpu_seconds_total{mode='idle'}[5m])) * 100)",
            "memory_usage": "(1 - (sum(node_memory_MemAvailable_bytes) / sum(node_memory_MemTotal_bytes))) * 100",
            "services_up": "count(up == 1)",
            "services_down": "count(up == 0) or vector(0)",
            "ssl_expiring_soon": "count((ssl_cert_not_after - time()) < 86400 * 30) or vector(0)",
            "websites_down": "count(probe_success{job='blackbox-http'} == 0) or vector(0)",
            "firing_alerts": "count(ALERTS{alertstate='firing'}) or vector(0)",
        }
        context = {}
        for name, q in queries.items():
            try:
                result = await self.query(q)
                results = result.get("data", {}).get("result", [])
                if results:
                    context[name] = float(results[0]["value"][1])
                else:
                    context[name] = 0.0
            except Exception as e:
                logger.warning("Context query %s failed: %s", name, e)
                context[name] = None
        return context

    async def get_metric_history(self, promql: str, hours: int = 24) -> Dict:
        """Get metric history for a given period."""
        import time
        end = int(time.time())
        start = end - hours * 3600
        return await self.query_range(
            promql,
            start=str(start),
            end=str(end),
            step="5m",
        )
