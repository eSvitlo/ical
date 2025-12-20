FROM python:3.14-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN \
    apt-get update && \
    apt-get install -y dumb-init && \
    :

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/

RUN \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --locked --no-cache --no-dev && \
    uv run playwright install --with-deps chromium && \
    rm -rf /var/lib/apt/lists/* && \
    :

COPY app app

ENTRYPOINT ["dumb-init", "--"]
CMD ["sh", "-c", "exec \
    uv run \
    hypercorn \
    app.main:app \
    --bind 0.0.0.0:${PORT:-8000} \
    --access-log - \
    --max-requests ${HYPERCORN_MAX_REQUESTS:-500} \
    "]
