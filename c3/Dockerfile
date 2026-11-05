# SCICP C3 Alert Tracking and Monitoring. Build context is c3/ (the service does not import C1).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/c3
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY c3_service ./c3_service

EXPOSE 8003
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8003/api/v1/health').status==200 else 1)"

CMD ["uvicorn", "c3_service.main:app", "--host", "0.0.0.0", "--port", "8003", "--proxy-headers", "--forwarded-allow-ips", "*"]
