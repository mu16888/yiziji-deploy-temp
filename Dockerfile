# AI 互动课堂变现模板 — 应用镜像（FastAPI + uvicorn）
# 构建：docker compose build  或  docker build -t ai-monetization-template .
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# curl 供容器 healthcheck 探测 /health；无其它系统依赖。
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# 依赖清单只维护在 pyproject.toml 一处。
COPY pyproject.toml ./
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY scripts ./scripts
COPY templates ./templates
COPY static ./static

RUN python -m pip install --upgrade pip \
 && python -m pip install -e .

# 默认 SQLite 文件放在可挂载的 /app/data（见 docker-compose.yml 的 appdata 卷）。
# 生产建议把 DATABASE_URL 指向 PostgreSQL。
RUN useradd --create-home appuser \
 && mkdir -p /app/data \
 && chown -R appuser:appuser /app
USER appuser

ENV DATABASE_URL=sqlite:////app/data/app.db

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# 启动顺序固定：先迁移、再幂等播种、最后起服务。
# seed 的 Base.metadata.create_all 在表已存在时是 no-op（T2 裁决），不会覆盖迁移结构。
CMD ["sh", "-c", "alembic upgrade head && python scripts/seed.py && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
