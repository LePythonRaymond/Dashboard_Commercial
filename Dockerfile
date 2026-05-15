# syntax=docker/dockerfile:1.6
FROM python:3.12-slim

# System libs needed by some Python wheels (gspread / numpy / plotly / kaleido).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so layer caches when only source changes.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.txt

# Copy the rest of the project (Dockerignore strips venv/, logs/, .env).
COPY . /app/

# Streamlit configuration — disable usage stats, run headless on 0.0.0.0:8501.
# CORS off and XSRF off because Traefik terminates TLS in front of us; XSRF
# tokens get confused when the browser sees https:// but the container sees http://.
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_ENABLE_CORS=false \
    STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION=false

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "src/dashboard/app.py"]
