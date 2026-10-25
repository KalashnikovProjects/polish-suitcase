# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder

WORKDIR /build
COPY data_layer/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-s andard]
WORKDIR /app

# Копируем зависимости из builder
COPY --from=builder /install /usr/local

# Копируем код
COPY data_layer/ ./data_layer/

# Создаем непривилегированного пользователя
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

ENV PARSER_PORT=8080
ENV PARSER_SERVICE_TOKEN=dev-token-123
EXPOSE 8080

# Healthcheck
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS http://localhost:${PARSER_PORT}/health || exit 1

CMD ["sh", "-c", "python -m uvicorn data_layer.service.api:app --host 0.0.0.0 --port ${PARSER_PORT}"]