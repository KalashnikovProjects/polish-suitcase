# data_layer — слой данных и парсинга

Хакатон Островок 2026 · команда polish-notation · роль: Data Engineer.

Реализация раздела 09 общего архитектурного плана MVP на LangGraph.
Модуль **Data Gateway + Ingest**: источник фактов для агента.

> **Для ИИ-агентов:** этот README — контракт. Не выдумывай методы, поля и статусы,
> которых здесь нет. Единственный способ получить данные — вызвать методы
> `DataGateway` с типизированным запросом. Не парси HTML, не вызывай LLM,
> не генерируй цены и предложения. Все фактические значения приходят только из `Offer`.

---

## 0. Кратко

- Модуль принимает **формализованные характеристики** поездки и возвращает **проверенные предложения**.
- Это **Python-модуль внутри worker'а**, а не HTTP-сервис. Внутреннего REST между агентом и слоем данных нет.
- Текущий режим: `data_mode = synthetic` на versioned-фикстурах. Реальные API не подключены.
- Поддержаны три вида поиска: отели, транспорт, трансферы.
- Поддержана ревалидация в двух режимах: сверка отпечатков и повторный запрос источника.
- При недостатке покрытия модуль возвращает явный флаг, а не выдуманные варианты.

---

## 1. Границы ответственности

### Модуль делает
- читает каталог направлений, отелей и маршрутов из `fixtures/catalog/`;
- фильтрует предложения по вместимости и жёстким условиям;
- нормализует цену в минорные единицы (копейки) и считает сборы;
- считает `content_hash` существенных условий каждого предложения;
- считает покрытие относительно **обязательных компонентов конкретной поездки**;
- проверяет неизменность предложений (`revalidate_offers`);
- маркирует происхождение (`live` / `sandbox` / `synthetic`) и возраст данных.

### Модуль НЕ делает
- не разбирает свободный текст пользователя — это узлы `extract_update` / `apply_requirements` агента;
- не работает с UI, `chat_struct.json`, историей чата, `trip_id`, `plan_version`;
- не строит планы и не ранжирует компромиссы — это `compose_and_validate` / `rank_and_explain`;
- не решает, задавать ли вопрос пользователю — это `decide_next` / `ask_user`;
- не подключает реальные write-методы поставщиков и не оформляет заказы;
- не является отдельным микросервисом и не имеет своего порта/эндпоинта.

**Интеграция по плану (§02, §14):** пакет `data_layer` переносится в общий
`apps/backend/data/`, контракты — в `contracts/`. Логика не меняется,
меняется только префикс импортов.

---

## 2. Структура

```
data_layer/
├── __init__.py                 # экспорт Catalog, DataGateway, SyntheticAdapter
├── contracts.py                # все Pydantic DTO (единственный источник контрактов)
├── hashing.py                  # content_hash, normalize, diff_payloads
├── catalog.py                  # загрузка фикстур + расчёт покрытия
├── gateway.py                  # DataGateway — интерфейс для агента
├── adapters/
│   ├── base.py                 # SearchAdapter (Protocol) + AdapterResult
│   └── synthetic.py            # синтетический адаптер
├── fixtures/catalog/
│   ├── destinations.json       # 9 направлений, coverage по компонентам
│   ├── hotels.json             # 22 отеля
│   └── transport.json          # 8 маршрутов
├── cli/
│   └── smoke_data.py           # 6 проверочных сценариев
├── tests/unit/test_hashing.py  # 4 юнит-теста стабильности хэша
├── requirements.txt
└── README.md
```

---

## 3. Быстрый старт

Требуется Python 3.10+ (проверено на 3.13), Pydantic **v2**.

Из корня репозитория (папка, содержащая `data_layer`):

```bash
pip install -r data_layer/requirements.txt
python -m data_layer.cli.smoke_data
python -m pytest data_layer/tests/unit/test_hashing.py -v
```

Windows / PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -r data_layer\requirements.txt
.\.venv\Scripts\python.exe -m data_layer.cli.smoke_data
.\.venv\Scripts\python.exe -m pytest data_layer\tests\unit\test_hashing.py -v
```

Переменные окружения и API-ключи **не требуются**.

В PyCharm: пометь папку-родитель `data_layer` как **Mark Directory as → Sources Root**.

---

## 4. Инициализация

```python
from data_layer import Catalog, DataGateway, SyntheticAdapter

catalog = Catalog()                                  # читает fixtures/catalog
gateway = DataGateway(catalog, SyntheticAdapter(catalog))
```

`Catalog(fixtures_dir=...)` принимает произвольный путь — для тестов и будущего ingest.

> **Важно для жизненного цикла:** реестр выданных офферов (`_registry`) живёт
> в экземпляре `DataGateway`. Для ревалидации используй **тот же экземпляр**,
> в котором выполнялся поиск. После перезапуска процесса сначала сделай
> повторный поиск, потом ревалидацию.

---

## 5. Интерфейс DataGateway

```python
class DataGateway:
    async def search_hotels(self, query: HotelSearchQuery) -> SearchResult
    async def search_transport(self, query: TransportSearchQuery) -> SearchResult
    async def estimate_transfers(self, query: TransferSearchQuery) -> SearchResult
    async def get_destination_context(self, destination_id: str) -> DestinationContext
    async def revalidate_offers(self, req: RevalidateRequest) -> RevalidateResult
```

Все методы `async`. Возвращают Pydantic-модели.
Для передачи в LLM или через API используй `.model_dump(mode="json")`.

### 5.1. Запросы

Общие поля `SearchQueryBase`:

| Поле | Тип | Назначение |
|---|---|---|
| `query_id` | `str` | Идентификатор запроса для логов |
| `budget` | `Budget \| None` | `max_minor`, `currency`, `scope` |
| `required_components` | `list[TripComponent]` | Обязательные компоненты **этой** поездки |
| `coverage_policy` | `warn_if_sparse` / `best_effort` | По умолчанию `warn_if_sparse` |
| `data_modes_allowed` | `list[DataMode]` | По умолчанию live, sandbox, synthetic |
| `origin` | `str \| None` | Имя узла графа / ревизия, только для логов |

`TripComponent`: `hotel`, `transport_out`, `transport_back`, `transfer`.

> **Обязательный параметр.** Если `required_components` не передать,
> покрытие считается только по типу текущего поиска. Для полноценной
> поездки всегда передавай полный список компонентов.

`HotelSearchQuery`:
`destination_ids: list[str]`, `check_in: date`, `check_out: date`,
`guests: Guests`, `rooms: int`, `hard_constraints: list[str]`,
`soft_preferences: list[str]`.

`TransportSearchQuery`:
`origin_id: str`, `destination_id: str`, `departure_date: date`,
`return_date: date | None`, `passengers: Guests`,
`modes: list["flight" | "train" | "bus"]`, `baggage_kg: int | None`,
`route_constraints: list[str]`.

`TransferSearchQuery`:
`pickup_place_id`, `pickup_at: datetime`, `dropoff_place_id`,
`passengers: Guests`, `luggage: int`, `constraints: list[str]`.

`Guests`: `adults: int`, `children_ages: list[int]`, `pets: list[Pets]`.
`Pets`: `type: str`, `size: str`.

### 5.2. Пример: отели для семьи с собакой

```python
from datetime import date
from data_layer.contracts import Guests, Pets, HotelSearchQuery, TripComponent

query = HotelSearchQuery(
    query_id="q_hotels_1",
    destination_ids=["place_sochi"],
    check_in=date(2026, 6, 10),
    check_out=date(2026, 6, 17),
    guests=Guests(adults=2, children_ages=[5], pets=[Pets(type="dog")]),
    required_components=[
        TripComponent.HOTEL,
        TripComponent.TRANSPORT_OUT,
        TripComponent.TRANSPORT_BACK,
        TripComponent.TRANSFER,
    ],
    origin="search_candidates",
)

result = await gateway.search_hotels(query)
if result.status == "ok":
    candidates = [o.model_dump() for o in result.offers]
```

### 5.3. Пример: транспорт

```python
from data_layer.contracts import TransportSearchQuery

q = TransportSearchQuery(
    query_id="q_transport_1",
    origin_id="place_moscow",
    destination_id="place_sochi",
    departure_date=date(2026, 6, 10),
    passengers=Guests(adults=2, children_ages=[5], pets=[Pets(type="dog")]),
    modes=["flight", "train"],
)
result = await gateway.search_transport(q)
```

---

## 6. Ответ: SearchResult

```json
{
  "status": "ok",
  "offers": [ { " см. Offer ниже ": "..." } ],
  "coverage": {
    "destination_id": "place_sochi",
    "level": "full",
    "required_components": ["hotel", "transport_out"],
    "missing": [],
    "recommendation": null
  },
  "warnings": [],
  "query_fingerprint": "sha256:..."
}
```

Статусы `SearchResult.status`:

| Статус | Значение | Действие агента |
|---|---|---|
| `ok` | Есть предложения | Передавать в compose/rank |
| `empty` | Источники ответили, подходящих нет | Объяснить, каких параметров касается; сузить/ослабить soft |
| `insufficient_coverage` | Направление покрыто слабо | **Предупредить пользователя**, предложить альтернативу |
| `timeout` | Источник не ответил | Partial result с warning, не выдумывать |
| `rate_limited` | Лимит запросов | Повтор позже / cached |
| `unsupported` | Тип поиска не поддерживается | Обозначить ограничение |
| `invalid_response` | Ответ не прошёл валидацию | Контролируемый отказ |

**`empty` ≠ `timeout`.** Не трактуй сетевую ошибку как «вариантов нет».

---

## 7. Offer — единица факта

| Поле | Тип / значения | Семантика |
|---|---|---|
| `id` | `str` | Внутренний ID. На него ссылается агент |
| `provider` | `str` | Сейчас `synthetic_v1` |
| `provider_offer_id` | `str` | Стабильный ID у источника, используется при повторном запросе |
| `kind` | `hotel` / `transport` / `transfer` / `activity` | Тип составляющей |
| `data_mode` | `live` / `sandbox` / `synthetic` | **Происхождение** данных |
| `cache_hit` | `bool` | **Способ получения**: кэш или живой запрос |
| `freshness` | `fresh` / `stale` / `expired` / `unknown` | **Возраст** данных |
| `readiness` | `ready` / `preliminary` / `blocked` | Готовность к оформлению |
| `price` | `{amount_minor, currency}` | Базовая цена в копейках |
| `price_scope` | `entire_stay_all_guests` / `per_person` / `total` | База цены |
| `fees` | `Price \| None` | Обязательные сборы (например, питомец) |
| `total_with_fees` | `Price \| None` | Итог со сборами |
| `terms` | `Terms` | Отмена, завтрак, питомцы, дети, багаж, extra |
| `details` | `dict` | Контент по типу: отель, маршрут, точки трансфера |
| `retrieved_at` | `datetime` | Когда получено |
| `valid_until` | `datetime \| None` | `null` = срок **неизвестен**, не бессрочность |
| `source_url` | `str \| None` | Происхождение для синтетики — `null` |
| `content_hash` | `str` | Отпечаток существенных условий |
| `warnings` | `list[str]` | Предупреждения по конкретному офферу |

### Правила чтения Offer для агента

1. **Никогда не превращай `unknown` в `false` или нулевую цену.**
   `terms.cancellation = "unknown"`, `terms.pets_allowed = null` — это неизвестно, а не запрещено.
2. `readiness = "preliminary"` — карточку можно показать как **предварительную**,
   но оформлять нельзя: критичное условие не подтверждено.
3. `readiness = "blocked"` — нарушено жёсткое условие.
4. В объяснении пользователь должен видеть `data_mode = synthetic`,
   если использовалась синтетика. Молчаливая подмена запрещена.
5. Сравнивай `total_with_fees`, если поле есть, иначе `price`.
   Не забывай `price_scope`.
6. Не изменяй `content_hash` и не вычисляй его сам — только возвращай как получил.

---

## 8. content_hash

Считается в `hashing.py`, SHA-256 от канонического JSON
(сортировка ключей, trim строк, цены только `int`).

**В хэш входят** только существенные условия:

- hotel: `provider_offer_id`, `check_in`, `check_out`, `guests`, `rooms`,
  `price`, `fees`, `total_with_fees`, `terms`, `room`;
- transport: `provider_offer_id`, `route`, даты, `passengers`,
  `price`, `fees`, `total_with_fees`, `baggage`, `terms`;
- transfer: `provider_offer_id`, `pickup`, `dropoff`, `passengers`,
  `price`, `fees`, `total_with_fees`, `terms`.

**Не входят** служебные поля: внутренний `id`, `retrieved_at`, `valid_until`,
`freshness`, `cache_hit`, `data_mode`, `source_url`, `warnings`.

`unknown` / `null` **включаются** в хэш: переход `unknown → free` обязан менять отпечаток.

> `content_hash` — способ **сравнить** два состояния оффера.
> Он **не подтверждает доступность**. Доступность подтверждает только свежий ответ источника.

---

## 9. Ревалидация: две проверки

### Шаг A — при формировании топ-плана

```python
from data_layer.contracts import OfferCheckRef, RevalidateRequest, RevalidateReason

refs = [OfferCheckRef(offer_id=o.id, content_hash=o.content_hash)
        for o in result.offers[:2]]

check = await gateway.revalidate_offers(RevalidateRequest(
    reason=RevalidateReason.PLAN_CANDIDATE_CHECK,   # strictness = medium
    offers=refs,
))
```

Сверка отпечатков с реестром. Быстро, без внешнего запроса.

### Шаг B — перед финальным оформлением

```python
check = await gateway.revalidate_offers(RevalidateRequest(
    reason=RevalidateReason.CHECKOUT_PREVIEW_CHECK,  # strictness = strict
    offers=refs,
))
```

Делает **повторный запрос адаптеру** по сохранённым параметрам и матчит
`provider_offer_id`. Это тестовый аналог real-time проверки поставщика.

> **Вызов шага B — обязанность доменного слоя API**, а не агента:
> `POST /api/v1/trips/{id}/checkout-preview` → `DomainServices` → `DataGateway`.
> LangGraph и LLM для этого не нужны (план, §12).

### Ответ RevalidateResult

```json
{
  "status": "unchanged",
  "changed_offers": [],
  "confirmed_offer_ids": ["of_1", "of_2"],
  "unavailable_offer_ids": [],
  "unknown_offer_ids": [],
  "warnings": [],
  "checked_at": "2026-10-25T10:15:00Z"
}
```

| Статус | Когда |
|---|---|
| `unchanged` | Все отпечатки совпали; пустой `changed_offers` + явные `confirmed_offer_ids` |
| `changed` | Есть изменения; в `changed_offers` полный новый `Offer`, `changed_fields`, `previous` |
| `partial_unknown` | Часть офферов не подтверждена (неизвестный ID, чужой/подменённый хэш) |
| `unavailable` | Предложение больше не найдено у источника |
| `timeout` / `rate_limited` / `error` | Проверка не состоялась — **не** трактовать как `unchanged` |

**Пустой список изменений без явного статуса не имеет смысла.** Всегда читай `status`.

Изменённый оффер возвращается **целиком**, чтобы агент не склеивал старые и новые условия.
После любого изменения план нужно заново прогнать через `compose_and_validate`.

---

## 10. Покрытие направлений

Покрытие считается как **худший** уровень среди пар
`(направление × обязательный компонент поездки)`.

Уровни: `full` → `partial` → `sparse` → `unsupported`.
При `sparse` / `unsupported` и `coverage_policy = "warn_if_sparse"`:
`status = "insufficient_coverage"`, `offers = []`,
`coverage.recommendation = "warn_user"`.

| Направление (`destination_id`) | Отель | Транспорт | Трансфер |
|---|---|---|---|
| `place_moscow` | full | full | full |
| `place_saint_petersburg` | full | full | full |
| `place_sochi` | full | full | full |
| `place_anapa` | partial | sparse | partial |
| `place_gelendzhik` | partial | unsupported | sparse |
| `place_vladimir` | partial | unsupported | unsupported |
| `place_suzdal` | sparse | unsupported | unsupported |
| `place_yaroslavl` | partial | sparse | unsupported |
| `place_sergiev_posad` | partial | sparse | unsupported |

Пример корректной поездки без транспорта:
`required_components = [hotel]` → покрытие считается только по отелям.
Трансфер не обязателен, если его нет в списке компонентов.

**Для агента:** не проси модуль «найти что-нибудь в Суздале с перелётом» —
он честно вернёт недостаток покрытия. Предложи наземный маршрут или другое направление.

---

## 11. Пример интеграции в узел графа

Код узла пишет ML-инженер. Контракт твой:

```python
async def search_candidates(state):
    gateway: DataGateway = state["gateway"]
    req = state["requirements"]   # уже нормализованный patch через apply_requirements

    hotels = await gateway.search_hotels(HotelSearchQuery(
        query_id=f"{state['run_id']}-hotels",
        destination_ids=req["destination_ids"],
        check_in=req["check_in"],
        check_out=req["check_out"],
        guests=req["guests"],
        hard_constraints=req["hard_constraints"],
        required_components=req["required_components"],
        origin="search_candidates",
    ))

    if hotels.status != "ok":
        return {"data_status": hotels.status,
                "coverage": hotels.coverage.model_dump(),
                "warnings": hotels.warnings}

    return {
        "hotel_candidate_ids": [o.id for o in hotels.offers],
        "hotel_candidates": [o.model_dump() for o in hotels.offers],
    }
```

Дальше compose/rank (обычный Python-код) отбирает **разнообразные** компромиссы.
LLM получает только выбранные факты, не весь каталог (план, §04, §10, §13).
Обрезки выдачи по длине в модуле нет и не будет: `limit` не реализован намеренно.

---

## 12. Известные ограничения MVP

Честный список того, чего пока нет. Не считай это готовым production-слоем.

1. **Только синтетика.** Боевых API-адаптеров нет, ключей нет.
2. **Каталог в памяти из JSON.** PostgreSQL, SQLAlchemy-модели и Alembic — следующий шаг.
3. **Нет фонового обновления кэша и пауков.** `valid_until` = 30 минут от момента выдачи.
4. **`freshness` пока проставляется как `fresh`.** Реальное сравнение с `now()`
   при чтении кэша ещё не реализовано. До появления кэш-слоя проверяй
   `valid_until` самостоятельно перед использованием оффера.
5. **Реестр офферов в памяти экземпляра.** Не переживает перезапуск процесса.
6. **Шаг B — повторный вызов адаптера**, не реального поставщика.
7. **Несколько комнат:** параметр `rooms` сохраняется, но цена пока считается
   за одну комнату. Для демо используй `rooms = 1` либо учитывай это в расчёте.
8. **Нет пересчёта валют** (всё в RUB) и нет реальных тарифов/налогов.
9. **Нет расписания по времени суток** в транспорте: есть `duration_minutes`,
   но нет точного времени вылета. Стыковки по часам пока проверяются выше слоем.

---

## 13. Тесты

```bash
python -m pytest data_layer/tests/unit/test_hashing.py -v
python -m data_layer.cli.smoke_data
```

Юнит-тесты (ожидаемо **4 passed**):
- порядок ключей не влияет на хэш;
- служебные поля не влияют на хэш;
- изменение даты меняет хэш;
- переход `free → unknown` меняет хэш.

Смоук-сценарии (6):
1. Отели Сочи, семья + собака → `ok`, фильтр по вместимости, `preliminary` при unknown.
2. Транспорт Москва → Сочи → `ok`, сбор за питомца в `fees` / `total_with_fees`.
3. Трансфер Сочи → `ok`.
4. Ревалидация без изменений → `unchanged` + `confirmed_offer_ids`.
5. Подменённый хэш → `partial_unknown` + `unknown_offer_ids`.
6. Суздаль с обязательным транспортом → `insufficient_coverage` + `warn_user`.

Вывод смоука — компактная сводка **всех** офферов, без обрезки JSON.
Это только отладочная печать: агент всегда получает полный `SearchResult`.

---

## 14. Roadmap модуля

- **Готово:** контракты, хэши, каталог, синтетический адаптер, Gateway, две ревалидации, покрытие.
- **Далее:** PostgreSQL-модели + Alembic, `Ingest CLI` из фикстур в БД.
- **Далее:** один отельный и один транспортный адаптер к реальному API / песочнице.
- **Далее:** фоновое обновление кэша с TTL по типам данных.
- **Отложено:** пауки для обогащения покрытия — только фоном, никогда на пути пользователя.

---

## 15. Правила для ИИ-интеграторов

1. Не импортируй приватные методы (`_search`, `_rerequest`, `_register`).
2. Не передавай в запросы свободный текст, строковые цены и «1 ноября».
3. Не выдумывай `destination_id`: бери из таблицы раздела 10 или из `Catalog.destinations`.
4. Не генерируй `content_hash` сам — бери из `Offer`.
5. Не вызывай шаг B из LLM-tool calling: только из доменного слоя.
6. Не публикуй план при `insufficient_coverage`, `partial_unknown`, `unavailable`.
7. Не подменяй `unknown` на `false`.
8. Не считай `empty` сетевой ошибкой и наоборот.
9. Не передавай в LLM весь каталог — только отобранные кандидаты.
10. При изменении требований делай новый поиск: старый оффер может быть неактуален.