"""
Notification channels — Slack, Email (future).
"""
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self, slack_webhook: Optional[str] = None):
        self.slack_webhook = slack_webhook

    async def send_slack(self, message: str, channel: Optional[str] = None) -> bool:
        """Send a message to Slack via webhook."""
        if not self.slack_webhook:
            logger.debug("Slack webhook not configured, skipping notification")
            return False

        payload: dict = {"text": message}
        if channel:
            payload["channel"] = channel

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(self.slack_webhook, json=payload)
                resp.raise_for_status()
                return True
        except Exception as e:
            logger.error("Slack notification failed: %s", e)
            return False
