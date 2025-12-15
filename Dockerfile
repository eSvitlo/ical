FROM python:3.14-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/

COPY . .

RUN uv sync --no-dev
RUN uv run playwright install chromium
RUN uv run playwright install-deps chromium && rm -rf /var/lib/apt/lists/*

CMD ["sh", "-c", "uv run hypercorn app:app --bind 0.0.0.0:${PORT:-8000} --access-log -"]
