# SCICP C2 Edge Agent and IoT Device Management. Build context is c2/ (the service does not import C1).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/c2
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY c2_service ./c2_service
COPY tools ./tools
RUN mkdir -p /data/clips

EXPOSE 8002
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8002/api/v1/health').status==200 else 1)"

CMD ["uvicorn", "c2_service.main:app", "--host", "0.0.0.0", "--port", "8002", "--proxy-headers", "--forwarded-allow-ips", "*"]
