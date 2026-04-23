# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app

# Кешируем зависимости отдельно от исходников
COPY pyproject.toml ./
# uv.lock генерируется командой `uv lock` — если файл есть, используем его
COPY uv.lock* ./

RUN uv sync --frozen --no-dev --no-install-project

# Копируем исходники и устанавливаем сам пакет
COPY src/ src/
RUN uv sync --frozen --no-dev


# ── Runtime image ───────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        openssh-client \
    && rm -rf /var/lib/apt/lists/*

# Создаём непривилегированного пользователя
RUN useradd -m -u 1000 mirror

WORKDIR /app

# Копируем виртуальное окружение из builder
COPY --from=builder /app/.venv /app/.venv

# Директории для конфига, зеркал и SSH-ключей
RUN mkdir -p /mirrors /keys && chown mirror:mirror /mirrors /keys

USER mirror

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["gitverse-mirror"]
# По умолчанию — однократный запуск; для демона передайте --daemon
CMD ["--config", "/app/config.yaml"]
