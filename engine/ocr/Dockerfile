FROM python:3.12.16-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 libglib2.0-0 libgl1 && rm -rf /var/lib/apt/lists/*
LABEL org.opencontainers.image.source="https://github.com/sunkailun1992/rag"
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-cache
COPY ocr_app.py ocr_worker.py download_models.py model-sha256.json ./
COPY scripts ./scripts
COPY tests/fixtures ./tests/fixtures
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PADDLE_PDX_MODEL_SOURCE=BOS \
    PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True RAG_OCR_MODEL_ROOT=/opt/models \
    PADDLE_PDX_CACHE_HOME=/tmp/paddlex XDG_CACHE_HOME=/tmp/cache
RUN python download_models.py && rm -rf /root/.paddlex /root/.cache && useradd --uid 10001 --create-home ocr
USER 10001
EXPOSE 8092
CMD ["uvicorn", "ocr_app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8092", "--workers", "1", "--limit-concurrency", "4", "--no-access-log"]
