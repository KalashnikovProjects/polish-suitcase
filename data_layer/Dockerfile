# --- ЭТАП 1: Сборка зависимостей ---
FROM python:3.12-slim AS builder

WORKDIR /build
COPY data_layer/requirements.txt .

# Устанавливаем зависимости в отдельную директорию, чтобы скопировать только их
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# --- ЭТАП 2: Легковесный рантайм ---
FROM python:3.12-slim AS runtime

# Копируем зависимости из этапа сборки
COPY --from=builder /install /usr/local

# Создаем непривилегированного пользователя для безопасности
RUN useradd --create-home --uid 10001 appuser

WORKDIR /app

# Копируем код приложения
COPY data_layer/ ./data_layer/

# Передаем права на директорию приложения пользователю appuser
RUN chown -R appuser:appuser /app

# Переключаемся на непривилегированного пользователя
USER appuser

# Переменные окружения по умолчанию
ENV PARSER_PORT=8080 \
    DATA_MODE=synthetic \
    PYTHONUNBUFFERED=1

EXPOSE 8080

# Healthcheck для Docker Compose
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ['PARSER_PORT'] + '/health', timeout=3)"

# Запуск приложения через uvicorn
CMD ["sh", "-c", "python -m uvicorn data_layer.service.api:app --host 0.0.0.0 --port ${PARSER_PORT}"]
