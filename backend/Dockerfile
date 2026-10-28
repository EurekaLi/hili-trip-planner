FROM python:3.11-slim AS dependencies

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY backend/requirements-linux.lock ./requirements-linux.lock
RUN python -m pip install \
    --prefix=/install \
    --require-hashes \
    -r requirements-linux.lock


FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8

RUN addgroup --system app && adduser --system --ingroup app --home /app app
COPY --from=dependencies /install /usr/local

WORKDIR /app
COPY --from=hili_agents_src /hili_agents ./hili_agents
COPY backend/app ./app
COPY backend/evals ./evals
COPY backend/migrations ./migrations
COPY backend/vendor ./vendor
COPY backend/alembic.ini ./alembic.ini

USER app
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"]

CMD ["python", "-m", "uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
