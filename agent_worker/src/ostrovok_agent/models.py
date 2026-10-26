from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

MAX_ID = 2**53 - 1


class WireModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    def wire(self) -> dict:
        return self.model_dump(mode="json", by_alias=True)


class Task(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat_id: StrictInt = Field(gt=0, le=MAX_ID)


class Patch(WireModel):
    op: Literal["add", "remove", "replace"]
    path: str
    value: Any = None

    def wire(self):
        result = {"op": self.op, "path": self.path}
        if self.op != "remove":
            result["value"] = self.value
        return result


class HistoryEntry(WireModel):
    id: StrictInt = Field(gt=0, le=MAX_ID)
    sender: Literal["user", "user-action", "agent"] = Field(alias="from")
    message: str | None = None
    operations: list[Patch] = Field(default_factory=list)


class ChatInfo(WireModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    name: str = Field(default="Новый чат", min_length=1, max_length=200)

    @model_validator(mode="after")
    def nonblank_name(self):
        if not self.name.strip():
            raise ValueError("chat name must not be blank")
        return self


class Dates(WireModel):
    start: str = Field(default="-", alias="from")
    end: str = Field(default="-", alias="to")


class Peoples(WireModel):
    adults: int = Field(default=0, ge=0)
    children: int = Field(default=0, ge=0)
    unknown: int = Field(default=1, ge=0)
    additional_info: list[str] = Field(default_factory=list, alias="additional-info")


class PriceRestrictions(WireModel):
    min: int = Field(default=-1, ge=-1)
    max: int = Field(default=-1, ge=-1)


class Basic(WireModel):
    departure: str = "-"
    dates: Dates = Field(default_factory=Dates)
    peoples: Peoples = Field(default_factory=Peoples)
    price_restrictions: PriceRestrictions = Field(default_factory=PriceRestrictions)
    cost_range: str = "-"
    locations_tree: dict[str, Any] = Field(default_factory=dict)


class ExtraField(WireModel):
    id: str
    type: Literal["number", "tags", "text", "boolean"]
    label: str
    value: Any

    @model_validator(mode="after")
    def matching_value(self):
        valid = {
            "number": isinstance(self.value, (int, float)) and not isinstance(self.value, bool),
            "tags": isinstance(self.value, list) and all(isinstance(x, str) for x in self.value),
            "text": isinstance(self.value, str),
            "boolean": isinstance(self.value, bool),
        }
        if not valid[self.type]:
            raise ValueError("additional value does not match type")
        return self


class ExtraGroup(WireModel):
    id: str
    title: str
    fields: list[ExtraField]


class DisplayItem(WireModel):
    label: str
    value: str


class Habitation(WireModel):
    id: int = Field(gt=0, le=MAX_ID)
    name: str
    description: str
    dates: Dates
    url: str
    price: str
    additional: list[DisplayItem] = Field(default_factory=list)


class Transport(WireModel):
    id: int = Field(gt=0, le=MAX_ID)
    name: str
    type: str
    price: str
    times: dict[str, str] = Field(default_factory=dict)
    additional: list[DisplayItem] = Field(default_factory=list)


class Variant(WireModel):
    id: int = Field(gt=0, le=MAX_ID)
    name: str
    location: str
    price: str
    dates: Dates
    completed: bool
    description: str
    urls: list[str]
    habitations: list[Habitation]
    transports: list[Transport]
    additional: list[DisplayItem] = Field(default_factory=list)


class AgentStatus(WireModel):
    status: Literal["idle", "working", "waiting_user", "completed", "error"] = "idle"
    status_message: str = Field(default="", alias="status-message")


class ChatState(WireModel):
    id: StrictInt = Field(gt=0, le=MAX_ID)
    revision: StrictInt = Field(ge=0)
    chat: ChatInfo = Field(default_factory=ChatInfo)
    history: list[HistoryEntry] = Field(default_factory=list)
    basic: Basic = Field(default_factory=Basic)
    variants: list[Variant] = Field(default_factory=list)
    additional: list[ExtraGroup] = Field(default_factory=list)
    additional_hidden: dict[str, Any] = Field(default_factory=dict)
    agent: AgentStatus = Field(default_factory=AgentStatus)

    def latest_user_id(self) -> int | None:
        return next((x.id for x in reversed(self.history) if x.sender != "agent"), None)

    def field_value(self, group: str, field: str, default=None):
        return next(
            (f.value for g in self.additional if g.id == group for f in g.fields if f.id == field),
            default,
        )


class Requirements(BaseModel):
    """Normalized search input, never a second canonical chat."""

    origin: str | None = None
    destinations: list[str] = Field(default_factory=list)
    check_in: date | None = None
    check_out: date | None = None
    adults: int | None = Field(default=None, ge=1, le=20)
    children: int = Field(default=0, ge=0, le=20)
    children_ages: list[int] = Field(default_factory=list)
    rooms: int = Field(default=1, ge=1, le=10)
    budget_max_minor: int | None = Field(default=None, ge=0)
    budget_min_minor: int | None = Field(default=None, ge=0)
    budget_per_person: bool = False
    budget_unlimited: bool = False
    pets: bool = False
    meal_plan: str | None = None
    refundable_only: bool = False
    max_connections: int | None = Field(default=None, ge=0)
    checked_baggage: bool = False
    mood: list[str] = Field(default_factory=list)
    keep_hotel: str | None = None
    exclusions: list[str] = Field(default_factory=list)
    autonomy: Literal["autonomous", "balanced", "guided"] = "balanced"


class Intent(BaseModel):
    """Only explicitly stated updates from unhandled user text; no patch from the LLM."""

    origin: str | None = None
    destinations: list[str] | None = None
    check_in: date | None = None
    check_out: date | None = None
    date_shift_days: int | None = Field(default=None, ge=-365, le=365)
    adults: int | None = Field(default=None, ge=1, le=20)
    children: int | None = Field(default=None, ge=0, le=20)
    children_ages: list[int] | None = None
    budget_rub: int | None = Field(default=None, ge=0)
    budget_per_person: bool | None = None
    pets: bool | None = None
    meal_plan: str | None = None
    refundable_only: bool | None = None
    max_connections: int | None = Field(default=None, ge=0)
    checked_baggage: bool | None = None
    mood: list[str] | None = None
    keep_hotel: str | None = None
    reinstate_destinations: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    confirmed_choice: bool = False
    clear_budget: bool = False
    cost_priority: bool | None = None
    clarification_question: str | None = None


class Guests(BaseModel):
    adults: int = Field(ge=1)
    children_ages: list[int] = Field(default_factory=list)


class Money(BaseModel):
    amount_minor: int = Field(ge=0, strict=True)
    currency: str


class Source(WireModel):
    provider: str
    data_mode: Literal["live", "sandbox", "synthetic"]
    source_url: str | None = None
    retrieved_at: datetime
    valid_until: datetime | None = None


class Place(WireModel):
    place_id: str
    name: str
    country: str = ""
    region: str = ""
    timezone: str


class Offer(WireModel):
    kind: Literal["hotel", "transport"]
    offer_id: str
    name: str
    price: Money | None = None
    price_scope: str
    mandatory_fees_included: bool | None = None
    availability: str = "unknown"
    source: Source
    guests: Guests
    # Hotel fields. Unknown facts remain null, including pet and capacity policies.
    hotel_id: str | None = None
    check_in: date | None = None
    check_out: date | None = None
    meal_plan: str = "unknown"
    cancellation: dict[str, Any] = Field(default_factory=dict)
    pets_allowed: bool | None = None
    capacity: int | None = Field(default=None, ge=1)
    check_in_time: str | None = None
    check_out_time: str | None = None
    # Transport bundles contain all outbound/return segments and one total fare.
    segments: list[dict[str, Any]] = Field(default_factory=list)
    checked_baggage: bool | None = None


class SearchResult(WireModel):
    request_id: str
    status: Literal["ok", "partial", "empty", "unsupported"]
    items: list[dict[str, Any]]
    warnings: list[str] = Field(default_factory=list)


class PlanResult(BaseModel):
    message: str
    operations: list[Patch]
    waiting: bool

    def payload(self, revision: int) -> dict:
        return {
            "message": self.message,
            "from_revision": revision,
            "operations": [op.wire() for op in self.operations],
        }
