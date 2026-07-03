---
title: Quick Start Tutorial
difficulty: beginner
estimated_time: 15 minutes
---

# Quick Start Tutorial

Welcome to the **Acme Platform** quick start tutorial.
By the end of this guide, you will have a working application.

## Prerequisites

Before you begin, ensure you have:

- [Python](https://python.org "Python Official Site") 3.11 or later
- A code editor (we recommend [VS Code](https://code.visualstudio.com "VS Code"))
- Basic knowledge of `command line` tools

## Step 1: Install the CLI

Install the Acme CLI using `pip`:

```bash
pip install acme-cli
```

> **Tip:** Consider using a virtual environment:
> ```bash
> python -m venv .venv
> source .venv/bin/activate  # On Windows: .venv\Scripts\activate
> ```

## Step 2: Initialize a Project

Create a new project with the `init` command:

```bash
acme init my-project
cd my-project
```

This creates the following structure:

```
my-project/
├── app/
│   ├── __init__.py
│   └── main.py
├── config/
│   ├── settings.py
│   └── routes.py
├── tests/
│   └── test_app.py
└── pyproject.toml
```

## Step 3: Configure Your Application

Edit `config/settings.py` with your preferences:

```python
# config/settings.py

from acme.config import Settings

class AppSettings(Settings):
    app_name: str = "My Application"
    debug: bool = True
    database_url: str = "sqlite:///app.db"
    secret_key: str = "change-me-in-production"
```

> **Caution:** Never commit secrets to version control.
> Use environment variables for `secret_key` and `database_url`.

## Step 4: Run the Development Server

Start the development server:

```bash
acme serve --reload
```

Your application will be available at `http://localhost:8000`.

## Step 5: Verify It Works

Open your browser or use `curl`:

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{"status": "ok", "version": "1.0.0"}
```

## Comparison: Development vs Production

| Aspect | Development | Production |
|--------|-------------|------------|
| Server | Built-in dev server | Gunicorn + Uvicorn |
| Debug | Enabled | Disabled |
| Logging | Console (DEBUG level) | JSON (INFO level) |
| Database | SQLite | PostgreSQL |
| Static files | Dev server | CDN or Nginx |
| Secret key | Hardcoded | Environment variable |

## Next Steps

Now you have a running application! See the [deployment guide](/deployment_guide.md "Deployment Guide") for production setup.

Additional resources:

- [Configuration Guide](/configuration_guide.md "Configuration Guide") — detailed settings
- [API Documentation](/api_docs.md "API Documentation") — REST API reference
- [Technical Spec](/technical_spec.md "Technical Spec") — protocol details

> **Congratulations!** You completed the quick start tutorial.
