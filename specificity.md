# Аномалии

- **Codegraph-индекс рассинхронен**: содержит фантомные символы `_ServerState`, `get_or_create_db`, `resolve_connection` в `server.py` и `get_or_create_db`/`resolve_connection` в `database.py`. `grep` по диску: NONE. Индекс относится к устаревшей версии файлов. При необходимости — `npx codegraph init`.
