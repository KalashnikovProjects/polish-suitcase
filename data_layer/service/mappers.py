"""Мапперы для преобразования внутренних DTO в wire-формат контракта агента."""
from data_layer.contracts import Offer, SearchResult, SearchStatus


def _normalize_place_id(pid: str | None) -> str | None:
    """Преобразует внутренние place_* в согласованные city:* для агента."""
    if pid and pid.startswith("place_"):
        return f"city:{pid.replace('place_', '')}"
    return pid


def map_offer_to_agent(offer: Offer) -> dict:
    """Преобразует внутренний Offer в формат, ожидаемый агентом."""
    name = (
        offer.details.get("hotel_name") or
        offer.details.get("route", {}).get("carrier") or
        f"Предложение {offer.kind}"
    )

    # P2: Транспорт хранит пассажиров в passengers, отели в guests
    raw_guests = offer.details.get("passengers") or offer.details.get("guests", {})

    # P3: Корректный маппинг meal_plan
    bp = offer.terms.breakfast
    meal_plan = offer.terms.extra.get("meal_plan") or ("breakfast" if bp == "included" else ("none" if bp == "not_included" else "unknown"))

    # P3: Не превращать unknown в False для багажа
    bg = offer.terms.baggage
    checked_baggage = True if bg == "included" else (False if bg == "paid" else None)

    source = {
        "provider": offer.provider,
        "data_mode": offer.data_mode.value if hasattr(offer.data_mode, "value") else str(offer.data_mode),
        "source_url": offer.source_url,
        "retrieved_at": offer.retrieved_at.isoformat() if offer.retrieved_at else None,
        "valid_until": offer.valid_until.isoformat() if offer.valid_until else None,
    }

    total = offer.total_with_fees or offer.price
    result = {
        "kind": offer.kind,
        "offer_id": offer.id,
        "content_hash": offer.content_hash,  # P4: Критически важно для ревалидации
        "name": name,
        "guests": raw_guests,
        "price": {
            "amount_minor": total.amount_minor,
            "currency": total.currency,
        } if total else None,
        "price_scope": offer.price_scope,
        "mandatory_fees_included": offer.details.get("mandatory_fees_included", True if offer.total_with_fees else None),
        "availability": "test" if offer.data_mode.value != "live" else "quoted",
        "readiness": offer.readiness.value if hasattr(offer.readiness, "value") else str(offer.readiness),
        "warnings": offer.warnings,
        "source": source,
    }

    if offer.kind == "hotel":
        result.update({
            "hotel_id": offer.details.get("hotel_id"),
            "check_in": offer.details.get("check_in"),
            "check_out": offer.details.get("check_out"),
            "meal_plan": meal_plan,
            "cancellation": {"kind": offer.terms.cancellation},
            "pets_allowed": offer.terms.pets_allowed,
            "capacity": offer.details.get("room", {}).get("capacity") or offer.details.get("capacity"),
            "check_in_time": offer.details.get("check_in_time"),
            "check_out_time": offer.details.get("check_out_time"),
        })
    elif offer.kind == "transport":
        # P2: Нормализация ID и сохранение времён в сегментах
        mapped_segments = []
        for seg in offer.details.get("segments", []):
            mapped_segments.append({
                "direction": seg.get("direction", "outbound"),
                "type": seg.get("type", "flight"),
                "origin_id": _normalize_place_id(seg.get("origin_id")),
                "destination_id": _normalize_place_id(seg.get("destination_id")),
                "departure": seg.get("departure"),
                "arrival": seg.get("arrival"),
            })
        result.update({
            "segments": mapped_segments,
            "checked_baggage": checked_baggage,
            "pets_allowed": offer.terms.pets_allowed,
        })

    return result


def map_search_result_to_agent(result: SearchResult, request_id: str) -> dict:
    """Преобразует SearchResult в ответ API с единым конвертом."""
    # P5: Корректный маппинг статусов. partial только если есть items + warnings
    status_map = {
        SearchStatus.OK: "ok",
        SearchStatus.EMPTY: "empty",
        SearchStatus.UNSUPPORTED: "unsupported",
        SearchStatus.INSUFFICIENT_COVERAGE: "unsupported",
        SearchStatus.TIMEOUT: "error",      # Будет обработано как HTTP 503 на уровне API
        SearchStatus.RATE_LIMITED: "error", # Будет обработано как HTTP 429 на уровне API
        SearchStatus.INVALID_RESPONSE: "error",
    }

    # Если статус error, но мы всё же что-то вернули, используем partial с warnings
    status = status_map.get(result.status, "error")
    if status == "error" and result.offers:
        status = "partial"
    elif status in ["empty", "unsupported"] and not result.offers:
        pass # Корректно
    elif status == "partial" and not result.offers:
        status = "unsupported" # Защита от partial с пустым items

    return {
        "request_id": request_id,
        "status": status,
        "items": [map_offer_to_agent(o) for o in result.offers],
        "warnings": result.warnings,
    }
