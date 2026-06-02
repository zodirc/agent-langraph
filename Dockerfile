FROM python:3.11-slim

WORKDIR /app

# Chroma / sqlite 兼容
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    g++ \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10 \
    PIP_PROGRESS_BAR=off \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}

RUN python -m pip install --upgrade pip setuptools wheel && \
    python -m pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY config/ ./config/
COPY web/ ./web/
COPY knowledge/ ./knowledge/

RUN mkdir -p /data/db /data/vectorstore /data/logs /data/code_verify /data/models/sentence-transformers

# 与 docker-compose.yml 一致；宿主机通过 -e / --env-file 覆盖
ENV CONFIG_PATH=/app/config/config.docker.yaml \
    APP_ENV=production \
    APP_PORT=8000 \
    STORAGE_BACKEND=postgres

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
