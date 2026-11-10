# SCICP C2 car simulator (the c2-sim service of docker-compose.c2.yml). Build context: c2/edge.
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv/edge
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agent.py sounds.py simulator.py ./
CMD ["python", "simulator.py"]
