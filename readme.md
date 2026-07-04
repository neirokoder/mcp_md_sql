# md-sql

MCP-сервер для работы с Markdown-документацией как с реляционной базой данных. Парсит `.md` файлы в SQLite-таблицу `md`, предоставляя два MCP-инструмента: `md_select` (SELECT-запросы) и `md_change` (INSERT / UPDATE / DELETE с автоматической записью изменений обратно в исходные файлы).

## Быстрый старт

```bash
pip install -e .
DOC_ROOTS='["/path/to/docs"]' python -m mcp_md_sql.server
```

## Подключение к MCP-клиенту

### Zed

```json
{
  "mcp_servers": {
    "sql-mcp": {
      "command": "python",
      "args": ["-m", "mcp_md_sql.server"]
    }
  }
}
```

### Claude Desktop

```json
{
  "mcpServers": {
    "sql-mcp": {
      "command": "python",
      "args": ["-m", "mcp_md_sql.server"]
    }
  }
}
```

> `DOC_ROOTS` можно не указывать — сервер получит корни от IDE через MCP-протокол. Переменная окружения используется как fallback.

## DOC_ROUTES

JSON-массив путей:

```bash
DOC_ROOTS='["/project/docs", "/project/specs"]'
```

Алиас корня генерируется из имени папки. Для путей выше это `docs` и `specs`.

Поддерживается и legacy-формат `alias=/path` (разделитель `;`):

```bash
DOC_ROOTS="docs=./docs;specs=./specifications"
```

Если переменная не задана — используется текущая рабочая директория с алиасом `default`.

## MCP-инструменты

### `md_select`

Выполняет произвольный SELECT-запрос к таблице `md`.

```
SELECT * FROM md WHERE type = 'heading' AND depth = 0
```

### `md_change`

Модифицирует документацию через INSERT / UPDATE / DELETE. Хирургически патчит исходный `.md` файл и возвращает diff.

Ограничения:
- DDL и транзакции запрещены
- UPDATE и DELETE без WHERE запрещены
- Прямое изменение `id` запрещено
- Multi-statement в SELECT запрещены
- Таймаут SQL-запроса: 15 секунд

## Таблица `md`

| Колонка      | Тип       | Описание                                     |
|-------------|-----------|------------------------------------------------|
| id          | INTEGER   | Автоинкрементный первичный ключ                |
| root        | TEXT      | Алиас корня документации                       |
| file        | TEXT      | Относительный путь к .md файлу                 |
| line_start  | INTEGER   | Начальная строка элемента в файле (1-based)     |
| line_end    | INTEGER   | Конечная строка элемента                        |
| pos_in_file | INTEGER   | Порядковая позиция в документе (1..N)           |
| parent_pos  | INTEGER   | pos_in_file родителя (NULL для корневых)        |
| depth       | INTEGER   | Уровень вложенности                             |
| type        | TEXT      | Тип элемента (heading, paragraph, list_item, code_block, blockquote, table, strong, emphasis, link, image, inline_code, strikethrough, hr, frontmatter, html, html_inline) |
| content     | TEXT      | Содержимое элемента                             |
| attrs_json  | TEXT      | JSON-строка с атрибутами                       |

## Структура проекта

```
src/mcp_md_sql/
├── config.py         # DOC_ROOTS → RootConfig
├── parser.py         # Парсинг .md через mistune → flat list
├── database.py       # SQLite in-memory: схема, загрузка, reload
├── reconstructor.py  # Обратная сборка Markdown из строк таблицы
├── tools.py          # md_select / md_change
├── watcher.py        # Live-синхронизация через watchdog
└── server.py         # Точка входа, MCP-сервер

tests/
├── test_parser.py          # 30 тестов парсера (+ frontmatter, nested lists)
├── test_reconstructor.py   # 23 теста реконструктора (+ frontmatter)
├── test_integration.py     # 15 интеграционных тестов (+ integrity check)
├── test_select_queries.py  # 47 тестов (SELECT + валидация + config + timeout + FS errors)
├── test_watcher.py         # 4 теста файлового вотчера
├── test_server.py          # 11 тестов серверного слоя (_handle_call_tool dispatch)
├── test_scenarios.py       # 20 сценариев реальной работы (SELECT/INSERT/UPDATE/DELETE chains)
└── fixtures/
    ├── test_basic.md       # Базовый фикстур
    └── complex/            # 5 сложных .md документов для 20 сценариев
        ├── api_docs.md
        ├── configuration_guide.md
        ├── technical_spec.md
        ├── tutorial_quickstart.md
        └── deployment_guide.md
```

## Запуск тестов

```bash
pip install -e ".[dev]"
python -m pytest tests/
```

## Зависимости

- `mistune >= 3.0` — парсинг Markdown в AST
- `watchdog >= 6.0` — отслеживание изменений файлов
- `mcp >= 1.0` — протокол Model Context Protocol
- Python >= 3.11
