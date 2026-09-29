# EPI form service. Built from the repository root:
#   docker build -f epi_form/Dockerfile .
# Deployed with deploy/epi-form/docker-compose.yml (Traefik in front, TLS there).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY epi_form/requirements.txt /app/epi_form/requirements.txt
RUN pip install --no-cache-dir -r /app/epi_form/requirements.txt

COPY epi_form /app/epi_form

# Not root inside the container.
RUN useradd --create-home --uid 10001 epi
USER epi

EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"

# --proxy-headers: the client address comes from Traefik's X-Forwarded-For (used by the rate limit).
# The container publishes no port: only Traefik, on the Docker network, can reach it.
CMD ["uvicorn", "epi_form.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
