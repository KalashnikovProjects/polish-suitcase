# AI-планировщик путешествий

Планировщик собирает требования в чате, ищет варианты, сохраняет изменения параметров и показывает обновления через SSE. Это MVP подбора: покупки, бронирования и оплаты пока нет.

## Запуск

Нужны Docker Desktop/Engine и Compose v2. Скопируйте `.env.example` в `.env`, заполните `OPENAI_API_KEY`, `LITEAPI_KEY` и `YANDEX_RASP_API_KEY`, задайте собственные пароли и сервисные токены, затем:

```sh
docker compose up --build -d
docker compose ps
```

Основной Compose всегда запускает `AGENT_MODE=openai` и `DATA_MODE=live`, включая стенды со старым `.env`, где остались `demo`/`synthetic`. Без обязательных ключей сервисы останавливаются до обработки чатов; подмены внешнего поиска демонстрационными предложениями нет. После обновления существующего стенда выполните `docker compose up -d --build agent-worker data-layer`. Старый демонстрационный кеш не используется при переходе к внешним источникам.

Для отдельного запуска без внешних API используйте явный демонстрационный override:

```sh
docker compose -f docker-compose.yaml -f docker-compose.demo.yaml up --build -d
```

Интерфейс: http://localhost:3000. Next.js проксирует `/api/v1/*` в backend на том же адресе, включая cookies и SSE. Caddy не требуется. Compose поднимает шесть сервисов: frontend, backend, agent-worker, data-layer, MongoDB и RabbitMQ. Порты на хосте привязаны к loopback.

Прямые адреса по умолчанию: backend http://localhost:8000/healthz, data-layer http://localhost:8080/health, RabbitMQ management http://localhost:15672. Порты задаются в `.env`; при смене `FRONTEND_PORT` обновите `CORS_ORIGINS`. `BACKEND_INTERNAL_URL` — адрес backend внутри Docker, не публичный порт хоста.

Остановка с сохранением данных: `docker compose down`. Команда `down -v` удаляет данные — используйте её только для одноразового тестового стека.

## Режимы и источники

| Настройка | Поведение |
| --- | --- |
| `AGENT_MODE=demo` | Детерминированный агент и встроенные демонстрационные предложения, без внешних ключей |
| `AGENT_MODE=openai` | Настоящее извлечение требований и объяснение через OpenAI, поиск через data-layer |
| `DATA_MODE=synthetic` | Только версионированные синтетические фикстуры |
| `DATA_MODE=sandbox`, `live` или `live_transport` | Жильё через LiteAPI, дорога через Яндекс Расписания; каждый источник требует своего ключа |

Для прямого запуска Python вне Compose используйте `AGENT_MODE=openai`, `DATA_MODE=live` и заполните `OPENAI_API_KEY`, `LITEAPI_KEY`, `YANDEX_RASP_API_KEY`. `sandbox` и `live_transport` также вызывают оба внешних API; фактический sandbox/live LiteAPI определяется ключом и ответом. `OPENAI_MODEL` и `OPENAI_REASONING_EFFORT` настраиваются отдельно. `AGENT_MAX_CONCURRENT_CHATS` ограничивает параллельные активные чаты (по умолчанию 32). `GUEST_NATIONALITY` по умолчанию RU; перед использованием для другого гражданства измените её.

В sandbox/live нет подмены неудачного поиска синтетическими ценами. LiteAPI используется для read-only `/hotels/rates`; фактический sandbox/live берётся из ответа. Города вне встроенного справочника разрешаются через GeoNames/Open-Meteo с проверенными координатами, страной и IANA timezone. LiteAPI ищет жильё по координатам; Яндекс Расписания получает код города через nearest_settlement и объединяет прямые сегменты обеих сторон в комплект. Покрытие зависит от источника. Расписание не подтверждает места, багаж или конечный тариф. Неизвестная цена остаётся неизвестной; взрослые справочные тарифы не переносятся на детей. Трансферы не реализованы. При недоступности одного источника возможны отдельно жильё или расписание, с честным указанием неполной стоимости. Глобальный геопоиск не гарантирует наличие предложений поставщика.

Свободный запрос сначала превращается в цель отдыха и сезонный список городов; общего списка «Казань, Петербург, Сочи» для всех запросов нет. Цель сохраняется при редактировании параметров. Агент проверяет до шести направлений в пределах 24 логических запросов и прекращает расширение, когда есть три подходящих города. Карточки сравнивают разные города перед альтернативами в одном месте. Неподдержанный источник и ошибка поиска не считаются отсутствием билетов.

География: [GeoNames](https://www.geonames.org/) через [Open-Meteo Geocoding API](https://open-meteo.com/en/docs/geocoding-api). Публичный endpoint используется для некоммерческого MVP; коммерческое использование требует соответствующего тарифа или своего геокодера. География не является прогнозом погоды.

Агент сразу предлагает варианты и использует последние реплики для коротких ответов вроде «и то и то» и изменений состава группы. Без дат предлагает поездку через две недели на три ночи, без состава — одного взрослого; эти предположения видны в условиях и меняются пользователем. Отсутствие бюджета и города отправления не блокирует поиск жилья. Возраст детей и критичные противоречия уточняются. Режим `guided` сохраняет согласование перед поиском. Даты в интерфейсе — день, месяц, год; API хранит ISO. Поле чата растёт с текстом, Shift+Enter переносит строку, Enter отправляет.

Редактирование числа гостей, дат, бюджета, локаций и дополнительных условий выполняет настоящий JSON Patch. Данные подтверждаются сервером; конфликт ревизий требует повторить действие на свежем состоянии. При изменении требований старые карточки сбрасываются. Переименование чата сохраняет подбор и не вызывает модель повторно.

## Проверки

Python 3.12, Node 22. В отдельном виртуальном окружении из корня:

```sh
pip install -r agent_worker/requirements-dev.txt -r backend/requirements-dev.txt -r data_layer/requirements.txt
```

Задайте `PYTHONPATH` из `.`, `backend`, `agent_worker/src` (разделитель `;` на Windows и `:` на Linux/macOS), затем:

```sh
python -m pytest backend/tests data_layer/tests tests -q
cd agent_worker
python -m pytest -q
ruff check src tests
ruff format --check src tests
cd ../frontend
npm ci
node --experimental-strip-types --test tests/*.test.ts
npm run lint
npm run build
```

Полный backend-тест с одноразовыми MongoDB/RabbitMQ и проверкой ACK/NACK/DLQ worker:

```powershell
./backend/integration_tests/run.ps1 -Python python
```

Скрипт поднимает отдельный Compose project, не запускает агента с платными API и удаляет только свои контейнеры/тома. Проверяет SSE, разграничение доступа, конфликты, повторную доставку и восстановление после сбоев.

Дополнительные **явно запускаемые** сетевые проверки используют локальные ключи и расходуют квоту:

```sh
python scripts/check_providers.py --extract
python scripts/check_providers.py --family
python scripts/check_providers.py --yandex
python scripts/check_conversation.py
python scripts/check_destination_search.py --live
python scripts/check_destination_search.py --replay
python scripts/live_smoke.py --base-url http://127.0.0.1:3000
python scripts/secret_scan.py
```

`live_smoke` регистрирует тестовый аккаунт, выполняет настоящий подбор в Рим и проверяет SSE, смену гостей, конфликт, переименование, refresh/logout. Создаёт `artifacts/live-smoke.json` без токенов и ключей. Ожидает LiteAPI; для демо укажите `--expect-provider ""`. CI проверяет локальные тесты, интеграцию и production build без внешних API-ключей.

## Корпоративный TLS на Windows

Если HTTPS перехватывается доверенным корпоративным прокси и контейнер не доверяет его CA, экспортируйте уже доверенные Windows ROOT сертификаты. Скрипт не меняет хранилище доверия Windows и не отключает проверку TLS:

```powershell
python scripts/export_windows_ca.py
```

Добавьте в **локальный** `.env`:

```dotenv
COMPOSE_FILE=docker-compose.yaml;docker-compose.ca.yaml
COMPOSE_PATH_SEPARATOR=;
```

Overlay монтирует игнорируемый `.local-tools/ca-bundle.pem` read-only только в worker/data-layer. При переносе на другую машину пересоздайте bundle либо уберите overlay, если корпоративный CA не нужен. `.env`, сертификаты и артефакты тестов исключены из Git и Docker build context.

## Материалы

- [Результаты полного аудита и ограничения](docs/READINESS_AUDIT.md)
- [Контракт Agent → Parser](agent_worker/PARSER_CONTRACT.md)
- [Worker](agent_worker/README.md)
- [Backend](backend/README.md)
- [Data layer](data_layer/README.md)

Для публичного развёртывания нужны HTTPS и `COOKIE_SECURE=true`, собственные секреты, ограничение запросов на входе, резервное копирование MongoDB и мониторинг очереди/DLQ. Этот аудит проверяет один локальный Compose-стек, а не нагрузочную устойчивость или доступность внешних поставщиков.


Если именно Docker Desktop не разрешает публичные имена API (при работающем DNS Windows), можно дополнительно включить `docker-compose.network.yaml` в локальный `COMPOSE_FILE`, разделённый `;`. Он задаёт DNS 1.1.1.1/8.8.8.8 только worker/data-layer; адреса можно заменить `TRAVEL_DNS`/`TRAVEL_DNS_SECONDARY` на разрешённые в вашей сети. TLS остаётся включённым. Для обычной сети overlay не требуется.
