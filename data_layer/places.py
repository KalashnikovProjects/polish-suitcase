"""Explicit supported-city mapping; timezones are geographic facts, not server time."""
PLACES = [
    ("moscow", "Москва", "Moscow", "Россия", "RU", "Europe/Moscow"),
    ("saint_petersburg", "Санкт-Петербург", "Saint Petersburg", "Россия", "RU", "Europe/Moscow"),
    ("sochi", "Сочи", "Sochi", "Россия", "RU", "Europe/Moscow"),
    ("kazan", "Казань", "Kazan", "Россия", "RU", "Europe/Moscow"),
    ("ufa", "Уфа", "Ufa", "Россия", "RU", "Asia/Yekaterinburg"),
    ("rome", "Рим", "Rome", "Италия", "IT", "Europe/Rome"),
    ("paris", "Париж", "Paris", "Франция", "FR", "Europe/Paris"),
    ("istanbul", "Стамбул", "Istanbul", "Турция", "TR", "Europe/Istanbul"),
    ("antalya", "Анталья", "Antalya", "Турция", "TR", "Europe/Istanbul"),
    ("dubai", "Дубай", "Dubai", "ОАЭ", "AE", "Asia/Dubai"),
]

ALIASES = {"saint_petersburg": ["Питер", "Петербург", "Санкт Петербург"], "kazan": ["столица Татарстана"], "moscow": ["столица России"]}


def enrich_catalog(destinations):
    for key, name, english, country, code, zone in PLACES:
        identifier = "place_" + key
        destinations.setdefault(identifier, {"id": identifier, "name": name, "region": "",
            "component_coverage": {"hotel": "full", "transport": "unsupported", "transfer": "unsupported"}})
        destinations[identifier].update(country=country, country_code=code, timezone=zone,
                                         provider_city=english, aliases=[english, *ALIASES.get(key, [])])
