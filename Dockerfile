# Multi-stage production container for Signalpost Agent
FROM python:3.11-slim as base

# Prevent Python from buffering stdout/stderr and writing .pyc files
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install system dependencies (certificates, compiler essentials if needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy pyproject.toml first for layer caching
COPY pyproject.toml ./

# Install python dependencies
RUN pip install --no-cache-dir .

# Copy full application codebase
COPY src/ ./src/
COPY tests/ ./tests/
COPY .env.example ./

# Create non-privileged service user and output directories
RUN useradd -m -u 1000 appuser && \
    mkdir -p output/profiles output/deltas output/telemetry && \
    chown -R appuser:appuser /app

USER appuser

# Entrypoint default
ENTRYPOINT ["python", "-m", "src.cli"]
CMD ["--help"]
