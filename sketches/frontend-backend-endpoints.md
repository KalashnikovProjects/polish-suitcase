Протоколы: REST (JSON) и SSE (Server-Sent Events).

Основные HTTP-коды:

| Код   | Значение                 |
| ----- | ------------------------ |
| `200` | Успешный запрос          |
| `201` | Объект создан            |
| `204` | Успешно, без тела ответа |
| `400` | Некорректный запрос      |
| `401` | Не авторизован           |
| `403` | Недостаточно прав        |
| `404` | Объект не найден         |
| `409` | Конфликт ревизий         |
| `422` | Ошибка валидации         |
| `500` | Ошибка сервера           |

```
{
  "error": {
    "code": "version_conflict",
    "message": "Chat state has changed",
    "details": {
      "expected_revision": 19,
      "current_revision": 20
    }
  }
}
```

# 2. Авторизация

Для запросов кроме аутентификации нужен токен в заголовках HTTP запроса:

```
Authorization: Bearer <access_token>
```

Access token — JWT, время жизни 30 минут. Refresh token хранится в `HttpOnly` cookie.

| Метод  | Endpoint         | Назначение          |
| ------ | ---------------- | ------------------- |
| `POST` | `/auth/register` | Регистрация         |
| `POST` | `/auth/login`    | Авторизация         |
| `POST` | `/auth/refresh`  | Обновление токена   |
| `POST` | `/auth/logout`   | Выход               |
| `GET`  | `/users/me`      | Данные пользователя |

## POST `/api/v1/auth/register`

Запрос:

```
{
  "email": "user@example.com",
  "password": "password123"
}
```

Ответ `201`:

```
{
  "id": 123,
  "email": "user@example.com",
  "created_at": "2026-10-24T14:00:00Z"
}
```

## POST `/api/v1/auth/login`

Запрос:

```
{
  "email": "user@example.com",
  "password": "password123"
}
```

Ответ `200`:

```
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 1800,
  "user": {
    "id": 123,
    "email": "user@example.com"
  }
}
```

Refresh token устанавливается через `Set-Cookie`.

## POST `/api/v1/auth/refresh`

Тело отсутствует. Refresh token передаётся браузером через cookie.

Ответ `200`:

```
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 1800
}
```

## POST `/api/v1/auth/logout`

Ответ `204 No Content`. Refresh-сессия отзывается.

## GET `/api/v1/users/me`

Ответ `200`:

```
{
  "id": 123,
  "email": "user@example.com",
  "created_at": "2026-10-24T14:00:00Z"
}
```

# 3. Чаты

| Метод    | Endpoint           | Назначение                 |
| -------- | ------------------ | -------------------------- |
| `GET`    | `/chats`           | Список чатов               |
| `POST`   | `/chats`           | Создание чата              |
| `GET`    | `/chats/{chat_id}` | Полное публичное состояние |
| `DELETE` | `/chats/{chat_id}` | Удаление чата              |
Удаление чата для MVP не срочно реализовывать.

## GET `/api/v1/chats`

Параметры: `limit` (по умолчанию 30)

Ответ `200`:

```
[
  {
      "id": 1,
      "name": "Путешествия на ноябрь",
      "updated_at": "2026-10-24T14:30:00Z"
    }
]
```

## POST `/api/v1/chats`
Чат как в чате гпт создаётся после написания первого сообщения

Запрос:

```
{
  "name": "Путешествия на ноябрь",
  "message": "Первое сообщение от пользователя",
}
```

Ответ `201`:

```
{
  "id": 1,
  "revision": 1,
  "name": "Путешествия на ноябрь"
}
```

## GET `/api/v1/chats/1`

Возвращает полную структуру данных чата.

Ответ `200`:

```
тут полная структура
```

Поле `additional_hidden` отсутствует.

## DELETE `/api/v1/chats/1`

Ответ `204 No Content`.

# 4. Сообщения и операции

| Метод  | Endpoint                    | Назначение                                       |
| ------ | --------------------------- | ------------------------------------------------ |
| `GET`  | `/chats/{chat_id}/messages` | Получение истории                                |
| `POST` | `/chats/{chat_id}/messages` | Отправка сообщения и отправка изменений кнопками |

## GET `/api/v1/chats/1/messages`

Параметры:

```
GET /api/v1/chats/1/messages
```

Ответ:

```
  [
    {
      "id": 1234,
      "from": "user",
      "message": "Хочу в Уфу"
    },
    {
      "id": 1235,
      "from": "user-action",
      "operations": [
        {
          "op": "replace",
          "path": "/basic/dates/from",
          "value": "29.10.2026"
        }
      ]
    }
  ]
```

## POST `/api/v1/chats/1/messages`

### Обычное сообщение

```
{
  "message": "Хочу в уху"
}
```

### Изменение параметров

```
{
  "operations": [
    {
      "op": "replace",
      "path": "/basic/dates/from",
      "value": "29.10.2026"
    },
    {
      "op": "remove",
      "path": "/basic/locations_tree/Европа"
    }
  ]
}
```

### Изменение поля из `additional`

```
{
  "operations": [
    {
      "op": "replace",
      "path": "/additional/group_city_restrictions/fields/f1/value",
      "value": [
        "Ограничен въезд в Москву"
      ]
    }
  ]
}
```

Здесь используется спецификация JSON Patch (RFC 6902). 

!!!!! НО когда тебе нужно элемент списка изменить, ты обращаешься не по его индексу типа 0, 1, 2, а по id внутри элемента.

"/additional/group_city_restrictions/fields/f1/value" - например тут это group_city_restrictions вместо 0 и f1 вместо 0

### Ответ `201`

```
{
  "id": 1237,
  "from": "user-action",
  "operations": [
    {
      "op": "replace",
      "path": "/basic/dates/from",
      "value": "29.10.2026"
    }
  ],
  "created_at": "2026-10-24T14:31:00Z"
}
```

- `id`, `from` и `created_at` устанавливает сервер.

# 5. SSE (типа вебсокета) — события чата

## GET `/api/v1/chats/{chat_id}/events`

Frontend подписывается на события конкретного чата.

Ответ:

```
Content-Type: text/event-stream
Cache-Control: no-cache
```

В браузере нативный `EventSource` использует cookie-аутентификацию, поскольку не поддерживает установку произвольного заголовка `Authorization`.

Для SSE Backend предоставляет защищённую сессионную cookie. Если используется только Bearer JWT, потребуется fetch-based SSE-клиент вместо нативного `EventSource`.

### Типы событий

| Событие          | Когда отправляется                                | Данные                                               |
| ---------------- | ------------------------------------------------- | ---------------------------------------------------- |
| `init_info`      | При подключении или полной ресинхронизации        | Полный публичный JSON чата                           |
| `new_message`    | Добавлено сообщение или пользовательское действие | Элемент `chat.history`                               |
| `changed_travel` | Изменилось состояние поездки                      | Полные `basic`, `variants`, `additional`, `revision` |
| `agent_info`     | Изменился статус агента                           | Полный объект `agent`                                |

## `init_info`

```
event: init_info
id: 100
data: {"id":1,"revision":19,"chat":{"name":"Путешествия на ноябрь","history":[]},"basic":{},"variants":[],"additional":[],"agent":{"status":"working","status-message":"Поиск отелей..."}}
```

Это сокращённый пример. Реально `init_info` передаёт полный публичный JSON чата, включая историю.

После получения `init_info` Frontend заменяет всё локальное состояние чата.

## `new_message`

```
event: new_message
id: 101
data: {"id":1237,"from":"agent","message":"Нашёл гостиницу","operations":[{"op":"replace","path":"/basic/cost_range","value":"35000–42000 ₽"}]}
```

Frontend добавляет сообщение в `chat.history`, если сообщения с таким ID ещё нет.

Операции из `new_message` не нужно применять к локальному состоянию. Для обновления состояния используется отдельное событие `changed_travel`.

## `changed_travel`

```
event: changed_travel
id: 102
data: {"revision":20,"basic":{},"variants":[],"additional":[]}
```

Реально передаются полные `basic`, `variants`, `additional`.

Frontend заменяет эти три поля, если полученная `revision` больше локальной.

## `agent_info`

```
event: agent_info
id: 103
data: {"status":"working","status-message":"Сравниваю гостиницы..."}
```

Возможные статусы:

| Статус         | Значение                    |
| -------------- | --------------------------- |
| `idle`         | Агент не работает           |
| `working`      | Агент выполняет задачу      |
| `waiting_user` | Ожидает ответа пользователя |
| `completed`    | Работа завершена            |
| `error`        | Ошибка выполнения           |

### Пример подписки

```
const source = new EventSource(
    "/api/v1/chats/1/events",
    { withCredentials: true }
);

source.addEventListener("init_info", (event) => {
    const chat = JSON.parse(event.data);
    setChat(chat);
});

source.addEventListener("new_message", (event) => {
    const message = JSON.parse(event.data);

    setChat(prev => ({
        ...prev,
        chat: {
            ...prev.chat,
            history: prev.chat.history.some(
                item => item.id === message.id
            )
                ? prev.chat.history
                : [...prev.chat.history, message]
        }
    }));
});

source.addEventListener("changed_travel", (event) => {
    const update = JSON.parse(event.data);

    setChat(prev => {
        if (!prev || update.revision <= prev.revision) {
            return prev;
        }

        return {
            ...prev,
            revision: update.revision,
            basic: update.basic,
            variants: update.variants,
            additional: update.additional
        };
    });
});

source.addEventListener("agent_info", (event) => {
    const agent = JSON.parse(event.data);

    setChat(prev => ({
        ...prev,
        agent
    }));
});
```


# 6. Правила отображения данных

## `basic`

Все поля доступны для редактирования пользователем, если интерфейс предусматривает соответствующий элемент управления.

- `departure` — строка свободного формата.
- `dates.from`, `dates.to` — короткие строки свободного формата, `"-"` при отсутствии.
- `peoples.adults`, `children`, `unknown` — целые неотрицательные числа.
- `peoples.additional-info` — массив строк, редактируемый как теги.
- `price_restrictions.min`, `max` — числовые значения, `-1` означает отсутствие ограничения.
- `cost_range` — строка, которую уточняет агент.
- `locations_tree` — дерево произвольной глубины; пустой объект `{}` означает конечную ветвь.

При изменении дерева локаций Frontend отправляет JSON Patch по конкретному пути, например:

```
{
  "op": "add",
  "path": "/basic/locations_tree/Россия/Кавказ/Сочи",
  "value": {}
}
```

Для этого родительская ветвь `/Россия/Кавказ` уже должна существовать. При необходимости сначала создаётся родитель.

Если название локации содержит `~` или `/`, сегмент JSON Pointer необходимо экранировать по RFC 6901: `~` → `~0`, `/` → `~1`.

## `variants`

Массив независимых вариантов путешествия, только для просмотра.

`completed: false` означает, что агент ещё исследует или заполняет вариант.

`completed: true` означает, что агент закончил работу над вариантом, но не гарантирует актуальность цен, доступность бронирования или полноту внешних данных.

`habitations` — список мест проживания в рамках одного варианта, а не альтернативы для выбора.

`transports` — список транспортных этапов.

`urls` — список источников.

`additional` внутри варианта, проживания и транспорта — список произвольных пар `{label, value}` для отображения. Эти поля пользователь не редактирует.

## `additional`

Массив групп пользовательских полей.

Каждая группа:

```
{
  "id": "group_city_restrictions",
  "title": "Ограничения по городам",
  "fields": [
    {
      "id": "f1",
      "type": "number",
      "label": "Поле 1",
      "value": 12345
    },
    {
      "id": "f2",
      "type": "tags",
      "label": "Поле 2",
      "value": ["Ограничен въезд в Тверь"]
    }
  ]
}
```

Frontend строит интерфейс по `type`.

Минимально поддерживаемые типы в v1:

| Type      | Значение `value` | UI                 |
| --------- | ---------------- | ------------------ |
| `number`  | Число            | Числовое поле      |
| `tags`    | Массив строк     | Редактируемые теги |
| `text`    | Строка           | Текстовое поле     |
| `boolean` | Boolean          | Переключатель      |

Frontend может изменять `value`, но не `id`, `type`, `label` или структуру группы. Создание и изменение структуры дополнительных полей выполняет агент.

# 7. Сценарии работы Frontend

## Открытие чата

1. Пользователь выбирает чат.
2. Frontend подключается к `/chats/{id}/events`.
3. Получает `init_info` и отображает полное состояние.
4. Далее получает `new_message`, `changed_travel`, `agent_info`.
5. При потере соединения SSE переподключается автоматически.

## Отправка сообщения

1. Пользователь вводит текст.
2. Frontend генерирует `request_id`.
3. Отправляет `POST /chats/{id}/messages`.
4. После `201` сообщение считается сохранённым.
5. По SSE приходит `new_message`; дубликат по ID не добавляется.
6. Backend самостоятельно инициирует работу агента.

## Изменение параметров

1. Пользователь изменяет поле.
2. Frontend формирует JSON Patch по текущему состоянию.
3. Отправляет `POST /messages` с `expected_revision`.
4. При `201` ожидает `changed_travel` или использует ответ как подтверждение сохранения.
5. При `409` получает актуальное состояние и предлагает повторить изменение либо пересоздаёт патч относительно нового состояния.

## Работа агента

1. По SSE приходит `agent_info` со статусом `working`.
2. Агент постепенно добавляет сообщения и обновляет состояние.
3. Frontend получает `new_message` и `changed_travel`.
4. При `completed` интерфейс показывает завершение работы.
5. При `waiting_user` интерфейс ожидает пользовательского ответа.
6. При `error` отображается сообщение об ошибке.

# 8. Итоговый список endpoints

POST   /api/v1/auth/register POST   /api/v1/auth/login POST   /api/v1/auth/refresh POST   /api/v1/auth/logout GET    /api/v1/users/me  GET    /api/v1/chats POST   /api/v1/chats GET    /api/v1/chats/{chat_id} DELETE /api/v1/chats/{chat_id}  GET    /api/v1/chats/{chat_id}/messages POST   /api/v1/chats/{chat_id}/messages  GET    /api/v1/chats/{chat_id}/events

Главные договорённости для Frontend: использовать именно финальные имена полей (`revision`, `price_restrictions`, `cost_range`, `additional_hidden`), не изменять `variants` кнопками, работать с массивами по индексам JSON Patch, не применять операции из `new_message` повторно и считать `init_info`/`changed_travel` авторитетными снимками состояния.
