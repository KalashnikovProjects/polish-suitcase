# AI-планировщик путешествий

## Запуск всех сервисов

Скопируйте `.env.example` в `.env`, при необходимости замените пароли и токены, затем запустите:

```sh
docker compose up --build -d
docker compose ps
```

Приложение доступно через Caddy по адресу `http://localhost:3000`: страницы и `/api/v1/*` работают на одном адресе. Backend также доступен напрямую на порту `BACKEND_PORT`, Data Layer — на `DATA_LAYER_PORT`. Панель RabbitMQ доступна на порту `RABBITMQ_MANAGEMENT_PORT`.

Для доступа по IP сервера или домену направьте трафик на Caddy. Если нужен адрес без указания порта, задайте `FRONTEND_PORT=80` в `.env` и откройте `http://<IP>/` или `http://<домен>/`. Все запросы браузера к API и SSE используют относительные пути `/api/v1/...`; Caddy направляет их в Backend при любом имени хоста. Сессия браузера привязана к имени хоста, поэтому при переходе с `localhost` на IP или домен потребуется войти снова.

Compose запускает MongoDB и RabbitMQ с постоянными томами, затем Backend, Data Layer, agent-worker, frontend и Caddy. Worker подключается к Backend и RabbitMQ по внутренним именам сервисов; Caddy направляет `/api/v1/*` в Backend, остальные запросы — во frontend. Порт `BACKEND_PORT` меняет только прямой доступ с хоста и не влияет на маршрутизацию через Caddy. По умолчанию `AGENT_MODE=demo` и `DATA_MODE=synthetic`, поэтому для первого запуска внешние API-ключи не нужны. В demo-режиме worker использует встроенные демонстрационные данные; при `AGENT_MODE=openai` он обращается к Data Layer.

Для реального агента задайте в `.env` `AGENT_MODE=openai`, `OPENAI_API_KEY` и `PARSER_SERVICE_TOKEN`. Режим данных выбирается через `DATA_MODE`; ключи внешних источников указываются в том же файле. При доступе через Caddy менять `CORS_ORIGINS` для IP или домена не требуется: запросы браузера идут на тот же адрес.
`DATA_MODE` режим источников: `sandbox` `live` `synthetic`

## Как проверить доставку сообщения агенту

```sh
docker compose logs -f backend agent-worker
docker compose exec rabbitmq rabbitmqctl list_queues name messages_ready messages_unacknowledged consumers
```

В логах по `chat_id` видна цепочка `message_saved` → `task_published` → `delivery_started` → `sse_initialized` → `planner_started` → `agent_message_accepted` → `delivery_acked`. Если агент ждёт уточнения, появится `session_waiting_user`: счётчик `messages_unacknowledged` в RabbitMQ останется положительным, но worker продолжит обрабатывать другие чаты. Одновременно он держит до `AGENT_MAX_CONCURRENT_CHATS` чатов (по умолчанию 32). При перезапуске Backend строка `unfinished_tasks_replayed` означает повторную публикацию незавершённых задач после возможной потери сообщений брокером. Тексты сообщений и токены в этих логах не выводятся.

Остановить стек без удаления данных:

```sh
docker compose down
```
