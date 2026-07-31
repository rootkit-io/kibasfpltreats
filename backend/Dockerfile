# ---------------------------------------------------------------------------
# fpl-xpts Admin API (FastAPI + Uvicorn)
#
# Build context: backend/  (see .dockerignore for exclusions)
# The package is installed non-editable into site-packages; FPL_XPTS_ROOT
# re-anchors all default data/model paths to /app (see fpl_xpts/config.py).
# ---------------------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# libgomp1: OpenMP runtime required by xgboost/scikit-learn wheels.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies + the package first for better layer caching.
COPY pyproject.toml ./
COPY src ./src
RUN pip install ".[api,db]"

# Runtime assets: entrypoint package, model bundles, data caches, weekly CSVs.
COPY app ./app
COPY db ./db
COPY scripts ./scripts
COPY models ./models
COPY data ./data
COPY *.csv ./

# Anchor fpl_xpts default paths to the image's working directory.
ENV FPL_XPTS_ROOT=/app

# Run as a non-root user.
RUN useradd --create-home --uid 1001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
