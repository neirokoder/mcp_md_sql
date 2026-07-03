---
title: Deployment and CI/CD Guide
product: Acme Platform
last_reviewed: 2025-06-01
---

# Deployment and CI/CD Guide

This guide covers automated deployment for the **Acme Platform** using Docker and CI/CD pipelines.

## Docker Setup

### Building the Image

```dockerfile
FROM python:3.12-slim AS builder

WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY . .
RUN pip install --no-cache-dir -e .

FROM python:3.12-slim
COPY --from=builder /app /app
WORKDIR /app

EXPOSE 8000
CMD ["acme", "serve", "--host", "0.0.0.0"]
```

Build and tag the image:

```bash
docker build -t acme-platform:latest .
```

### Docker Compose

```yaml
version: "3.9"

services:
  app:
    image: acme-platform:latest
    ports:
      - "8000:8000"
    environment:
      - ACME_DB_URL=postgresql://user:pass@db:5432/acme
      - ACME_SECRET_KEY=${ACME_SECRET_KEY}
    depends_on:
      - db

  db:
    image: postgres:16-alpine
    volumes:
      - pgdata:/var/lib/postgresql/data
    environment:
      POSTGRES_USER: user
      POSTGRES_PASSWORD: pass
      POSTGRES_DB: acme

volumes:
  pgdata:
```

---

## CI/CD Pipeline

### GitHub Actions

```yaml
name: Deploy

on:
  push:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev]"
      - run: pytest tests/

  deploy:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker build -t acme-platform .
      - run: docker push acme-platform:latest
      - run: kubectl set image deployment/acme acme=acme-platform:latest
```

---

## Environment Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ACME_DB_URL` | Yes | — | Database connection URL |
| `ACME_SECRET_KEY` | Yes | — | Secret key for signing |
| `ACME_LOG_LEVEL` | No | `INFO` | Logging level |
| `ACME_WORKERS` | No | `4` | Number of workers |
| `ACME_PORT` | No | `8000` | HTTP port |

---

## Health Checks

Configure **readiness** and **liveness** probes in Kubernetes:

```yaml
readinessProbe:
  httpGet:
    path: /health/ready
    port: 8000
  initialDelaySeconds: 10
  periodSeconds: 5

livenessProbe:
  httpGet:
    path: /health/live
    port: 8000
  initialDelaySeconds: 30
  periodSeconds: 15
```

> **Important:** Health check endpoints **must not** require authentication.

---

## Monitoring

Prometheus metrics are exposed at `/metrics`:

```bash
curl http://localhost:8000/metrics
```

Key metrics to monitor:

- `http_requests_total` — request count by status
- `http_request_duration_seconds` — latency histogram
- `db_connections_active` — active database connections
- `token_expiry_seconds` — remaining token validity

---

## Rollback Procedure

1. **Identify the issue**: Check logs and metrics
   ```bash
   kubectl logs deployment/acme --tail=100
   ```
2. **Rollback the deployment**:
   ```bash
   kubectl rollout undo deployment/acme
   ```
3. **Verify the rollback**:
   ```bash
   kubectl rollout status deployment/acme
   ```

> **Note:** Automated rollbacks are triggered when health checks fail 3 consecutive times.
