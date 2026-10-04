"""
AI Engine — supports OpenAI (GPT-4o) and Anthropic (Claude 3.5 Sonnet).
Falls back gracefully if keys are missing.
"""
import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert Site Reliability Engineer (SRE) and AWS infrastructure specialist.
You have access to real-time Prometheus metrics from the infrastructure you are monitoring.

Your responsibilities:
- Analyze metric data and identify anomalies, trends, and root causes
- Provide concise, actionable recommendations
- Prioritize issues by severity
- Explain technical concepts in plain language when needed
- Format responses with clear sections: Summary, Analysis, Root Cause (if applicable), Recommendations

Current infrastructure being monitored:
- EC2 instances (with Node Exporter)
- ECS clusters and services
- Lambda functions
- API Gateway (REST & HTTP APIs)
- Auto Scaling Groups
- WAF v2 policies
- Website/URL endpoints (Blackbox probe)
- SSL certificates (including wildcards)
- CloudWatch metrics (via CloudWatch Exporter)

Always be specific, reference metric values when available, and include severity (INFO/WARNING/CRITICAL).
"""


class AIEngine:
    def __init__(self, openai_api_key: Optional[str], anthropic_api_key: Optional[str]):
        self.openai_client = None
        self.anthropic_client = None

        if openai_api_key:
            try:
                from openai import AsyncOpenAI
                self.openai_client = AsyncOpenAI(api_key=openai_api_key)
                logger.info("OpenAI client initialized")
            except ImportError:
                logger.warning("openai package not installed")

        if anthropic_api_key:
            try:
                import anthropic
                self.anthropic_client = anthropic.AsyncAnthropic(api_key=anthropic_api_key)
                logger.info("Anthropic client initialized")
            except ImportError:
                logger.warning("anthropic package not installed")

    def _preferred_client(self) -> str:
        if self.anthropic_client:
            return "anthropic"
        if self.openai_client:
            return "openai"
        return "none"

    async def _call_llm(self, messages: List[Dict], system: str = SYSTEM_PROMPT) -> str:
        """Call the best available LLM."""
        client = self._preferred_client()

        if client == "anthropic":
            response = await self.anthropic_client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=2048,
                system=system,
                messages=messages,
            )
            return response.content[0].text

        elif client == "openai":
            all_messages = [{"role": "system", "content": system}] + messages
            response = await self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=all_messages,
                max_tokens=2048,
                temperature=0.3,
            )
            return response.choices[0].message.content

        else:
            return (
                "⚠️ No AI provider configured. "
                "Set OPENAI_API_KEY or ANTHROPIC_API_KEY to enable AI analysis."
            )

    async def analyze_metrics(
        self,
        query: str,
        data: Dict[str, Any],
        context: Optional[str] = None,
    ) -> str:
        """Analyze raw Prometheus metric results."""
        results_str = json.dumps(data.get("data", {}).get("result", []), indent=2)
        user_content = f"""Analyze these Prometheus metric results:

**Query:** `{query}`
{f'**Context:** {context}' if context else ''}

**Results:**
```json
{results_str[:3000]}
```

Provide a concise analysis including: current state, any concerns, and recommendations."""

        return await self._call_llm([{"role": "user", "content": user_content}])

    async def investigate_alert(
        self,
        alert_name: str,
        labels: Dict,
        annotations: Dict,
        related_metrics: Dict[str, Any],
        current_context: Dict[str, Any],
    ) -> str:
        """Perform root-cause analysis for a firing alert."""
        content = f"""A Prometheus alert has fired. Investigate and provide root-cause analysis.

**Alert Name:** {alert_name}
**Labels:** {json.dumps(labels, indent=2)}
**Annotations:** {json.dumps(annotations, indent=2)}

**Current Infrastructure Context:**
{json.dumps(current_context, indent=2)}

**Related Metrics (last 1h):**
{json.dumps(related_metrics, indent=2)[:4000]}

Provide:
1. **Summary** — What is happening in one sentence
2. **Severity Assessment** — Is this CRITICAL/WARNING/INFO and why
3. **Root Cause Analysis** — Most likely cause(s)
4. **Immediate Actions** — What to do right now (numbered list)
5. **Long-term Recommendations** — How to prevent recurrence"""

        return await self._call_llm([{"role": "user", "content": content}])

    async def generate_infra_summary(
        self,
        context: Dict[str, Any],
        alerts: List[Dict],
        period_hours: int = 24,
    ) -> str:
        """Generate a human-readable infrastructure health summary."""
        alerts_str = json.dumps(alerts[:10], indent=2) if alerts else "None"
        content = f"""Generate a {period_hours}-hour infrastructure health report.

**Current State:**
- CPU Usage: {context.get('cpu_usage', 'N/A')}%
- Memory Usage: {context.get('memory_usage', 'N/A')}%
- Services Up: {context.get('services_up', 'N/A')}
- Services Down: {context.get('services_down', 'N/A')}
- SSL Certs Expiring < 30 days: {context.get('ssl_expiring_soon', 'N/A')}
- Websites Down: {context.get('websites_down', 'N/A')}
- Active Alerts: {context.get('firing_alerts', 'N/A')}

**Firing Alerts:**
```json
{alerts_str}
```

Generate a Slack-formatted report with:
1. 🟢/🟡/🔴 Overall Health Status
2. Key metrics summary
3. Active issues (if any)
4. SSL cert warnings
5. Top recommendations"""

        return await self._call_llm([{"role": "user", "content": content}])

    async def chat(
        self,
        message: str,
        history: List[Dict],
        metrics_context: Dict[str, Any],
    ) -> str:
        """Conversational interface with infra context injected."""
        context_str = json.dumps(metrics_context, indent=2)
        system = SYSTEM_PROMPT + f"\n\nCurrent infrastructure snapshot:\n```json\n{context_str}\n```"

        messages = [
            {"role": m["role"], "content": m["content"]}
            for m in history[-10:]  # Last 10 messages
        ]
        messages.append({"role": "user", "content": message})

        return await self._call_llm(messages, system=system)
