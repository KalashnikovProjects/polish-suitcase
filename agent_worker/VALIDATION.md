# Проверка реализации — 25.10.2026

Проверено на Python 3.12.14 / Windows и в Linux Docker-образе агента.

| Проверка | Результат |
| --- | --- |
| `python -m pytest -q` | 59 passed, 1 skipped; пропущен только opt-in тест реального брокера |
| `scripts/test-rabbitmq.ps1` | 1 passed, 59 deselected; настоящий RabbitMQ 4.2, quorum queue, ACK, NACK/redelivery, reject/DLQ |
| `ruff check src tests scripts/export-examples.py` | Все проверки пройдены |
| `ruff format --check src tests scripts/export-examples.py` | Форматирование проверено |
| `python -m pip check` | No broken requirements found |
| Установка пакета через pyproject.toml | Успешна, CLI установлен |
| Локальный CLI demo | Три варианта без ключей |
| `docker build -t ostrovok-agent:local-check .` | Образ собран |
| Docker demo | Три варианта без ключей |
| Docker worker без настроек | Завершился до подключения: не заполнены RABBITMQ_URL, AGENT_SERVICE_TOKEN |

Тесты проверяют диалог, режимы автономии, пересборку, арифметику, неизвестные условия, сохранение отеля, удаления направлений, SSE/REST, конфликты, отмену, повторную доставку и восстановление. Формат запроса GPT-6 Luna проверен с MockTransport: `/v1/responses`, `model=gpt-6-luna`, reasoning `low`, JSON Schema, `store=false`.

Секреты пользователя не добавлялись. Реальная модель не вызывалась. Полный прогон с backend и Parser команды ещё не выполнялся: в исходном репозитории этих реализаций нет. Формы Parser нужно совместно сверить с владельцем сервиса; см. PARSER_CONTRACT.md.

На этой машине Windows sandbox блокирует socketpair, необходимый asyncio. Асинхронные проверки выполнялись вне sandbox; внешние API в автономных тестах заменены имитациями. Временный RabbitMQ-контейнер удалён после проверки.

Все исходные отслеживаемые файлы репозитория оставлены без изменений; новые файлы находятся в agent_worker.
