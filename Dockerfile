# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN --mount=type=secret,id=proxy_ca \
    PIP_CERT=$(if [ -f /run/secrets/proxy_ca ]; then echo /run/secrets/proxy_ca; else echo /etc/ssl/certs/ca-certificates.crt; fi) \
    pip install --no-cache-dir -r requirements.txt
COPY --chown=10001:10001 backend ./backend
COPY --chown=10001:10001 frontend ./frontend
COPY --chown=10001:10001 skills ./skills
COPY --chown=10001:10001 LICENSE ./LICENSE
RUN useradd --uid 10001 --create-home tone_correction && mkdir -p /app/data && chown 10001:10001 /app/data
USER 10001
EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
