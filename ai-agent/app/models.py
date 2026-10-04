from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class AnalyzeRequest(BaseModel):
    query: str
    context: Optional[str] = None


class AlertInvestigationRequest(BaseModel):
    alert_name: str
    labels: Dict[str, str] = {}
    annotations: Dict[str, str] = {}
    starts_at: Optional[str] = None
    generator_url: Optional[str] = None


class SummarizeRequest(BaseModel):
    period_hours: int = 24


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    message: str
    history: List[ChatMessage] = []
