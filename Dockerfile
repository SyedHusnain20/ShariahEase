FROM python:3.11-slim

# Non-root user required by HF Spaces
RUN useradd -m -u 1000 appuser

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    curl \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy full project
COPY --chown=appuser:appuser . .

# Persistent storage directory for SQLite DB
RUN mkdir -p /data && chown appuser:appuser /data

USER appuser

EXPOSE 7860

ENV DATABASE_PATH=/data/shariahease.db
ENV ENV=production
ENV PYTHONUNBUFFERED=1

# Auto-build FAISS index if missing, then start app
CMD ["sh", "-c", "if [ ! -f knowledge_base/index/faiss_index.bin ]; then echo 'Building FAISS index...' && python knowledge_base/build_index.py; fi && uvicorn main:app --host 0.0.0.0 --port 7860"]