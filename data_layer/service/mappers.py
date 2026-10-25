"""Мапперы для преобразования внутренних DTO в wire-формат контракта агента."""
from data_layer.contracts import Offer, SearchResult, SearchStatus


def map_offer_to_agent(offer: Offer) -> dict:
    """Преобразует внутренний Offer в формат, ожидаемый агентом."""
    # Извлекаем читаемое имя
    name = (
            offer.details.get("hotel_name") or
            offer.details.get("carrier") or
            f"Предложение {offer.kind} {offer.id}"
    )

    # Формируем объект source
    source = {
        "provider": offer.provider,
        "data_mode": offer.data_mode.value,
        "source_url": offer.source_url,
        "retrieved_at": offer.retrieved_at.isoformat(),
        "valid_until": offer.valid_until.isoformat() if offer.valid_until else None,
    }

    # Базовый ответ
    result = {
        "kind": offer.kind,
        "offer_id": offer.id,
        "name": name,
        "guests": offer.details.get("guests", {}),
        "price": {
            "amount_minor": offer.price.amount_minor,
            "currency": offer.price.currency,
        } if offer.price else None,
        "price_scope": offer.price_scope,
        "mandatory_fees_included": offer.fees is None,  # Упрощение для MVP
        "availability": "test",
        "source": source,
    }

    # Добавляем специфичные поля в зависимости от типа
    if offer.kind == "hotel":
        result.update({
            "hotel_id": offer.details.get("hotel_id"),
            "check_in": offer.details.get("check_in"),
            "check_out": offer.details.get("check_out"),
            "meal_plan": offer.terms.breakfast,
            "cancellation": {"kind": offer.terms.cancellation},
            "pets_allowed": offer.terms.pets_allowed,
            "capacity": offer.details.get("room", {}).get("capacity"),
        })
    elif offer.kind == "transport":
        # P4: segments уже сформированы в synthetic.py
        result.update({
            "segments": offer.details.get("segments", []),
            "checked_baggage": offer.terms.baggage == "included",
            "pets_allowed": offer.terms.pets_allowed,
        })

    return result


def map_search_result_to_agent(result: SearchResult, request_id: str) -> dict:
    """Преобразует SearchResult в ответ API."""
    # Маппинг статусов
    status_map = {
        SearchStatus.OK: "ok",
        SearchStatus.EMPTY: "empty",
        SearchStatus.TIMEOUT: "partial",  # Или обработка через HTTP 503
        SearchStatus.RATE_LIMITED: "partial",
        SearchStatus.UNSUPPORTED: "unsupported",
        SearchStatus.INVALID_RESPONSE: "unsupported",
        SearchStatus.INSUFFICIENT_COVERAGE: "partial",
    }

    status = status_map.get(result.status, "partial")

    return {
        "request_id": request_id,
        "status": status,
        "items": [map_offer_to_agent(o) for o in result.offers],
        "warnings": result.warnings,
    }