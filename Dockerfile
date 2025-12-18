FROM python:3.14-slim

ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/

COPY . .

RUN uv sync --locked --no-dev
RUN uv run playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*

CMD ["sh", "-c", "uv run hypercorn app:app --bind 0.0.0.0:${PORT:-8000} --access-log -"]
