"""
Notification channels — Microsoft Teams, Email (future).

MS Teams uses Incoming Webhook with Adaptive Card format.
"""
import logging
from typing import Optional
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self, teams_webhook: Optional[str] = None):
        self.teams_webhook = teams_webhook

    async def send_teams(self, message: str, title: str = "Observability Alert", color: str = "attention") -> bool:
        """
        Send a message to MS Teams via Incoming Webhook.
        Uses Adaptive Card format for rich formatting.

        color options: good (green), warning (yellow), attention (red), accent (blue)
        """
        if not self.teams_webhook:
            logger.debug("MS Teams webhook not configured — skipping notification")
            return False

        # MS Teams Adaptive Card payload
        payload = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "body": [
                            {
                                "type": "Container",
                                "style": color,
                                "items": [
                                    {
                                        "type": "TextBlock",
                                        "text": title,
                                        "weight": "Bolder",
                                        "size": "Medium",
                                        "wrap": True,
                                        "color": "Default"
                                    }
                                ]
                            },
                            {
                                "type": "TextBlock",
                                "text": message,
                                "wrap": True,
                                "spacing": "Medium"
                            },
                            {
                                "type": "TextBlock",
                                "text": f"🕐 {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC",
                                "isSubtle": True,
                                "size": "Small"
                            }
                        ]
                    }
                }
            ]
        }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    self.teams_webhook,
                    json=payload,
                    headers={"Content-Type": "application/json"}
                )
                resp.raise_for_status()
                logger.info("MS Teams notification sent: %s", title)
                return True
        except Exception as e:
            logger.error("MS Teams notification failed: %s", e)
            return False

    async def send_alert(self, summary: str, description: str, severity: str = "warning") -> bool:
        """Send a formatted alert to MS Teams."""
        color_map = {
            "critical": "attention",
            "warning": "warning",
            "info": "accent",
            "ok": "good",
            "resolved": "good"
        }
        icon_map = {
            "critical": "🔴",
            "warning": "🟡",
            "info": "🔵",
            "ok": "🟢",
            "resolved": "✅"
        }
        color = color_map.get(severity.lower(), "warning")
        icon  = icon_map.get(severity.lower(), "⚠️")

        title = f"{icon} [{severity.upper()}] {summary}"
        return await self.send_teams(description, title=title, color=color)
