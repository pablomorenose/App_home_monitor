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
    CMD curl -f http://localhost:8088/login || exit 1

CMD ["python", "app.py"]
