FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Build context is the repository root (docker-compose.yml: build: { context: ., dockerfile: c1/api/Dockerfile })
# so that the seed package can be imported in process when SEED_ON_START=1.
COPY c1/api/requirements.txt /srv/c1/api/requirements.txt
RUN pip install --no-cache-dir -r /srv/c1/api/requirements.txt

COPY c1/api /srv/c1/api
COPY seed /srv/seed

WORKDIR /srv/c1/api
ENV PYTHONPATH=/srv/c1/api:/srv

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
