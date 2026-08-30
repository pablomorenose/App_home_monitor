FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim

ARG DOCKER_GID=991

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 iputils-ping curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g ${DOCKER_GID} docker \
    && useradd -r -s /bin/false -G docker appuser

COPY --from=builder /install /usr/local

WORKDIR /app
COPY . .

RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8088

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8088/health || exit 1

# Un único worker a propósito: el hilo de monitorización corre dentro del
# proceso. La concurrencia se saca de los threads.
# --control-socket va a /tmp porque el contenedor arranca con read_only: true
# y su valor por defecto ($HOME/.gunicorn/gunicorn.ctl) no se puede crear.
CMD ["gunicorn", "--workers", "1", "--threads", "8", \
     "--bind", "0.0.0.0:8088", "--access-logfile", "-", \
     "--timeout", "60", "--control-socket", "/tmp/gunicorn.ctl", \
     "wsgi:app"]
