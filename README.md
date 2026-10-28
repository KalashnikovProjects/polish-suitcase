<div align="center">

# 🧭 Polish Notation

### Путешествие начинается с разговора

Опишите, как хотите отдохнуть, — AI-планировщик подберёт направления,<br>
найдёт жильё и дорогу, а затем поможет уточнить детали в чате.

<p>
  <img src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white" alt="Python 3.12">
  <img src="https://img.shields.io/badge/Next.js-Frontend-111111?logo=nextdotjs&logoColor=white" alt="Frontend на Next.js">
  <img src="https://img.shields.io/badge/LangGraph-AI_Agent-1C3C3C" alt="AI-агент на LangGraph">
  <img src="https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white" alt="Запуск через Docker Compose">
</p>

**💬 Идея поездки → 🗺️ Направления → 🏨 Жильё и 🚆 Дорога → ✨ Ваши варианты**

[Возможности](#features) · [Быстрый старт](#quick-start) · [Архитектура](#architecture) · [Документация](#docs)

</div>

---

<a id="features"></a>

## ✨ Что умеет планировщик

| 💬 В диалоге | 🧳 В подборе |
| --- | --- |
| **Собирает пожелания.** Учитывает цель отдыха, даты, бюджет, состав группы и дополнительные условия. | **Предлагает направления.** Выбирает города под запрос и сезон, затем сравнивает доступные варианты. |
| **Помнит контекст.** Понимает короткие ответы и уточнения: «из Москвы», «добавь ребёнка», «давай дешевле». | **Ищет во внешних источниках.** Получает жильё через LiteAPI, дорогу — через Яндекс Расписания. |
| **Помогает менять планы.** Сохраняет правки параметров и обновляет результат через SSE. | **Показывает условия.** Отделяет известные цены и ограничения от того, что ещё требует проверки. |

> 💡 **Попробуйте начать так:** «Хочу на три ночи в ноябре, выезд из Москвы, двое взрослых, бюджет до 90 000 ₽. Предложи несколько направлений».

Проект развивается как **MVP планирования путешествий**. Реальное бронирование и оплата пока не подключены.

<a id="quick-start"></a>

## 🚀 Быстрый старт

Понадобятся **Git**, **Docker Desktop / Docker Engine с Compose v2** и ключи трёх API.

### 1. Скачайте проект

```sh
git clone --branch main https://github.com/ostrovok-hackathon-2026/polish-notation.git
cd polish-notation
cp .env.example .env
```

Репозиторий приватный — используйте GitHub-аккаунт с доступом к проекту.

### 2. Настройте окружение

Заполните ключи в локальном `.env`:

| Переменная | Для чего нужна |
| --- | --- |
| `OPENAI_API_KEY` | Понимание запросов и объяснение результатов |
| `LITEAPI_KEY` | Поиск жилья и тарифов |
| `YANDEX_RASP_API_KEY` | Поиск расписаний транспорта |

Задайте собственные пароли и сервисные токены из [`.env.example`](.env.example). Ключи хранятся локально; `.env` исключён из Git.

### 3. Запустите сервисы

```sh
docker compose up --build -d
docker compose ps
```

Откройте **[localhost:3000](http://localhost:3000)** — интерфейс готов к работе, когда сервисы прошли проверку готовности.

> 🔌 **Основной запуск использует внешние API:** `AGENT_MODE=openai` и `DATA_MODE=live` заданы в Compose. Старые значения `demo` / `synthetic` в `.env` их не переопределяют. Без обязательных ключей сервисы останавливаются до обработки чатов. Ошибки внешнего поиска не подменяются синтетическими предложениями; старый демонстрационный кеш не используется.

<details>
<summary><strong>🎭 Запуск демонстрации без внешних API</strong></summary>

Для отдельного офлайн-сценария явно подключите демонстрационный файл Compose:

```sh
docker compose -f docker-compose.yaml -f docker-compose.demo.yaml up --build -d
```

В этом режиме понимание запросов и предложения демонстрационные. Backend, MongoDB и RabbitMQ продолжают работать как обычные сервисы.

</details>

<details>
<summary><strong>🔄 Обновление и остановка</strong></summary>

После получения изменений из `main` пересоберите агент и слой данных:

```sh
docker compose up -d --build agent-worker data-layer
```

Остановка с сохранением данных:

```sh
docker compose down
```

Команда `docker compose down -v` удаляет данные томов. Используйте её только для одноразового тестового стека.

</details>

## 🖥️ Адреса сервисов

| Сервис | Адрес по умолчанию |
| --- | --- |
| 💬 Веб-интерфейс | [localhost:3000](http://localhost:3000) |
| ⚙️ Backend · healthcheck | [localhost:8000/healthz](http://localhost:8000/healthz) |
| 🔎 Data layer · healthcheck | [localhost:8080/health](http://localhost:8080/health) |
| 📨 RabbitMQ · management | [localhost:15672](http://localhost:15672) |

Порты задаются в `.env` и привязаны к loopback. При смене `FRONTEND_PORT` обновите `CORS_ORIGINS`. `BACKEND_INTERNAL_URL` — адрес backend внутри Docker. Next.js проксирует `/api/v1/*`, cookies и SSE через адрес интерфейса; Caddy для локального запуска не требуется.

<a id="architecture"></a>

## 🧩 Как устроен проект

Compose поднимает **шесть сервисов**:

| Компонент | Роль |
| --- | --- |
| **Frontend · Next.js** | Чат, параметры путешествия и карточки вариантов |
| **Backend · FastAPI** | Авторизация, состояние чата, API и обновления через SSE |
| **Agent worker · LangGraph** | Понимание запроса, планирование поиска и сборка результата |
| **Data layer** | Единый интерфейс к LiteAPI, Яндекс Расписаниям и геопоиску |
| **MongoDB** | Хранение состояния и истории |
| **RabbitMQ** | Очередь задач для агента |

Агент получает актуальное состояние из backend, обращается к слою данных и сохраняет результат через API. Структура чата описана в [`sketches/chat_struct.json`](sketches/chat_struct.json), взаимодействие агента со слоем данных — в [контракте Agent → Parser](agent_worker/PARSER_CONTRACT.md).

```text
polish-notation/
├── frontend/       # Веб-интерфейс
├── backend/        # API, хранение и события
├── agent_worker/   # AI-агент и планирование
├── data_layer/     # Адаптеры внешних источников
├── tests/          # Межкомпонентные проверки
├── scripts/        # Диагностика и сценарии проверки
├── sketches/       # Эскизы структуры данных
└── docs/           # Аудит и технические материалы
```

## 🌍 Источники и режимы работы

| Источник | Что даёт проекту | Что учитывать |
| --- | --- | --- |
| 🧠 **OpenAI** | Извлечение требований, выбор направлений и объяснение результата | Модель и reasoning effort настраиваются отдельно |
| 🏨 **LiteAPI** | Жильё и тарифы через `/hotels/rates`, поиск по координатам | Фактический sandbox / live определяется ключом и ответом |
| 🚆 **Яндекс Расписания** | Коды городов и прямые сегменты дороги в обе стороны | Расписание не подтверждает места, багаж или конечный тариф |
| 🗺️ **GeoNames / Open-Meteo** | Города, координаты, страна и IANA timezone за пределами встроенного справочника | Геопоиск не гарантирует предложения поставщиков и не является прогнозом погоды |

Географические данные: [GeoNames](https://www.geonames.org/) через [Open-Meteo Geocoding API](https://open-meteo.com/en/docs/geocoding-api). Публичный endpoint используется для некоммерческого MVP; коммерческому проекту нужен соответствующий тариф или свой геокодер.

<details>
<summary><strong>⚙️ Режимы и дополнительные настройки</strong></summary>

| Настройка | Поведение |
| --- | --- |
| `AGENT_MODE=openai` | OpenAI для понимания текста и объяснений, data layer для поиска |
| `AGENT_MODE=demo` | Детерминированный агент и встроенные демонстрационные предложения |
| `DATA_MODE=live`, `sandbox`, `live_transport` | Жильё через LiteAPI и дорога через Яндекс; нужны оба ключа |
| `DATA_MODE=synthetic` | Версионированные синтетические фикстуры |

Для прямого запуска Python вне Compose задайте `AGENT_MODE=openai`, `DATA_MODE=live` и три API-ключа. В основном Compose режимы зафиксированы; демонстрация включается отдельным файлом.

- `OPENAI_MODEL` и `OPENAI_REASONING_EFFORT` управляют настройками модели.
- `AGENT_MAX_CONCURRENT_CHATS` ограничивает число параллельных активных чатов; по умолчанию — `32`.
- `GUEST_NATIONALITY` по умолчанию — `RU`. Для другого гражданства измените настройку.

Режимы `sandbox` и `live_transport` также вызывают оба внешних API. Неудачный поиск не заменяется синтетическими ценами.

</details>

## 💬 Как агент ведёт подбор

- **Отталкивается от цели поездки.** Превращает запрос в цель отдыха и сезонный список городов, сохраняет цель при изменении параметров. Проверяет до шести направлений в пределах 24 логических запросов и прекращает расширение после трёх подходящих городов. Сначала сравнивает разные города, затем альтернативы внутри одного места.
- **Использует историю разговора.** Учитывает короткие ответы вроде «и то и то» и изменения состава группы. В режиме `guided` согласует поиск с пользователем.
- **Делает предположения видимыми.** Без дат предлагает поездку через две недели на три ночи, без состава — одного взрослого. Пользователь может изменить эти условия. Бюджет и город отправления не обязательны для начала поиска жилья; возраст детей и критичные противоречия уточняются.
- **Сохраняет изменения.** Гости, даты, бюджет, локации и дополнительные условия обновляются через JSON Patch с подтверждением сервера. Конфликт ревизий требует повторить действие на свежем состоянии. При изменении требований старые карточки сбрасываются; переименование чата сохраняет подбор и не вызывает модель повторно.

⌨️ **В чате:** `Enter` отправляет сообщение, `Shift + Enter` переносит строку, поле ввода растёт вместе с текстом. Даты отображаются как день, месяц и год; API хранит их в ISO-формате.

### 📌 Границы текущего MVP

Неизвестная цена остаётся неизвестной, а взрослые справочные тарифы не переносятся на детей. Трансферы пока не реализованы. Если один источник недоступен, возможны отдельные варианты жилья или расписания с указанием неполной стоимости. Ошибка или неподдержанный источник не трактуются как отсутствие билетов. Покрытие зависит от поставщиков; перед бронированием условия требуют проверки.

## 🧪 Проверки и разработка

Для локальной разработки используются **Python 3.12** и **Node.js 22**. CI проверяет локальные тесты, интеграцию и production build без ключей внешних API.

<details>
<summary><strong>🔬 Локальные тесты, lint и сборка</strong></summary>

Создайте отдельное виртуальное окружение и из корня установите зависимости:

```sh
pip install -r agent_worker/requirements-dev.txt -r backend/requirements-dev.txt -r data_layer/requirements.txt
```

Добавьте `.`, `backend`, `agent_worker/src` в `PYTHONPATH`: разделитель `:` на Linux/macOS и `;` на Windows. Затем выполните:

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

</details>

<details>
<summary><strong>🔗 Интеграция с MongoDB и RabbitMQ</strong></summary>

Из корня репозитория:

```powershell
./backend/integration_tests/run.ps1 -Python python
```

Скрипт поднимает отдельный Compose project с одноразовыми MongoDB и RabbitMQ, проверяет SSE, разграничение доступа, конфликты, ACK/NACK/DLQ, повторную доставку и восстановление после сбоев. Он не запускает агента с платными API и удаляет только свои контейнеры и тома.

</details>

<details>
<summary><strong>🌐 Проверки внешних API и полного сценария</strong></summary>

Эти проверки запускаются **явно**, используют локальные ключи и расходуют квоту:

```sh
python scripts/check_providers.py --extract
python scripts/check_providers.py --family
python scripts/check_providers.py --yandex
python scripts/check_conversation.py
python scripts/check_destination_search.py --live
python scripts/live_smoke.py --base-url http://127.0.0.1:3000
```

`live_smoke` регистрирует тестовый аккаунт, выполняет подбор в Рим и проверяет SSE, смену гостей, конфликт, переименование, refresh/logout. Результат сохраняется в `artifacts/live-smoke.json` без токенов и ключей. Проверка ожидает LiteAPI; для демонстрации укажите `--expect-provider ""`.

Дополнительная диагностика:

```sh
python scripts/check_destination_search.py --replay
python scripts/secret_scan.py
```

</details>

## 🛠️ Настройка окружения

<details>
<summary><strong>🔐 Корпоративный TLS на Windows</strong></summary>

Если HTTPS перехватывается доверенным корпоративным прокси и контейнер не доверяет его CA, экспортируйте уже доверенные Windows ROOT сертификаты:

```powershell
python scripts/export_windows_ca.py
```

Скрипт не меняет хранилище доверия Windows и не отключает проверку TLS. Добавьте в **локальный** `.env`:

```dotenv
COMPOSE_FILE=docker-compose.yaml;docker-compose.ca.yaml
COMPOSE_PATH_SEPARATOR=;
```

Overlay монтирует игнорируемый `.local-tools/ca-bundle.pem` только для чтения в worker и data layer. На другой машине пересоздайте bundle либо уберите overlay, если корпоративный CA не нужен. `.env`, сертификаты и артефакты тестов исключены из Git и Docker build context.

</details>

<details>
<summary><strong>🌐 DNS внутри Docker Desktop</strong></summary>

Если именно Docker Desktop не разрешает публичные имена API при работающем DNS Windows, подключите `docker-compose.network.yaml` в локальный `COMPOSE_FILE`, разделяя файлы через `;`.

Overlay задаёт DNS `1.1.1.1` / `8.8.8.8` только worker и data layer. Их можно заменить через `TRAVEL_DNS` / `TRAVEL_DNS_SECONDARY` на разрешённые в вашей сети адреса. TLS остаётся включённым; для обычной сети этот overlay не требуется.

</details>

<details>
<summary><strong>☁️ Публичное развёртывание</strong></summary>

Для публичного стенда нужны HTTPS и `COOKIE_SECURE=true`, собственные секреты, ограничение входящих запросов, резервное копирование MongoDB и мониторинг очереди/DLQ. Локальный аудит одного Compose-стека не подтверждает нагрузочную устойчивость или доступность внешних поставщиков.

</details>

<a id="docs"></a>

## 📚 Документация

| Материал | Что внутри |
| --- | --- |
| [🧠 Agent worker](agent_worker/README.md) | Запуск, граф агента, память и протокол работы |
| [🔌 Контракт Agent → Parser](agent_worker/PARSER_CONTRACT.md) | HTTP-контракт и форматы предложений |
| [⚙️ Backend](backend/README.md) | API, хранение состояния и события |
| [🔎 Data layer](data_layer/README.md) | Источники данных и адаптеры |
| [🧱 Структура чата](sketches/chat_struct.json) | Эскиз полей, вариантов и истории; содержит комментарии |
| [📋 Аудит готовности](docs/READINESS_AUDIT.md) | Результаты проверок и ограничения |

---

<div align="center">

**🧭 От идеи поездки — к вариантам, которые удобно сравнить.**

Проект команды **Polish Notation** · Ostrovok Hackathon 2026

</div>
