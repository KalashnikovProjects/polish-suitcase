# Data Layer Service (Parser)

Микросервис слоя данных для AI-агента путешествий. Является единственным источником фактических предложений (отели, транспорт, трансферы) для агента. Предоставляет типизированный HTTP API, нормализует данные внешних и синтетических источников, рассчитывает покрытие направлений, обеспечивает ревалидацию существенных условий перед оформлением.

Модуль изолирован: общается с агентом только по сети (HTTP/JSON), не импортируется как Python-библиотека. Внутренние модели не просачиваются наружу — между ядром и сетевым контрактом лежит слой маппинга.

## Архитектура

```
data_layer/
├── contracts.py                  # Внутренние Pydantic DTO (Offer, Query, Result)
├── hashing.py                    # content_hash: канонизация, diff, исключение служебных полей
├── catalog.py                    # Загрузка фикстур, расчёт покрытия по обязательным компонентам
├── gateway.py                    # DataGateway: поиск, реестр снимков, ревалидация
├── adapters/
│   ├── base.py                   # Протокол SearchAdapter + AdapterResult
│   └── synthetic.py              # Синтетический адаптер (детерминированная генерация)
├── service/
│   ├── api.py                    # FastAPI эндпоинты, Bearer-аутентификация, request_id
│   └── mappers.py                # Маппинг внутренних DTO в wire-формат контракта агента
├── fixtures/catalog/
│   ├── destinations.json         # 10 направлений (Москва, СПб, Сочи, Казань, Анапа, Геленджик, Владимир, Суздаль, Ярославль, Сергиев Посад)
│   ├── hotels.json               # Каталог отелей с характеристиками
│   └── transport.json            # Маршруты, включая обратные для round-trip
├── cli/
│   └── smoke_data.py             # Демонстрационные сценарии (без assert)
├── tests/unit/
│   ├── test_hashing.py           # Стабильность хэша, регрессия price_scope
│   └── test_baseline.py          # Изоляция снимков, базовая ревалидация
├── requirements.txt
├── Dockerfile
└── README.md
```

### Поток запроса

```
Агент (HTTP POST + Bearer)
  → service/api.py (валидация токена, request_id, разбор тела)
  → service/mappers.py (wire-формат → внутренний запрос)
  → gateway.py (покрытие → адаптер → реестр снимков → SearchResult)
  → adapters/synthetic.py (генерация Offer из фикстур)
  → service/mappers.py (внутренний Offer → wire-формат агента)
  → HTTP ответ {request_id, status, items, warnings}
```

### Ключевые инварианты

- Происхождение данных честно маркируется на каждом предложении (`data_mode`: `live` / `sandbox` / `synthetic`). Подмена live на мок без обозначения запрещена.
- Неизвестность условия (`unknown` / `null`) никогда не приводится к `false` или нулевой цене.
- Пустая выдача (`empty`) отличается от отказа источника (`timeout` / `rate_limited` / `invalid_response`).
- Служебные поля не влияют на `content_hash`; существенные коммерческие условия влияют.
- Повторный поиск не ломает ранее выданную ссылку на предложение.

## Быстрый старт

### Docker (рекомендуемый способ)

```bash
cp .env.example .env
docker compose up --build
curl http://localhost:8080/health
```

### Локально без Docker

```bash
python -m pip install -r data_layer/requirements.txt
python -m uvicorn data_layer.service.api:app --reload --port 8080
curl http://localhost:8080/health
```

## Переменные окружения

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `PARSER_SERVICE_TOKEN` | Bearer-токен для аутентификации запросов агента | `dev-token-change-me-in-production` |
| `PARSER_PORT` | Порт, который слушает сервис внутри контейнера | `8080` |
| `DATA_MODE` | Режим источников: `synthetic` / `sandbox` / `live` | `synthetic` |

Секреты передаются только через `.env` / `env_file`, не коммитятся. Реальные токены и ключи поставщиков в репозитории отсутствуют.

## API контракт

Все эндпоинты, кроме `/health`, требуют заголовок `Authorization: Bearer <PARSER_SERVICE_TOKEN>`. Все запросы и ответы — JSON. Ответ коррелирует `request_id` с запросом. Поиски read-only.

### GET /health

```bash
curl http://localhost:8080/health
```

```json
{"status": "ok", "service": "data-layer"}
```

### POST /internal/v1/search/places

Поиск городов и аэропортов. Возвращает `place_id` в формате `city:<name>` и IANA timezone. Несколько совпадений требуют уточнения у пользователя — агент не выбирает первое автоматически.

```bash
curl -X POST http://localhost:8080/internal/v1/search/places \
  -H "Authorization: Bearer dev-token-change-me-in-production" \
  -H "Content-Type: application/json" \
  -d '{"request_id":"places-1","query":"Казань","limit":3}'
```

```json
{
  "request_id": "places-1",
  "items": [
    {
      "place_id": "city:kazan",
      "name": "Казань",
      "timezone": "Europe/Moscow",
      "country": "Россия",
      "region": "Поволжье"
    }
  ]
}
```

### POST /internal/v1/search/hotels

Поиск отелей. Учитывает даты, состав гостей, комнаты, жёсткие ограничения.

```bash
curl -X POST http://localhost:8080/internal/v1/search/hotels \
  -H "Authorization: Bearer dev-token-change-me-in-production" \
  -H "Content-Type: application/json" \
  -d '{
    "request_id":"hotel-1",
    "destination_id":"city:sochi",
    "check_in":"2026-11-10",
    "check_out":"2026-11-13",
    "guests":{"adults":2,"children_ages":[5],"pets":[{"type":"dog","size":"small"}]},
    "rooms":1,
    "currency":"RUB",
    "limit":10,
    "constraints":{"meal_plan":"breakfast","refundable_only":false,"pets":true}
  }'
```

```json
{
  "request_id": "hotel-1",
  "status": "ok",
  "items": [
    {
      "kind": "hotel",
      "offer_id": "syn-hotel-hotel:h_sochi_01:2026-11-10:2026-11-13-abc123def456",
      "name": "Отель «Морской бриз»",
      "guests": {"adults": 2, "children_ages": [5], "pets": [{"type": "dog", "size": "small"}]},
      "price": {"amount_minor": 2850000, "currency": "RUB"},
      "price_scope": "entire_stay_all_guests",
      "mandatory_fees_included": true,
      "availability": "test",
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
      "meal_plan": "included",
      "cancellation": {"kind": "free"},
      "pets_allowed": true,
      "capacity": 4,
      "check_in_time": "14:00",
      "check_out_time": "12:00"
    }
  ],
  "warnings": []
}
```

### POST /internal/v1/search/transport

Поиск транспорта. При наличии `return_date` сервис ищет оба направления и возвращает **один** комплект: `price_scope="round_trip_all_guests"`, одна общая цена за оба направления, `segments` содержит обе стороны (`direction="outbound"` и `direction="return"`), внутри каждой стороны сегменты идут в порядке следования. Два самостоятельных one-way предложения планировщик агента не объединяет — предложение без одного из направлений отклоняется.

```bash
curl -X POST http://localhost:8080/internal/v1/search/transport \
  -H "Authorization: Bearer dev-token-change-me-in-production" \
  -H "Content-Type: application/json" \
  -d '{
    "request_id":"transport-1",
    "origin_id":"city:moscow",
    "destination_id":"city:sochi",
    "departure_date":"2026-11-10",
    "return_date":"2026-11-17",
    "guests":{"adults":2,"children_ages":[5],"pets":[{"type":"dog"}]},
    "modes":["flight","train"],
    "currency":"RUB",
    "limit":10,
    "constraints":{"max_connections":0,"checked_baggage":true}
  }'
```

```json
{
  "request_id": "transport-1",
  "status": "ok",
  "items": [
    {
      "kind": "transport",
      "offer_id": "syn-tr-transport:r_mow_sochi_fl:2026-11-10:r_sochi_mow_fl:2026-11-17-xyz789",
      "name": "Демо Авиа",
      "guests": {"adults": 2, "children_ages": [5], "pets": [{"type": "dog"}]},
      "price": {"amount_minor": 3770000, "currency": "RUB"},
      "price_scope": "round_trip_all_guests",
      "mandatory_fees_included": false,
      "availability": "test",
      "source": {
        "provider": "synthetic_v1",
        "data_mode": "synthetic",
        "retrieved_at": "2026-10-26T12:00:00Z",
        "valid_until": "2026-10-26T12:30:00Z"
      },
      "segments": [
        {
          "direction": "outbound",
          "type": "flight",
          "origin_id": "city:moscow",
          "destination_id": "city:sochi",
          "departure": "2026-11-10T07:00:00+03:00",
          "arrival": "2026-11-10T09:20:00+03:00"
        },
        {
          "direction": "return",
          "type": "flight",
          "origin_id": "city:sochi",
          "destination_id": "city:moscow",
          "departure": "2026-11-17T18:00:00+03:00",
          "arrival": "2026-11-17T20:20:00+03:00"
        }
      ],
      "checked_baggage": true,
      "pets_allowed": true
    }
  ],
  "warnings": []
}
```

Такой комплект не означает единую бронь у поставщика — это нормализованная цена за оба направления для расчёта бюджета и стыковок.

### POST /internal/v1/search/transfers

Оценка локальных трансферов. Межгородские маршруты (`pickup_place_id != dropoff_place_id`) возвращают статус `unsupported`, а не готовое предложение по локальному тарифу.

```bash
curl -X POST http://localhost:8080/internal/v1/search/transfers \
  -H "Authorization: Bearer dev-token-change-me-in-production" \
  -H "Content-Type: application/json" \
  -d '{
    "request_id":"transfer-1",
    "pickup_place_id":"city:sochi",
    "pickup_at":"2026-11-10T12:00:00+03:00",
    "dropoff_place_id":"city:sochi",
    "passengers":{"adults":2,"children_ages":[5]},
    "luggage":2
  }'
```

```json
{
  "request_id": "transfer-1",
  "status": "ok",
  "items": [
    {
      "kind": "transfer",
      "offer_id": "syn-trf-transfer:place_sochi:2026-11-10T12:00:00+03:00-def456",
      "name": "Трансфер Сочи",
      "guests": {"adults": 2, "children_ages": [5]},
      "price": {"amount_minor": 282700, "currency": "RUB"},
      "price_scope": "total",
      "mandatory_fees_included": true,
      "availability": "test",
      "source": {
        "provider": "synthetic_v1",
        "data_mode": "synthetic",
        "retrieved_at": "2026-10-26T12:00:00Z",
        "valid_until": "2026-10-26T12:30:00Z"
      }
    }
  ],
  "warnings": []
}
```

### POST /internal/v1/revalidate

Ревалидация существенных условий перед формированием топ-плана (шаг A) и перед финальным оформлением (шаг B).

```bash
curl -X POST http://localhost:8080/internal/v1/revalidate \
  -H "Authorization: Bearer dev-token-change-me-in-production" \
  -H "Content-Type: application/json" \
  -d '{
    "request_id":"reval-1",
    "reason":"checkout_preview_check",
    "offers":[
      {"offer_id":"syn-hotel-hotel:h_sochi_01:2026-11-10:2026-11-13-abc123def456","content_hash":"sha256:fad2ad37..."}
    ]
  }'
```

```json
{
  "request_id": "reval-1",
  "status": "unchanged",
  "changed_offers": [],
  "confirmed_offer_ids": ["syn-hotel-hotel:h_sochi_01:2026-11-10:2026-11-13-abc123def456"],
  "unavailable_offer_ids": [],
  "unknown_offer_ids": [],
  "warnings": [],
  "checked_at": "2026-10-26T12:15:00Z"
}
```

## Статусы ответов

| Статус | Смысл | Действие агента |
|---|---|---|
| `ok` | Есть предложения | Передавать в сборку/ранжирование |
| `partial` | Есть предложения, но покрытие неполное | Показать с предупреждением |
| `empty` | Источники ответили, подходящих нет | Объяснить, каких параметров касается; сузить/ослабить soft |
| `insufficient_coverage` | Направление покрыто слабо | Предупредить пользователя, предложить альтернативу |
| `unsupported` | Тип поиска или фильтр не поддерживается | Обозначить ограничение |
| `timeout` | Источник не ответил | Partial result с warning, не выдумывать |
| `rate_limited` | Превышен лимит | Повтор позже / cached |
| `invalid_response` | Ответ не прошёл валидацию | Контролируемый отказ |

HTTP-коды: `429` / `503` означают недоступность источника, а не пустую выдачу. `400` / `403` / `422` — проблема запроса или доступа. `401` — ошибка конфигурации токена.

## Внутренние DTO

### Offer (внутренний)

| Поле | Тип | Семантика |
|---|---|---|
| `id` | `str` | Уникальный снимок для пользователя: `provider_offer_id` + fingerprint существенных параметров запроса |
| `provider` | `str` | Имя источника (`synthetic_v1` и др.) |
| `provider_offer_id` | `str` | Стабильный идентификатор предложения у поставщика |
| `kind` | `hotel` / `transport` / `transfer` / `activity` | Тип составляющей |
| `data_mode` | `live` / `sandbox` / `synthetic` | Происхождение данных |
| `cache_hit` | `bool` | Способ получения (кэш или живой запрос) |
| `freshness` | `fresh` / `stale` / `expired` / `unknown` | Возраст данных |
| `readiness` | `ready` / `preliminary` / `blocked` | Готовность к оформлению |
| `price` | `Price` | Базовая цена в минорных единицах |
| `price_scope` | `entire_stay_all_guests` / `per_person` / `total` / `round_trip_all_guests` | База цены |
| `fees` | `Price \| None` | Обязательные сборы |
| `total_with_fees` | `Price \| None` | Итог со сборами |
| `terms` | `Terms` | Отмена, завтрак, питомцы, дети, багаж, extra |
| `details` | `dict` | Контент по типу (отель, маршрут, сегменты, точки трансфера) |
| `retrieved_at` | `datetime` | Когда получено |
| `valid_until` | `datetime \| None` | `null` = срок неизвестен, не бессрочность |
| `source_url` | `str \| None` | Происхождение; для синтетики `null` |
| `content_hash` | `str` | Отпечаток существенных условий |
| `warnings` | `list[str]` | Предупреждения по конкретному офферу |

### Wire-формат агента (после маппинга)

Маппер (`service/mappers.py`) преобразует внутренний `Offer` в формат контракта агента:

| Внутреннее поле | Wire-поле | Примечание |
|---|---|---|
| `id` | `offer_id` | |
| `details.hotel_name` / `details.route.carrier` | `name` | Читаемое название |
| `details.guests` / `details.passengers` | `guests` | Верхнеуровневое поле |
| `provider` + `data_mode` + `source_url` + `retrieved_at` + `valid_until` | `source` (объект) | |
| `price` + `price_scope` | `price` + `price_scope` | |
| `fees` / `total_with_fees` | `mandatory_fees_included` | `true`/`false`/`null` |
| `details.segments` | `segments` | Для транспорта |
| `terms.cancellation` | `cancellation.kind` | |
| `terms.breakfast` | `meal_plan` | |

Внутренние модели намеренно не делаются идентичными моделям агента — граница держится маппером.

## content_hash

SHA-256 от канонического JSON (сортировка ключей, trim строк, даты в ISO, цены только `int`).

**В хэш входят** существенные условия по типу оффера:

- hotel: `provider_offer_id`, `check_in`, `check_out`, `guests`, `rooms`, `price`, `fees`, `total_with_fees`, `price_scope`, `terms`, `room`
- transport: `provider_offer_id`, `route`, `departure_date`, `return_date`, `passengers`, `price`, `fees`, `total_with_fees`, `price_scope`, `baggage`, `terms`, `segments`
- transfer: `provider_offer_id`, `pickup`, `dropoff`, `passengers`, `price`, `fees`, `total_with_fees`, `price_scope`, `terms`

**Не входят** служебные поля: внутренний `id`, `retrieved_at`, `valid_until`, `freshness`, `cache_hit`, `data_mode`, `source_url`, `warnings`, `readiness`.

`unknown` / `null` включаются в хэш: переход `unknown → free` обязан менять отпечаток. `price_scope` читается через `getattr` как верхнеуровневое поле, а не из `details`.

`content_hash` — способ **сравнить** два состояния оффера. Он **не подтверждает доступность**. Доступность подтверждает только свежий ответ источника.

## Ревалидация

Две проверки:

| Шаг | Режим | Когда | Что делает |
|---|---|---|---|
| A | `medium` | При формировании топ-плана | Сверка отпечатков с реестром, без внешнего запроса |
| B | `strict` | Перед финальным оформлением | Повторный запрос адаптеру по сохранённым параметрам, матчинг `provider_offer_id` |

Правило: `reason="checkout_preview_check"` всегда даёт `strictness="strict"` независимо от входного параметра — понизить checkout до `medium` нельзя. Противоречивая комбинация отвергается.

Статусы `RevalidateResult`:

| Статус | Когда |
|---|---|
| `unchanged` | Все отпечатки совпали; пустой `changed_offers` + явные `confirmed_offer_ids` |
| `changed` | Есть изменения; в `changed_offers` полный новый `Offer`, `changed_fields`, `previous` |
| `partial_unknown` | Часть офферов не подтверждена (неизвестный ID, чужой/подменённый хэш) |
| `unavailable` | Предложение больше не найдено у источника после успешного ответа |
| `timeout` / `rate_limited` / `error` | Проверка не состоялась — не трактовать как `unchanged` |

Сбой источника (`timeout` / `rate_limited` / `invalid_response`) не маскируется под «предложение недоступно». Недоступным предложение считается только после успешного полноценного ответа источника, в котором оно отсутствует.

Изменённый оффер возвращается целиком, чтобы агент не склеивал старые и новые условия. После любого изменения план нужно заново прогнать через сборку и валидацию.

## Покрытие направлений

Уровень считается как худший среди пар (направление × обязательный компонент поездки). Компоненты: `hotel`, `transport_out`, `transport_back`, `transfer`.

| Уровень | Смысл |
|---|---|
| `full` | Все обязательные компоненты доступны |
| `partial` | Часть компонентов доступна |
| `sparse` | Данных мало |
| `unsupported` | Направление/компонент не поддерживается |

При `sparse` / `unsupported` и `coverage_policy="warn_if_sparse"` возвращается `insufficient_coverage`, `offers=[]`, `recommendation="warn_user"`. Модуль не выдумывает варианты вне покрытия.

Таблица покрытия фикстур:

| Направление (`place_id` / `city:*`) | Отель | Транспорт | Трансфер |
|---|---|---|---|
| `city:moscow` | full | full | full |
| `city:saint_petersburg` | full | full | full |
| `city:sochi` | full | full | full |
| `city:kazan` | full | full | full |
| `city:anapa` | partial | sparse | partial |
| `city:gelendzhik` | partial | unsupported | sparse |
| `city:vladimir` | partial | unsupported | unsupported |
| `city:suzdal` | sparse | unsupported | unsupported |
| `city:yaroslavl` | partial | sparse | unsupported |
| `city:sergiev_posad` | partial | sparse | unsupported |

## Жёсткие ограничения

Поддерживаемые ограничения для отелей (словарь фильтров):

- `breakfast_included` — завтрак включён
- `pets_allowed` — разрешены питомцы
- `free_cancellation` — бесплатная отмена
- `children_allowed` — разрешены дети

Проверка по трём состояниям:

1. **Подтверждено** — условие выполнено → `readiness=ready`
2. **Нарушено** — условие явно не выполнено → оффер исключается
3. **Неизвестно** — условие не подтверждено (`unknown`/`null`) → `readiness=preliminary` + предупреждение конкретного оффера

Неподдерживаемые ограничения возвращают `unsupported`, а не обещают их выполнение молча. Для транспорта проверяются `baggage_kg`, `route_constraints`; для трансфера — `constraints`, `luggage`, пара точек.

## Budget

`budget` принимается в запросе, но **не используется для фильтрации компонента**. Зафиксированная семантика: бюджет — контекст общего бюджета поездки для последующего compose/валидации.

Правила:
- бюджет на человека нормализуется в общий до итоговой проверки;
- не распределяется автоматически между отелем и транспортом;
- итог проверяется по сумме обязательных компонентов и сборов;
- неизвестная цена или неподтверждённые сборы означают «соблюдение бюджета не подтверждено»;
- фильтр стоимости отдельного компонента — только отдельным явно названным полем.

## Синтетический адаптер

Детерминированная генерация из versioned-фикстур. Повторный поиск с теми же параметрами даёт тот же результат.

- Цены: база из фикстуры × сезонный коэффициент месяца × детерминированный джиттер (CRC32 от seed).
- Округление: до 100 минорных единиц.
- Питомцы в транспорте: `pets_rule=paid` добавляет `fees` и `total_with_fees`.
- Round-trip: outbound + return, сумма цен и сборов без дублирования, `segments` с обеими сторонами.
- Все офферы маркируются `data_mode=synthetic`, `source_url=null`.

## Docker

- Multi-stage build: build-этап ставит зависимости, runtime-этап копирует только `/install` и код.
- Базовый образ фиксирован: `python:3.12-slim` (не `latest`).
- Непривилегированный пользователь: `appuser`, UID 10001.
- Порт внутри контейнера: `8080`.
- Healthcheck: `curl -fsS http://localhost:${PARSER_PORT}/health`, interval 15s, timeout 5s, retries 3, start-period 10s.
- Кроссплатформенность: в compose указан `platforms: linux/amd64` для совместимости с Apple Silicon.
- Секреты: только через `env_file` / `.env`, не в образе.

## Тестирование

### Юнит-тесты ядра

```bash
python -m unittest -v data_layer.tests.unit.test_hashing
python -m unittest -v data_layer.tests.unit.test_baseline
```

Ожидаемо: все тесты проходят. `test_hashing` покрывает: порядок ключей, служебные поля, существенные изменения, значимость `unknown`, влияние `price_scope`. `test_baseline` покрывает: изоляцию снимков при разных гостях, базовую ревалидацию `unchanged`.

### Регрессионные тесты ревью

```bash
python -m unittest -v test_review_regressions
```

Ожидаемо: **8 passed, 1 diagnostic error**.

| Тест | Проверяет | Статус |
|---|---|---|
| 1 | Новый поиск не затирает старый оффер (P2) | PASS |
| 2 | Round-trip включает обратный тариф (P4) | PASS |
| 3 | Timeout ≠ unavailable (P3) | PASS |
| 4 | Смена `price_scope` меняет хэш (P5) | PASS |
| 5 | Unknown-завтрак не `ready` (P6) | PASS |
| 6 | Неподдержанное ограничение не игнорируется (P6) | PASS |
| 7 | Checkout нельзя понизить до medium (P7) | PASS |
| 8 | Межгород не по локальному тарифу (P8) | PASS |
| 9 | Агент принимает внутренний Offer | DIAGNOSTIC FAIL |

Тест 9 падает ожидаемо: он проверяет прямую совместимость внутренних DTO с моделью агента. В реальной архитектуре эта совместимость обеспечивается HTTP-маппингом (`service/mappers.py`), а не равенством внутренних моделей. Диагностическая проверка, не блокер.

### Smoke-демо

```bash
python -m data_layer.cli.smoke_data
```

Шесть демонстрационных сценариев без assert: отели Сочи с собакой, транспорт Москва→Сочи, трансфер, ревалидация без изменений, подменённый хэш, Суздаль вне покрытия.

## Интеграция с агентом

Агент вызывает сервис через HTTP. Пример клиента:

```python
import httpx

async def search_hotels():
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://parser:8080/internal/v1/search/hotels",
            headers={"Authorization": "Bearer dev-token-change-me-in-production"},
            json={
                "request_id": "req-1",
                "destination_id": "city:sochi",
                "check_in": "2026-11-10",
                "check_out": "2026-11-13",
                "guests": {"adults": 2, "children_ages": []},
                "rooms": 1,
                "currency": "RUB",
                "limit": 10
            }
        )
        return response.json()
```

Правила для агента:
- Использовать `place_id` в формате `city:<name>`, не внутренние `place_*`.
- Не выдумывать `content_hash` — брать из ответа.
- Не вызывать ревалидацию шага B из LLM-tool calling — только из доменного слоя при `checkout-preview`.
- Не публиковать план при `insufficient_coverage`, `partial_unknown`, `unavailable`.
- Не подменять `unknown` на `false`.
- Не считать `empty` сетевой ошибкой и наоборот.
- Передавать в LLM только отобранные кандидаты, не весь каталог.
- При изменении требований делать новый поиск: старый оффер может быть неактуален.

## Известные ограничения MVP

1. Только синтетика: боевых адаптеров к реальным API нет, ключей нет.
2. Каталог в памяти из JSON: нет PostgreSQL, SQLAlchemy, Alembic.
3. Реестр выданных снимков в памяти экземпляра Gateway: не переживает перезапуск процесса.
4. Нет фонового обновления кэша и пауков: `valid_until` = 30 минут от момента выдачи.
5. `freshness` пока константа `fresh`: реальное сравнение с `now()` при чтении кэша не реализовано. До появления кэш-слоя проверять `valid_until` самостоятельно.
6. Шаг B — повторный вызов адаптера, не реального поставщика.
7. `rooms > 1`: параметр сохраняется, но цена считается за одну комнату.
8. Нет пересчёта валют: всё в RUB.
9. Нет точного расписания по времени суток в транспорте: есть `duration_minutes`, но нет реальных `departure`/`arrival` из источника. Стыковки по часам проверяются выше слоем.
10. Нет поиска мест по внешнему геокодеру: поиск по локальному каталогу фикстур.

## Roadmap

- **Готово:** контракты, хэши, каталог, синтетический адаптер, Gateway, две ревалидации, покрытие, HTTP-мост, Docker.
- **Далее:** Яндекс Расписания для транспорта (ключ получен), LiteAPI sandbox для отелей.
- **Далее:** PostgreSQL-модели + Alembic, Ingest CLI из фикстур в БД.
- **Далее:** тёплый кэш с TTL, реальный пересчёт `freshness`.
- **Далее:** Open-Meteo для контекста направления (погода/сезонность).
- **Отложено:** экскурсии, визы, пакетные туры — требуют новых контрактов и не должны задерживать согласование контракта агент ↔ DataGateway.