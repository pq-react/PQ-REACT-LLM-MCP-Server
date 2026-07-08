FROM python:3.11-slim

WORKDIR /app

RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY policy.py cyclonedx_parser.py api.py /app/

RUN useradd --uid 10001 --create-home --shell /bin/false inv && \
    chown -R inv:inv /app
USER inv

ENV LISTEN_PORT=8082 \
    PYTHONUNBUFFERED=1

EXPOSE 8082

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8082/health >/dev/null || exit 1

CMD ["sh", "-c", "uvicorn api:app --host 0.0.0.0 --port ${LISTEN_PORT}"]
