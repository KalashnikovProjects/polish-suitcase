from pydantic import BaseModel, Field, field_validator
from typing import List, Any, Optional, Literal
from datetime import date

class SearchResponseEnvelope(BaseModel):
    request_id: str
    status: Literal["ok", "partial", "empty", "unsupported", "error"]
    items: List[Any] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)

class HotelSearchRequest(BaseModel):
    request_id: str
    destination_id: str
    check_in: str
    check_out: str
    guests: dict = Field(default_factory=lambda: {"adults": 1, "children_ages": [], "pets": []})
    rooms: int = 1
    currency: str = "RUB"
    limit: int = 10
    constraints: dict = Field(default_factory=dict)

    @field_validator('rooms')
    @classmethod
    def validate_rooms(cls, v):
        if not 1 <= v <= 10:
            raise ValueError("rooms must be between 1 and 10")
        return v

class TransportSearchRequest(BaseModel):
    request_id: str
    origin_id: str
    destination_id: str
    departure_date: str
    return_date: Optional[str] = None
    guests: dict = Field(default_factory=lambda: {"adults": 1, "children_ages": [], "pets": []})
    modes: List[str] = Field(default_factory=list)
    currency: str = "RUB"
    limit: int = 10
    constraints: dict = Field(default_factory=dict)
