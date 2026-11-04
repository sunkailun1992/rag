FROM python:3.12.16-slim
LABEL org.opencontainers.image.source="https://github.com/sunkailun1992/rag"
WORKDIR /app
# uv 版本与锁文件一起固定，构建不解析浮动依赖。
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY scripts ./scripts
RUN uv sync --frozen --no-dev --no-cache && useradd --uid 10001 --create-home engine
USER 10001
ENV OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8091
CMD ["uvicorn", "rag_engine.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8091", "--workers", "1", "--limit-concurrency", "16", "--no-access-log"]
