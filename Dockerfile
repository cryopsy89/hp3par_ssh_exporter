FROM python:3.9-slim

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN mkdir -p logs

COPY config.yaml ./
COPY requirements.txt .
COPY lightweight_monitoring.py .
COPY hp3_primera_monitoring.py .

RUN pip install --no-cache-dir -r requirements.txt

RUN groupadd -r app && useradd -r -g app app
RUN chown -R app:app /app

USER app

EXPOSE 6767

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:6767/health || exit 1

CMD ["python", "lightweight_monitoring.py"]