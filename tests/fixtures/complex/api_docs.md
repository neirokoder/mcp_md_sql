---
title: User Management API
version: 2.1.0
---

# User Management API

## Overview

This API provides **user management** functionality for the platform.
Supports *CRUD* operations, authentication, and role-based access control.

> **Important:** All API calls require a valid `Bearer` token in the Authorization header.
> Rate limiting: **1000** requests per hour per token.

## Endpoints

### Users

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | `/api/v1/users` | List all users | Admin |
| GET | `/api/v1/users/{id}` | Get user by ID | Any |
| POST | `/api/v1/users` | Create new user | Admin |
| PUT | `/api/v1/users/{id}` | Update user | Admin |
| DELETE | `/api/v1/users/{id}` | Delete user | Admin |

### Authentication

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/auth/login` | Login and get token |
| POST | `/api/v1/auth/refresh` | Refresh token |
| POST | `/api/v1/auth/logout` | Invalidate token |

## Code Examples

### List Users (Python)

```python
import requests

response = requests.get(
    "https://api.example.com/api/v1/users",
    headers={"Authorization": "Bearer TOKEN"}
)
users = response.json()
for user in users:
    print(user["name"], user["email"])
```

### Create User (cURL)

```bash
curl -X POST https://api.example.com/api/v1/users \
  -H "Authorization: Bearer TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "Alice", "role": "editor"}'
```

## Error Codes

| Code | Description |
|------|-------------|
| `400` | Bad Request — invalid input |
| `401` | Unauthorized — missing or invalid token |
| `403` | Forbidden — insufficient permissions |
| `404` | Not Found — resource does not exist |
| `429` | Rate Limited — too many requests |
| `500` | Internal Server Error — try again later |

## Deprecated Endpoints

> **Warning:** The following endpoints are deprecated and will be removed in v3.0.

| Method | Endpoint | Replacement |
|--------|----------|-------------|
| POST | `/api/v1/register` | `POST /api/v1/users` |
| POST | `/api/v1/login` | `POST /api/v1/auth/login` |
