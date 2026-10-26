# Аудит контрактов Backend — 26.10.2026

Проверены `sketches/frontend-backend-endpoints.md`, `sketches/backend-agent-endpoints.md` и пример `sketches/chat_struct.json`. Frontend и Agent Worker находятся вне области этого Backend. `chat_struct.json` содержит комментарии и является примером структуры, а не машинно читаемой JSON Schema.

Итог: основные интерфейсы и сценарии подтверждены тестами, но строгое требование одного активного агента на чат нельзя гарантировать без идентификатора попытки или механизма fencing в протоколе записи агента. Ниже приведены покрытие контрактов и оставшиеся ограничения.

## Contract mapping

| Requirement | Implementation | Verification |
| --- | --- | --- |
| Register, login, refresh rotation, logout, current user | `app/auth.py`, `app/main.py` | API tests and live MongoDB auth/rotation test |
| Chat creation with first message, list, full read, delete, owner isolation | `app/main.py`, `app/storage.py` | API tests and live owner-isolation test |
| Public state shape: `chat.name`, root `history`, `basic`, `variants`, `additional`, `revision`, `agent` | `initial_travel`, `chat_view` | API tests and live SSE snapshots |
| Private `additional_hidden` for Agent only | `chat_view`, `event_view`, `public_message` | API tests and simultaneous Agent/Frontend SSE test |
| User text and actions, Agent text and operations, full created-record response | `post_message`, `add_message` | API tests and live protocol flow |
| `from_revision` only for nonempty operations; 409 on stale revision | `post_message`, `add_message` | API tests and live concurrent CAS test |
| Atomic JSON Patch; ID addressing for identified arrays; indices for arrays without IDs | `app/patches.py`, single-document Mongo CAS | Patch tests and live user action / invalid batch tests |
| Full SSE `init_info`, ordered events, heartbeat, reconnect snapshot | `events`, persisted `event_seq` | Live SSE tests, including reconnect and heartbeat |
| `working`, `waiting_user`, `completed`, `error`; completion checks last user item | `update_agent` | API tests and live 409/completed flow |
| Durable quorum queue, persistent `chat_id` only, confirms, retry and DLQ | `app/publisher.py`, `rabbitmq.conf` | Live publication, ACK, redelivery, broker outage, and DLQ tests |
| Survive Backend restart and recover pending publication | Mongo state and Publisher loop | Live restart and broker-outage tests |

## Changes made during this audit

- Added RFC 6902 index addressing for arrays without IDs, including editable tags, while retaining ID addressing for `variants`, `habitations`, `transports`, `additional` groups and their `fields`.
- Allowed explicit `last_processed_message_id: null` only in a `completed` request; the atomic latest-message check still rejects it if a user item exists.
- Rejected nonfinite values, integers outside BSON's signed 64-bit range, NUL object keys and paths, excessively nested JSON, and operation bodies above 256 KiB before MongoDB writes.
- Added a JSON envelope for unhandled HTTP 500 responses; the server logs the internal exception.
- Made SSE heartbeat and polling intervals configurable and close user streams when their JWT expires.
- Kept the Pika connection active by servicing connection events while the Publisher is idle.
- Required configured JWT and Agent secrets at startup, and set the local RabbitMQ consumer timeout to 12 hours.
- Added an isolated Compose integration runner and live tests with real HTTP, SSE, MongoDB, RabbitMQ, restart, concurrency, redelivery, DLQ, privacy and authorization.

## Contract ambiguities and remaining limits

1. **Exactly one active Agent per chat cannot be guaranteed with the current external protocol.** RabbitMQ can redeliver `chat_id`, and the same service token is shared by all workers. The Backend has no worker-specific credential on REST requests, so it cannot fence an old worker after a duplicate delivery. The persisted `job_state` and `job_generation` prevent routine duplicate publication and stale publication marking, but a crash between broker confirm and MongoDB marking can still produce duplicate messages. A strict guarantee requires a worker claim/lease with a fencing value carried on Agent REST writes, or an equivalent change to the Agent protocol.
2. **After a task reaches the DLQ, the Backend's `job_state` can remain `queued` or `working`.** DLQ delivery is verified, but automatic reconciliation back into the chat state is not part of the current contract. Operations staff must inspect and retry or close failed tasks; a production design needs a defined recovery policy.
3. **Long chats eventually hit MongoDB's document limit.** History and full SSE snapshots are stored in one document for atomicity. This also makes polling and mutation cost grow with the entire chat. Production-scale history needs a storage redesign, with a defined snapshot/resynchronization policy.
4. **Agent and Frontend implementations are not included.** The integration suite acts as both clients and verifies the Backend protocol. It does not prove their own cancellation, ACK discipline, rendering or parser behavior.
5. **The sketches contain two stale/ambiguous examples.** The final sentence of the Frontend contract says array indices, while its detailed examples and the Agent contract require IDs for arrays of objects. The annotated `chat_struct.json` also shows `/basic/cost-range` in one comment while the final field name is `cost_range`. The Backend follows the explicit ID examples and final field name.
6. **Local Compose uses development default credentials.** Ports bind to loopback, and startup checks secret length. Any nonlocal deployment must provide unique secrets, HTTPS/Secure cookies, fixed dependency/image versions, and operational monitoring for pending tasks and DLQ.

On RabbitMQ 4.4, repeated AMQP 0-9-1 `basic.nack(requeue=true)` does not increase the quorum queue's `x-delivery-count`; `basic.reject(requeue=true)` does. The test uses `reject` to verify that the configured delivery limit routes a poison task to the DLQ. The Agent contract's `NACK` on lost SSE remains appropriate for transient retries, but a permanent-error retry policy must be agreed with the Agent developer. See [RabbitMQ quorum queue documentation](https://www.rabbitmq.com/docs/quorum-queues) and [negative acknowledgement documentation](https://www.rabbitmq.com/docs/nack).

## Test commands

```powershell
python -m pytest backend/tests -q
powershell -ExecutionPolicy Bypass -File backend/integration_tests/run.ps1
docker compose config --quiet
```

The integration runner creates a project named `travel-bot-int-*`, uses separate local ports and volumes, and removes only that project after the tests. A successful run proves that the Backend, MongoDB and RabbitMQ containers build, start, serve requests, and recover from the tested failures. It does not start an Agent Worker or Frontend.

Observed on 2026-10-26: 24 Backend unit/API tests passed, 10 live integration tests passed, and `docker compose config --quiet` passed. The live stack used the pinned `rabbitmq:4.4.0-management-alpine` image.
