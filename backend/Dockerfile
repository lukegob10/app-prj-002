# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

ARG TREASURY_ANALYTICS_PACKAGE

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY backend /tmp/backend

RUN --mount=type=secret,id=pip_extra_index_url,required=false \
    groupadd --system agora \
    && useradd --system --gid agora --home-dir /app --shell /usr/sbin/nologin agora \
    && pip install --upgrade pip \
    && pip install /tmp/backend \
    && if [ -n "${TREASURY_ANALYTICS_PACKAGE}" ]; then \
         if [ -s /run/secrets/pip_extra_index_url ]; then export PIP_EXTRA_INDEX_URL="$(cat /run/secrets/pip_extra_index_url)"; fi; \
         pip install "${TREASURY_ANALYTICS_PACKAGE}"; \
       fi \
    && rm -rf /tmp/backend /root/.cache \
    && mkdir -p /app/data \
    && chown -R agora:agora /app

USER agora
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health/live', timeout=3)"
CMD ["uvicorn", "agora.main:app", "--host", "0.0.0.0", "--port", "8000"]
