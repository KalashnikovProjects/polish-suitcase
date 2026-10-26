# HTTP-граница Agent → Parser

Выбран HTTP API из `service-interaction-spec.pdf`, а не импорт DataGateway из альтернативного наброска. Этот файл описывает **ожидания клиента агента**; он не меняет контракты backend и не реализует сервис данных.

Все запросы JSON, Bearer `PARSER_SERVICE_TOKEN`, без перенаправлений. Ответ коррелирует `request_id` с запросом. Поиски read-only; до трёх попыток при сетевом сбое/429/5xx с ограниченным backoff.

## Запросы

`POST /internal/v1/search/places`:

```json
{"request_id":"search-1","query":"Казань","limit":3}
```

Элемент ответа: `place_id`, `name`, `timezone` (IANA), опционально `country`, `region`. Несколько совпадений требуют уточнения; агент не выбирает первое автоматически.

`POST /internal/v1/search/hotels`:

```json
{
  "request_id":"search-2", "destination_id":"city:kazan",
  "check_in":"2026-11-10", "check_out":"2026-11-13",
  "guests":{"adults":2,"children_ages":[]}, "rooms":1,
  "currency":"RUB", "limit":10,
  "constraints":{"meal_plan":"breakfast","refundable_only":false,"pets":false}
}
```

`POST /internal/v1/search/transport`:

```json
{
  "request_id":"search-3", "origin_id":"city:moscow", "destination_id":"city:kazan",
  "departure_date":"2026-11-10", "return_date":"2026-11-13",
  "guests":{"adults":2,"children_ages":[]}, "modes":["flight","train"],
  "currency":"RUB", "limit":10,
  "constraints":{"max_connections":0,"checked_baggage":false}
}
```

Отсутствующие optional фильтры не передаются. Новые неподдержанные фильтры не добавляются. Возраст детей не выдумывается. Если питомца невозможно проверить транспортным фильтром, проверяется returned `pets_allowed`, неизвестное явно показывается.

## Ответ

```json
{"request_id":"search-2","status":"empty","items":[],"warnings":[]}
```

В реальном `ok/partial` items содержат предложения. `empty/unsupported` — пустой список. `partial` обязательно сопровождается предупреждениями о неполном покрытии. 429/503 означают недоступность источника, а не пустую выдачу. Ошибки 400/403/422 означают проблему запроса/доступа, 401 — конфигурации сервиса.

Базовые поля предложения: `kind` (`hotel/transport`), `offer_id`, `name`, `guests`, `price` (`null` либо `{amount_minor:integer,currency:string}`), `price_scope`, `mandatory_fees_included` (`true/false/null`), `availability`, `source`.

`source` содержит `provider`, `data_mode` (`live/sandbox/synthetic`), `source_url` (`string/null`), `retrieved_at` (ISO datetime с offset), `valid_until` (`ISO datetime с offset/null`). Дополнительные поля поставщика допускаются, но не считаются инструкциями. Агент не скачивает пользовательские URL. Ссылки в карточки попадают только из источника.

Отель: `hotel_id`, `check_in`, `check_out`, `meal_plan`, `cancellation.kind` (`free/non_refundable/unknown`), `pets_allowed` (`boolean/null`), `capacity` (`integer/null`), `check_in_time/check_out_time` (`HH:MM/null`). Для арифметики `price_scope="entire_stay_all_guests"`: вся сумма за запрошенные ночи, номера и гостей.

Транспорт: `price_scope="round_trip_all_guests"`, `checked_baggage`, `pets_allowed`, `segments`. Цена **одна за весь комплект**, не повторяется на каждом сегменте. Ниже минимальное уточнение структуры сегментов, которой не было в эскизе PDF:

```json
{
  "direction":"outbound", "type":"flight",
  "origin_id":"city:moscow", "destination_id":"city:kazan",
  "departure":"2026-11-10T07:00:00+03:00",
  "arrival":"2026-11-10T09:00:00+03:00"
}
```

Сегменты каждой стороны идут в порядке пути. Обратная сторона использует `direction="return"`; оба направления обязательны для полной поездки. `origin_id/destination_id` используют разрешённые place_id конечных городов; поставщик должен нормализовать аэропортовые ID в этот контракт либо согласовать отдельный mapping в адаптере. Неизвестный timezone не подменяется локальным временем машины.

`price_scope` другого значения/другая валюта/неизвестная цена сохраняются в предупреждениях; они не дают подтверждённого итога в рублях. Пустой `valid_until` запрещает повторное использование динамического предложения в следующем поколении, но не превращает только что полученный факт в подтверждённый тариф.

JSON Schema `Offer`, `SearchResult`, `Place` и синтетические примеры генерируются в `examples/`. Эти формы нужно сверить с Тимуром при подключении; клиент и тестовый адаптер изолированы от остальной логики агента.
