FROM python:3.14-slim

ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

RUN \
    apt-get update && \
    apt-get install -y dumb-init && \
    :

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/

COPY . .

RUN \
    uv sync --locked --no-dev && \
    uv run playwright install --with-deps chromium && \
    rm -rf /var/lib/apt/lists/* && \
    :

ENTRYPOINT ["dumb-init", "--"]
CMD ["sh", "-c", "exec \
    uv run \
    hypercorn \
    app:app \
    --bind 0.0.0.0:${PORT:-8000} \
    --access-log - \
    --max-requests ${HYPERCORN_MAX_REQUESTS:-500} \
    "]
