# syntax=docker/dockerfile:1

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HEXGATE_HOME=/data \
    HEXGATE_HOST=0.0.0.0 \
    HEXGATE_ALLOW_REMOTE=true

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY hexgate ./hexgate

RUN python -m pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin hexgate \
    && mkdir -p /data \
    && chown -R hexgate:hexgate /data /app

USER hexgate

VOLUME ["/data"]
EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/healthz', timeout=4)"]

CMD ["hexgate", "serve", "--no-browser"]
