"""Core data models: lots, buy boxes, evaluations."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class Lot(BaseModel):
    """A normalized auction lot, regardless of source (Copart, IAA, ...)."""

    source: str                      # "copart" | "iaa" | "sample" ...
    lot_id: str                      # source-specific lot / stock number
    url: Optional[str] = None
    year: Optional[int] = None
    make: Optional[str] = None
    model: Optional[str] = None
    trim: Optional[str] = None
    vin: Optional[str] = None
    odometer: Optional[int] = None
    odometer_status: Optional[str] = None   # "actual", "not actual", "exempt"
    primary_damage: Optional[str] = None
    secondary_damage: Optional[str] = None
    title_type: Optional[str] = None        # "clean", "salvage", "rebuilt", ...
    condition: Optional[str] = None         # "Run and Drive", "Starts", "Stationary"
    has_keys: Optional[bool] = None
    location: Optional[str] = None
    zip_code: Optional[str] = None
    auction_date: Optional[datetime] = None
    current_bid: Optional[float] = None
    buy_now: Optional[float] = None
    est_retail_value: Optional[float] = None  # ACV / ERV published by the site
    description: str = ""                     # free-text notes / highlights
    images: list[str] = Field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.lot_id}"

    @property
    def title_line(self) -> str:
        parts = [str(self.year or ""), self.make or "", self.model or "", self.trim or ""]
        return " ".join(p for p in parts if p).strip() or f"Lot {self.lot_id}"


class BuyBox(BaseModel):
    """What the user is looking for. Hard filters run first, for free;
    `must_haves` / `nice_to_haves` / `deal_breakers` are plain-English rules
    the LLM turns into yes/no questions per lot."""

    name: str = "default"
    year_min: Optional[int] = None
    year_max: Optional[int] = None
    max_miles: Optional[int] = None
    makes: list[str] = Field(default_factory=list)          # empty = any
    models: list[str] = Field(default_factory=list)         # empty = any
    title_types: list[str] = Field(default_factory=list)    # empty = any
    conditions: list[str] = Field(default_factory=list)     # e.g. ["run and drive"]
    excluded_damage: list[str] = Field(default_factory=list)  # e.g. ["flood", "fire"]
    require_keys: bool = False
    locations: list[str] = Field(default_factory=list)      # state codes / yard names

    must_haves: list[str] = Field(default_factory=list)
    nice_to_haves: list[str] = Field(default_factory=list)
    deal_breakers: list[str] = Field(default_factory=list)

    # Pricing assumptions for the max-bid calculation
    target_profit: float = 2500.0
    transport_cost: float = 500.0
    misc_costs: float = 300.0        # registration, inspection, detailing
    max_budget: Optional[float] = None
    auto_watch_score: int = 70       # auto-add to watchlist at/above this score


# ---- LLM evaluation output (structured) -------------------------------

class BuyBoxAnswer(BaseModel):
    question: str = Field(description="The buy-box rule rephrased as a yes/no question about this lot")
    answer: Literal["yes", "no", "unknown"]
    evidence: str = Field(description="Quote or field from the listing that supports the answer, or why it is unknown")
    kind: Literal["must_have", "nice_to_have", "deal_breaker"]


class LotEvaluation(BaseModel):
    answers: list[BuyBoxAnswer]
    risk_flags: list[str] = Field(description="Concerns a buyer should check before bidding")
    est_retail_value: float = Field(description="Estimated retail value once repaired, USD")
    repair_low: float = Field(description="Low end of estimated repair cost, USD")
    repair_high: float = Field(description="High end of estimated repair cost, USD")
    score: int = Field(description="0-100 fit with the buy box and deal quality")
    summary: str = Field(description="Two or three sentences a buyer can skim")


class Decision(BaseModel):
    lot_key: str
    passed_filters: bool
    filter_reasons: list[str] = Field(default_factory=list)
    evaluation: Optional[LotEvaluation] = None
    max_bid: Optional[float] = None
    max_bid_breakdown: dict[str, float] = Field(default_factory=dict)
