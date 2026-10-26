# Интеграция с сервисом `data_layer`

Этот документ описывает сетевой контракт сервиса `data_layer` для команды агента.

Важная архитектурная граница:

- агент **не импортирует** `data_layer` как Python-библиотеку;
- агент общается с `data_layer` только через HTTP;
- внутренняя модель `Offer` из `data_layer` не обязана совпадать с моделями агента;
- совместимость обеспечивает HTTP-слой и мапперы внутри `data_layer`.

---

## 1. Базовый URL

Локальный запуск:

```text
http://localhost:8080
```

Если сервис поднимается через `data_layer/docker-compose.yml`, имя сервиса в compose:

```text
parser
```

Внутри одной Docker-сети агент может обращаться так:

```text
http://parser:8080
```

Если команда решит использовать общий корневой `docker-compose.yml`, имя сервиса нужно согласовать отдельно.

---

## 2. Авторизация

Все эндпоинты, кроме `/health`, требуют заголовок:

```text
Authorization: Bearer <PARSER_SERVICE_TOKEN>
```

Переменная окружения сервиса:

```env
PARSER_SERVICE_TOKEN=dev-token-change-me-in-production
```

Коды авторизации:

| HTTP-код | Значение |
|---|---|
| `401` | отсутствует заголовок, неверный формат или неверный токен |

---

## 3. Единый конверт поисковых ответов

Все поисковые эндпоинты возвращают единый конверт:

```json
{
  "request_id": "...",
  "status": "ok | empty | unsupported | partial | error",
  "items": [],
  "warnings": []
}
```

Правила:

- `request_id` должен эхо-возвращаться из запроса;
- `status = "ok"` — есть подходящие предложения;
- `status = "empty"` — источники ответили, подходящих предложений нет;
- `status = "unsupported"` — запрос или направление не поддерживается;
- `status = "partial"` — есть предложения, но есть предупреждения;
- `status = "error"` — сбой источника или некорректный ответ источника.

Для `status = "ok"` и `status = "partial"` массив `items` должен быть непустым.

Для `status = "empty"` и `status = "unsupported"` массив `items` должен быть пустым.

---

## 4. Идентификаторы мест

Внутренний формат `data_layer` использует:

```text
place_moscow
place_sochi
```

Wire-формат для агента использует:

```text
city:moscow
city:sochi
```

Маппинг выполняется на стороне `data_layer`.

Агент должен отправлять и ожидать `city:*`.

---

## 5. Эндпоинты

### 5.1. Healthcheck

```http
GET /health
```

Ответ:

```json
{
  "status": "ok",
  "service": "data-layer"
}
```

---

### 5.2. Поиск мест

```http
POST /internal/v1/search/places
```

Пример запроса:

```json
{
  "request_id": "places-1",
  "query": "Казань",
  "limit": 3
}
```

Ответ:

```json
{
  "request_id": "places-1",
  "status": "ok",
  "items": [
    {
      "place_id": "city:kazan",
      "name": "Казань",
      "timezone": "Europe/Moscow",
      "country": "Россия",
      "region": "Поволжье"
    }
  ],
  "warnings": []
}
```

Если найдено несколько совпадений, агент не должен автоматически выбирать первый вариант без уточнения у пользователя.

---

### 5.3. Поиск отелей

```http
POST /internal/v1/search/hotels
```

Пример запроса:

```json
{
  "request_id": "hotels-1",
  "destination_id": "city:sochi",
  "check_in": "2026-11-10",
  "check_out": "2026-11-13",
  "guests": {
    "adults": 2,
    "children_ages": [5],
    "pets": []
  },
  "rooms": 1,
  "currency": "RUB",
  "limit": 10,
  "constraints": {
    "refundable_only": true,
    "breakfast_included": true,
    "pets": true
  }
}
```

Поддерживаемые ограничения отелей:

| Поле запроса | Внутреннее жёсткое ограничение |
|---|---|
| `constraints.pets = true` | `pets_allowed` |
| `constraints.refundable_only = true` | `free_cancellation` |
| `constraints.breakfast_included = true` | `breakfast_included` |
| `constraints.meal_plan = "breakfast"` | `breakfast_included` |

Ответ содержит массив `items`, где каждый элемент имеет обязательные поля:

```json
{
  "kind": "hotel",
  "offer_id": "...",
  "content_hash": "sha256:...",
  "name": "...",
  "guests": {
    "adults": 2,
    "children_ages": [5],
    "pets": []
  },
  "price": {
    "amount_minor": 2850000,
    "currency": "RUB"
  },
  "price_scope": "entire_stay_all_guests",
  "mandatory_fees_included": true,
  "availability": "test",
  "readiness": "ready | preliminary | blocked",
  "warnings": [],
  "source": {
    "provider": "synthetic_v1",
    "data_mode": "synthetic",
    "source_url": null,
    "retrieved_at": "2026-10-26T12:00:00Z",
    "valid_until": "2026-10-26T12:30:00Z"
  },
  "hotel_id": "h_sochi_01",
  "check_in": "2026-11-10",
  "check_out": "2026-11-13",
  "meal_plan": "breakfast | none | unknown",
  "cancellation": {
    "kind": "free | paid | non_refundable | unknown"
  },
  "pets_allowed": true,
  "capacity": 4
}
```

Важно:

- `meal_plan` возвращается как `breakfast`, `none` или `unknown`;
- значение `included` в `meal_plan` не используется;
- `content_hash` обязателен для последующей ревалидации;
- `readiness` показывает, можно ли считать предложение подтверждённым.

---

### 5.4. Поиск транспорта

```http
POST /internal/v1/search/transport
```

Пример запроса:

```json
{
  "request_id": "transport-1",
  "origin_id": "city:moscow",
  "destination_id": "city:sochi",
  "departure_date": "2026-11-10",
  "return_date": "2026-11-13",
  "guests": {
    "adults": 2,
    "children_ages": [5],
    "pets": []
  },
  "modes": ["flight", "train"],
  "currency": "RUB",
  "limit": 10,
  "constraints": {}
}
```

Ответ содержит один транспортный оффер на поездку туда-обратно, если задан `return_date`.

Пример элемента:

```json
{
  "kind": "transport",
  "offer_id": "...",
  "content_hash": "sha256:...",
  "name": "Демо Авиа",
  "guests": {
    "adults": 2,
    "children_ages": [5],
    "pets": []
  },
  "price": {
    "amount_minor": 3770000,
    "currency": "RUB"
  },
  "price_scope": "round_trip_all_guests",
  "mandatory_fees_included": false,
  "availability": "test",
  "readiness": "ready",
  "warnings": [],
  "source": {
    "provider": "synthetic_v1",
    "data_mode": "synthetic",
    "source_url": null,
    "retrieved_at": "2026-10-26T12:00:00Z",
    "valid_until": "2026-10-26T12:30:00Z"
  },
  "segments": [
    {
      "direction": "outbound",
      "type": "flight",
      "origin_id": "city:moscow",
      "destination_id": "city:sochi",
      "departure": "2026-11-10T08:00:00+03:00",
      "arrival": "2026-11-10T10:30:00+03:00"
    },
    {
      "direction": "return",
      "type": "flight",
      "origin_id": "city:sochi",
      "destination_id": "city:moscow",
      "departure": "2026-11-13T18:00:00+03:00",
      "arrival": "2026-11-13T20:30:00+03:00"
    }
  ],
  "checked_baggage": true,
  "pets_allowed": true
}
```

Правила:

- при наличии `return_date` транспорт возвращается одним комплектом;
- `price_scope = "round_trip_all_guests"`;
- `segments` содержит оба направления: `outbound` и `return`;
- идентификаторы городов в сегментах возвращаются в формате `city:*`;
- `departure` и `arrival` присутствуют даже для синтетического расписания.

---

### 5.5. Поиск трансферов

```http
POST /internal/v1/search/transfers
```

Пример локального запроса:

```json
{
  "request_id": "transfer-1",
  "pickup_place_id": "city:sochi",
  "dropoff_place_id": "city:sochi",
  "pickup_at": "2026-11-10T12:00:00+03:00",
  "passengers": {
    "adults": 2,
    "children_ages": [5]
  },
  "luggage": 2
}
```

Межгородский трансфер:

```json
{
  "request_id": "transfer-2",
  "pickup_place_id": "city:moscow",
  "dropoff_place_id": "city:sochi",
  "pickup_at": "2026-11-10T12:00:00+03:00",
  "passengers": {
    "adults": 2
  },
  "luggage": 1
}
```

Для межгородского трансфера ожидается:

```json
{
  "request_id": "transfer-2",
  "status": "unsupported",
  "items": [],
  "warnings": [
    "Intercity transfer city:moscow -> city:sochi is not supported in MVP"
  ]
}
```

---

### 5.6. Ревалидация

```http
POST /internal/v1/revalidate
```

Пример запроса:

```json
{
  "request_id": "reval-1",
  "reason": "checkout_preview_check",
  "offers": [
    {
      "offer_id": "...",
      "content_hash": "sha256:..."
    }
  ]
}
```

Ответ:

```json
{
  "request_id": "reval-1",
  "status": "unchanged",
  "changed_offers": [],
  "confirmed_offer_ids": ["..."],
  "unavailable_offer_ids": [],
  "unknown_offer_ids": [],
  "warnings": [],
  "checked_at": "2026-10-26T12:15:00Z"
}
```

Возможные статусы:

```text
unchanged
changed
partial_unknown
unavailable
timeout
rate_limited
error
```

---

## 6. HTTP-коды

| Код | Значение |
|---|---|
| `200` | успешный поисковый ответ, включая бизнес-статусы `ok`, `empty`, `unsupported`, `partial` |
| `400` | отсутствует обязательное поле или запрос логически некорректен |
| `401` | неверный или отсутствующий токен |
| `422` | неверный формат даты, состава гостей или неподдерживаемое ограничение |
| `429` | источник вернул rate limit |
| `503` | таймаут источника или некорректный ответ источника |

---

## 7. Повторные попытки

Агент может повторять запросы при:

- `429`;
- `502`;
- `503`;
- сетевой ошибке.

Рекомендуется не более 3 попыток с ограниченным backoff.

Не нужно повторять запрос при:

- `400`;
- `401`;
- `422`.

---

## 8. Известные ограничения MVP

1. Данные синтетические:

```json
{
  "source": {
    "provider": "synthetic_v1",
    "data_mode": "synthetic"
  }
}
```

2. Реестр выданных офферов живёт в памяти процесса.
3. Ревалидация должна идти в тот же экземпляр сервиса, который выдал оффер.
4. `rooms > 1` не поддерживается и отклоняется.
5. Валюта только `RUB`.
6. Межгородские трансферы возвращают `unsupported`.
7. `max_connections > 0` для транспорта не поддерживается.
8. `checked_baggage` для транспорта пока не подтверждается полностью и может возвращать предупреждение.

---

## 9. Быстрая проверка

Перед выполнением запросов убедитесь, что сервис запущен в отдельном окне терминала из корня репозитория:

```powershell
.\.venv\Scripts\python.exe -m uvicorn data_layer.service.api:app --reload --port 8080
```

### Способ A: Через `curl.exe` и JSON-файлы (рекомендуется для Windows/PowerShell)

PowerShell на Windows имеет специфику экранирования JSON при прямой передаче в `-d`. Самый надёжный способ — хранить тело запроса в файле и передавать его через `-d "@filename.json"`.

Готовые примеры JSON-файлов находятся в [`examples/`](./examples/). Откройте PowerShell в этой папке:

```powershell
cd data_layer\examples
```

**Healthcheck:**
```powershell
curl.exe http://localhost:8080/health
```

**Поиск мест:**
```powershell
curl.exe -X POST "http://localhost:8080/internal/v1/search/places" -H "Authorization: Bearer dev-token-change-me-in-production" -H "Content-Type: application/json" -d "@places-request.json"
```

**Поиск отелей:**
```powershell
curl.exe -X POST "http://localhost:8080/internal/v1/search/hotels" -H "Authorization: Bearer dev-token-change-me-in-production" -H "Content-Type: application/json" -d "@hotels-request.json"
```

**Поиск транспорта (round-trip):**
```powershell
curl.exe -X POST "http://localhost:8080/internal/v1/search/transport" -H "Authorization: Bearer dev-token-change-me-in-production" -H "Content-Type: application/json" -d "@transport-request.json"
```

**Поиск локального трансфера:**
```powershell
curl.exe -X POST "http://localhost:8080/internal/v1/search/transfers" -H "Authorization: Bearer dev-token-change-me-in-production" -H "Content-Type: application/json" -d "@transfer-local-request.json"
```

### Способ B: Через `Invoke-RestMethod` (нативный PowerShell)

Этот способ не требует файлов — PowerShell сам корректно сериализует hashtable в JSON с поддержкой UTF-8.

```powershell
$body = @{
    request_id = "hotels-1"
    destination_id = "city:sochi"
    check_in = "2026-11-10"
    check_out = "2026-11-13"
    guests = @{ adults = 2; children_ages = @(); pets = @() }
    rooms = 1
    currency = "RUB"
    constraints = @{ refundable_only = $true }
}

Invoke-RestMethod -Uri "http://localhost:8080/internal/v1/search/hotels" -Method Post -Headers @{ "Authorization" = "Bearer dev-token-change-me-in-production" } -ContentType "application/json" -Body ($body | ConvertTo-Json -Depth 5)
```