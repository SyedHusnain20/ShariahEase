FROM python:3.11-slim

# Non-root user required by HF Spaces
RUN useradd -m -u 1000 appuser

WORKDIR /app

# System deps — gcc/g++ needed for FAISS, faster-whisper native libs
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    curl \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps (separate layer for caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy full project
COPY --chown=appuser:appuser . .

# Persistent storage directory for SQLite DB
RUN mkdir -p /data && chown appuser:appuser /data

USER appuser

# HF Spaces mandatory port
EXPOSE 7860

# Environment defaults (overridden by Space Variables/Secrets)
ENV DATABASE_PATH=/data/shariahease.db
ENV ENV=production
ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]