FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/backend:/app
WORKDIR /app
RUN rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* \
    && apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/data/media \
    && chown -R appuser:appuser /app
COPY --chown=appuser:appuser backend/ ./backend/
COPY --chown=appuser:appuser worker/ ./worker/
COPY --chown=appuser:appuser scripts/ ./scripts/
WORKDIR /app
USER appuser
CMD ["python", "-m", "worker.main"]
