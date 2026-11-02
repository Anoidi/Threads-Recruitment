FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

FROM base AS test
RUN rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* \
    && apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*
COPY backend/requirements-dev.txt ./requirements-dev.txt
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY pytest.ini ./pytest.ini
COPY backend/ ./backend/
COPY worker/ ./worker/
COPY tests/backend/ ./tests/backend/
COPY scripts/ ./scripts/
COPY workflows/ ./workflows/
ENV PYTHONPATH=/app/backend:/app
CMD ["pytest", "tests/backend", "-q"]

FROM base AS runtime
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/data/media \
    && chown -R appuser:appuser /app
COPY --chown=appuser:appuser backend/ ./backend/
ENV PYTHONPATH=/app/backend
WORKDIR /app/backend
USER appuser
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log"]
