from pydantic import BaseModel, Field
from typing import List, Literal, Optional

class TradeRecommendation(BaseModel):
    ticker: str
    action: Literal["BUY"]  # long-only for now
    shares: int = Field(..., gt=0)
    entry: float = Field(..., gt=0)
    stop: float = Field(..., gt=0)
    target: Optional[float] = None
    confidence: int = Field(..., ge=0, le=100)
    reason: str

class GrokDecision(BaseModel):
    trades: List[TradeRecommendation] = Field(default_factory=list)
