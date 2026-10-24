# Backend планировщика путешествий

В этом репозитории реализуется Backend на FastAPI. Frontend и AI-агент разрабатываются отдельно. Backend хранит состояние чатов в MongoDB, публикует `chat_id` в RabbitMQ и предоставляет REST и SSE по спецификациям в `sketches/`.

## Локальный запуск

1. Скопировать `.env.example` в `.env` и заменить пароли и токены.
2. Запустить `docker compose up --build`.
3. Проверить `GET http://localhost:8000/healthz` и документацию `http://localhost:8000/docs`.

Docker Compose запускает только Backend, MongoDB и RabbitMQ. Тестовый или рабочий агент подключается отдельно. RabbitMQ Management UI доступен на `http://localhost:15672` с учётными данными из `.env`.

Если Frontend работает на другом адресе, укажите его origin в `CORS_ORIGINS`. Для HTTPS установите `COOKIE_SECURE=true`.

Backend принимает агентский Bearer-токен из `AGENT_SERVICE_TOKEN`. Агенту доступны `GET /api/v1/chats/{chat_id}/events`, `POST /api/v1/chats/{chat_id}/messages` и `POST /internal/v1/chats/{chat_id}/agent`. Агент получает из RabbitMQ только `{"chat_id": ...}` и сам отправляет ACK после успешного ответа `completed`.

## Контракт

- Пользователь регистрируется и получает JWT через `/api/v1/auth/*`. Access token живёт 30 минут, refresh token хранится в HttpOnly cookie. Для браузерного SSE дополнительно устанавливается HttpOnly cookie доступа.
- Чат создаётся вместе с первым сообщением через `POST /api/v1/chats`.
- `POST /api/v1/chats/{chat_id}/messages` принимает текст или `operations`. При операциях обязателен `from_revision`. Ответ содержит созданную запись истории и актуальную `revision`.
- JSON Patch поддерживает `add`, `remove`, `replace`. Элементы массивов с `id` адресуются по ID, добавление в конец — через `/-`. Пакет применяется целиком или отклоняется.
- SSE всегда начинается с `init_info` и затем передаёт `new_message`, `changed_travel`, `agent_info` в порядке сохранения. `additional_hidden` видит только сервисный агент.
- При `completed` Backend атомарно сверяет `last_processed_message_id` с последним пользовательским элементом истории. Если пришёл новый ввод, отвечает `409 new_messages`.

## Проверка

Зависимости для тестов перечислены в `backend/requirements-dev.txt`. После их установки:

```powershell
$env:PYTHONPATH='backend'
python -m pytest backend/tests -q
docker compose config --quiet
```

## Границы MVP

Backend рассчитан на один процесс FastAPI. История и журнал SSE-событий хранятся в документе чата, чтобы состояние, сообщение и события сохранялись атомарно. Для очень длинных чатов потребуется вынести историю из документа из-за ограничения размера MongoDB. Публикация задания восстановима через сохранённый `job_state=pending` и повторную попытку с Publisher Confirm; после сбоя между подтверждением RabbitMQ и отметкой в MongoDB возможна повторная доставка `chat_id`, поэтому агент должен учитывать уже сохранённые результаты.
