"""Human-facing copy; provider diagnostics remain in private state/logs."""

import re
from datetime import date

MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)

# The public channel is for travel results. Diagnostics stay in logs/private state.
DIAGNOSTIC = re.compile(
    r"traceback|exception|\b\w*(?:Error|Timeout)\b|\b(?:HTTP|API|SSE|AMQP|JSON|SDK)\b"
    r"|https?://(?:localhost|127\.0\.0\.1|[^\s/]+:\d+)"
    r"|\b(?:unsupported|rate.?limit|backend|parser|worker|openai)\b"
    r"|\b[a-z][a-z0-9]*_[a-z0-9_]+\b|\b(?:sk-|Bearer\s)"
    r"|техническ|тайм[ао]ут|ошибк|сбо[йяе]|недоступ|некорректн\w* данн"
    r"|не (?:удалось|удалось бы|получилось) (?:обновить|проверить|подключиться|завершить)"
    r"|(?:сервис|сервер|провайдер|источник)\w* (?:не |временно |вернул|ответил)"
    r"|(?:ключ|токен)\w* (?:доступ|невер|недейств)|повтор\w* запрос",
    re.I,
)


def format_date(value):
    try:
        day = value if isinstance(value, date) else date.fromisoformat(value)
        return f"{day.day} {MONTHS[day.month - 1]} {day.year}"
    except (ValueError, TypeError):
        return str(value)


def human_text(value: str) -> str:
    value = re.sub(
        r"checked_baggage requirement is not fully confirmed by synthetic transport source",
        "Условия провоза багажа нужно уточнить.",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"(?:Покрытие источника|покрытие поиска|покрытие)\s*[:—-]?\s*(?:unsupported|partial)\.?",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"Недостаточно покрытия:[^\n.?!]*",
        "По этому направлению пока доступны не все варианты дороги.",
        value,
    )
    value = re.sub(
        r"(?:Данные\s+)?(?:synthetic|sandbox)(?:; цены и наличие тестовые)?",
        "демонстрационные цены",
        value,
        flags=re.I,
    )
    value = value.replace(
        "Источник транспорта не подключён; дорога требует отдельного поиска",
        "Дорогу пока нужно уточнить",
    )
    if DIAGNOSTIC.search(value):
        return ""
    value = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", lambda match: format_date(match[0]), value)
    return value.strip()


def human_notes(notes):
    return list(dict.fromkeys(text for note in notes if (text := human_text(note))))
