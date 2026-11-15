FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt pyproject.toml ./
RUN pip install -r requirements.txt

COPY companion ./companion
COPY config ./config
RUN pip install --no-deps -e . \
    && useradd --create-home --uid 1000 companion \
    && mkdir -p /app/data && chown -R companion /app/data /app/config

USER companion

# SQLite DB lives here; mount a volume so memories survive restarts.
VOLUME ["/app/data"]

# Default: run the Telegram bot. Override to run the API instead:
#   docker run ... ai-companion uvicorn companion.api:app --host 0.0.0.0 --port 8000
CMD ["python", "-m", "companion.telegram_bot"]
