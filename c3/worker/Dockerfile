# SCICP C4 classifier worker (CLAP + head, CPU). Build context is c3/worker/.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/hf-cache \
    MODEL_CACHE_DIR=/tmp/c4-models \
    TOKENIZERS_PARALLELISM=false

WORKDIR /srv/c4
COPY requirements.txt .
# CPU-only torch wheels first (no CUDA libraries), then the rest; torch==2.5.1 in requirements.txt is then satisfied.
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch==2.5.1 \
 && pip install -r requirements.txt

COPY c4_worker ./c4_worker
COPY make_zero_shot_model.py .

# /hf-cache is the named volume c4-hf-cache: the CLAP weights are downloaded on the first start only.
RUN useradd --create-home --uid 10001 c4 && mkdir -p /hf-cache && chown c4:c4 /hf-cache
USER c4

EXPOSE 8010
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8010/healthz').status==200 else 1)"

CMD ["python", "-m", "c4_worker.main"]
