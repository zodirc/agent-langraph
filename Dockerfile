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
# sentence-transformers 依赖 torch；先装 CPU 版，避免 pip 拉取数 GB 的 nvidia_* CUDA wheel
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10 \
    PIP_PROGRESS_BAR=off \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}

RUN python -m pip install --upgrade pip setuptools wheel && \
    python -m pip install --no-cache-dir torch --index-url ${TORCH_INDEX_URL} && \
    python -m pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY config/ ./config/
COPY web/ ./web/
COPY knowledge/ ./knowledge/
# Open RAG benchmark（convert + run_open_rag_eval，可在容器内 exec 执行）
COPY tests/eval/ ./tests/eval/

RUN mkdir -p /app/models-baked/sentence-transformers

# 构建时预拉取 embedding 模型到镜像内（非 /data，避免 agent_data 卷挂载覆盖）
RUN python -c "\
from sentence_transformers import SentenceTransformer; \
SentenceTransformer( \
    'sentence-transformers/all-MiniLM-L6-v2', \
    device='cpu', \
    cache_folder='/app/models-baked/sentence-transformers', \
)"

COPY deploy/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# 与 docker-compose.yml 一致；宿主机通过 -e / --env-file 覆盖
ENV CONFIG_PATH=/app/config/config.docker.yaml \
    APP_ENV=production \
    APP_PORT=8000 \
    STORAGE_BACKEND=postgres

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/health/live || exit 1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
