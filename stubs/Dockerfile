# SCICP C2/C3 stubs + demo generator. Build context is the repository root
# (docker-compose.yml: build: { context: ., dockerfile: stubs/Dockerfile }) because the stubs
# reuse the models, error envelope and settings of the C1 API package and the seed's paths.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY c1/api/requirements.txt /srv/c1/api/requirements.txt
RUN pip install --no-cache-dir -r /srv/c1/api/requirements.txt

COPY c1/api/app /srv/c1/api/app
COPY seed /srv/seed
COPY stubs /srv/stubs

ENV PYTHONPATH=/srv/c1/api:/srv

EXPOSE 8002 8003
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8003/api/v1/health').status==200 else 1)"

CMD ["python", "-m", "stubs.standalone"]
