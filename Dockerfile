FROM python:3.14-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/

COPY . .

RUN uv sync --no-dev
RUN uv run playwright install chromium
RUN uv run playwright install-deps chromium && rm -rf /var/lib/apt/lists/*

CMD ["uv", "run", "hypercorn", "app:app", "--access-log", "-"]
