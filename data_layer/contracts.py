"""Общие DTO слоя данных. Форматы строгие: слой общается только с агентом (worker),
свободный текст и UI-форматы сюда не попадают."""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


# --- перечисления ((str, Enum) — совместимо с Python 3.10+) ---

class DataMode(str, Enum):
    LIVE = "live"
    SANDBOX = "sandbox"
    SYNTHETIC = "synthetic"


class Freshness(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class Readiness(str, Enum):
    READY = "ready"
    PRELIMINARY = "preliminary"
    BLOCKED = "blocked"


class TripComponent(str, Enum):
    HOTEL = "hotel"
    TRANSPORT_OUT = "transport_out"
    TRANSPORT_BACK = "transport_back"
    TRANSFER = "transfer"


class CoverageLevel(str, Enum):
    FULL = "full"
    PARTIAL = "partial"
    SPARSE = "sparse"
    UNSUPPORTED = "unsupported"


class SearchStatus(str, Enum):
    OK = "ok"
    EMPTY = "empty"
    INSUFFICIENT_COVERAGE = "insufficient_coverage"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    UNSUPPORTED = "unsupported"
    INVALID_RESPONSE = "invalid_response"


# --- общие объекты ---

class Pets(BaseModel):
    type: str = "unknown"
    size: str = "unknown"


class Guests(BaseModel):
    adults: int = Field(ge=0)
    children_ages: list[int] = Field(default_factory=list)
    pets: list[Pets] = Field(default_factory=list)

    @property
    def total_people(self) -> int:
        return self.adults + len(self.children_ages)


class Budget(BaseModel):
    max_minor: int | None = Field(default=None, ge=0)
    currency: str = "RUB"
    scope: Literal["entire_trip_all_guests", "per_person"] = "entire_trip_all_guests"


class Price(BaseModel):
    amount_minor: int = Field(ge=0)
    currency: str = "RUB"


# --- запросы (типизированы по типам поиска) ---

class SearchQueryBase(BaseModel):
    query_id: str
    budget: Budget | None = None
    required_components: list[TripComponent] = Field(default_factory=list)
    coverage_policy: Literal["warn_if_sparse", "best_effort"] = "warn_if_sparse"
    data_modes_allowed: list[DataMode] = Field(
        default_factory=lambda: [DataMode.LIVE, DataMode.SANDBOX, DataMode.SYNTHETIC]
    )
    origin: str | None = None


class HotelSearchQuery(SearchQueryBase):
    kind: Literal["hotel_search"] = "hotel_search"
    destination_ids: list[str]
    check_in: date
    check_out: date
    guests: Guests
    rooms: int = Field(default=1, ge=1)
    hard_constraints: list[str] = Field(default_factory=list)
    soft_preferences: list[str] = Field(default_factory=list)


class TransportSearchQuery(SearchQueryBase):
    kind: Literal["transport_search"] = "transport_search"
    origin_id: str
    destination_id: str
    departure_date: date
    return_date: date | None = None
    passengers: Guests
    modes: list[Literal["flight", "train", "bus"]] = Field(
        default_factory=lambda: ["flight", "train"]
    )
    baggage_kg: int | None = None
    route_constraints: list[str] = Field(default_factory=list)


class TransferSearchQuery(SearchQueryBase):
    kind: Literal["transfer_search"] = "transfer_search"
    pickup_place_id: str
    pickup_at: datetime
    dropoff_place_id: str
    passengers: Guests
    luggage: int = Field(default=0, ge=0)
    constraints: list[str] = Field(default_factory=list)


# --- оффер ---

class Terms(BaseModel):
    cancellation: Literal["free", "paid", "non_refundable", "unknown"] = "unknown"
    breakfast: Literal["included", "not_included", "unknown"] = "unknown"
    pets_allowed: bool | None = None
    children_allowed: bool | None = None
    baggage: Literal["included", "paid", "unknown"] = "unknown"
    extra: dict[str, str | None] = Field(default_factory=dict)


class Offer(BaseModel):
    id: str
    provider: str
    provider_offer_id: str
    kind: Literal["hotel", "transport", "transfer", "activity"]
    data_mode: DataMode
    cache_hit: bool = False
    freshness: Freshness = Freshness.FRESH
    readiness: Readiness = Readiness.READY
    price: Price
    price_scope: Literal["entire_stay_all_guests", "per_person", "total"]
    fees: Price | None = None
    total_with_fees: Price | None = None
    terms: Terms
    details: dict[str, Any] = Field(default_factory=dict)
    retrieved_at: datetime
    valid_until: datetime | None = None
    source_url: str | None = None
    content_hash: str
    warnings: list[str] = Field(default_factory=list)


# --- конверты ответов ---

class Coverage(BaseModel):
    destination_id: str | None = None
    level: CoverageLevel
    required_components: list[TripComponent] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    recommendation: str | None = None


class SearchResult(BaseModel):
    status: SearchStatus
    offers: list[Offer] = Field(default_factory=list)
    coverage: Coverage | None = None
    warnings: list[str] = Field(default_factory=list)
    query_fingerprint: str


class DestinationContext(BaseModel):
    destination_id: str
    name: str
    region: str | None = None
    data_mode: DataMode
    seasons: dict[str, Any] = Field(default_factory=dict)
    weather: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


# --- ревалидация ---

class RevalidateReason(str, Enum):
    PLAN_CANDIDATE_CHECK = "plan_candidate_check"
    CHECKOUT_PREVIEW_CHECK = "checkout_preview_check"


class Strictness(str, Enum):
    MEDIUM = "medium"
    STRICT = "strict"


class OfferCheckRef(BaseModel):
    offer_id: str
    content_hash: str


class RevalidateRequest(BaseModel):
    reason: RevalidateReason
    strictness: Strictness | None = None
    offers: list[OfferCheckRef]

    def effective_strictness(self) -> Strictness:
        if self.strictness is not None:
            return self.strictness
        if self.reason is RevalidateReason.CHECKOUT_PREVIEW_CHECK:
            return Strictness.STRICT
        return Strictness.MEDIUM


class ChangedOffer(BaseModel):
    offer_id: str
    old_content_hash: str
    new_content_hash: str
    changed_fields: list[str]
    previous: dict[str, Any] | None = None
    current: Offer


class RevalidateStatus(str, Enum):
    UNCHANGED = "unchanged"
    CHANGED = "changed"
    PARTIAL_UNKNOWN = "partial_unknown"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"


class RevalidateResult(BaseModel):
    status: RevalidateStatus
    changed_offers: list[ChangedOffer] = Field(default_factory=list)
    confirmed_offer_ids: list[str] = Field(default_factory=list)
    unavailable_offer_ids: list[str] = Field(default_factory=list)
    unknown_offer_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    checked_at: datetime