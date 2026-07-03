---
title: System Configuration Guide
product: Acme Platform 3.x
updated: 2025-06-15
---

# System Configuration Guide

This guide covers all configuration options for the **Acme Platform**.
See the [official website](https://acme.example.com "Acme Platform") for the latest version.

## Quick Start

To get started, create a `config.yml` file:

```yaml
server:
  host: "0.0.0.0"
  port: 8080
  workers: 4

database:
  url: "postgresql://localhost:5432/acme"
  pool_size: 10
```

> **Tip:** Use `--config` flag to specify a custom path:
> ```bash
> acme server --config /etc/acme/config.yml
> ```

## Database Configuration

### Connection Settings

The following table describes database connection options:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | string | — | Database connection string |
| `pool_size` | integer | `5` | Maximum connections in pool |
| `timeout` | integer | `30` | Connection timeout in seconds |
| `ssl_mode` | string | `prefer` | SSL mode: `disable`, `prefer`, `require` |

### Connection Pool Tuning

- **Small deployments** (1-5 workers)
  - `pool_size`: 5
  - `timeout`: 30
- **Medium deployments** (5-20 workers)
  - `pool_size`: 10-20
  - `timeout`: 15
  - *Enable connection recycling*
- **Large deployments** (20+ workers)
  - `pool_size`: 30-50
  - `timeout`: 10
  - *Use PgBouncer for connection pooling*

> **Warning:** Setting `pool_size` too high can degrade performance.
> Monitor connection usage with:
> ```bash
> acme admin db:stats
> ```

## Logging Configuration

### Log Levels

- **ERROR** — critical issues requiring immediate attention
- **WARN** — warnings that should be reviewed
- **INFO** — normal operational messages
- **DEBUG** — detailed debugging information

```ini
[logging]
level = INFO
format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
output = stdout
```

### Structured Logging

For production, enable **JSON logging** for integration with log aggregation tools:

```toml
[logging]
format = "json"
level = "INFO"
output = "stdout"
fields = ["timestamp", "level", "logger", "message", "request_id"]
```

## Security Settings

### TLS Configuration

SSL/TLS must be configured for *all* production deployments:

```yaml
tls:
  cert: "/etc/acme/certs/server.crt"
  key: "/etc/acme/certs/server.key"
  min_version: "1.2"
```

### Access Control

- **Authentication**: JWT-based with RS256 signatures
- **Authorization**: Role-based access control (RBAC)
  - Roles: `admin`, `editor`, `viewer`
  - *Custom roles* can be defined in `roles.yml`
- **Rate Limiting**: Configurable per endpoint group

> **Tip:** Use environment variables for sensitive values:
> ```bash
> export ACME_DB_URL="postgresql://user:pass@localhost:5432/acme"
> ```

## Troubleshooting

Common issues and solutions:

1. **Connection refused** — Check if the database is running:
   ```bash
   systemctl status postgresql
   ```
2. **TLS handshake failed** — Verify certificate paths:
   ```bash
   openssl verify -CAfile ca.pem server.crt
   ```
3. **Permission denied** — Ensure the user has correct RBAC role:
   ```bash
   acme admin user:info --user alice
   ```
