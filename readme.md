# md-sql-server

MCP-сервер для работы с Markdown-документацией как с реляционной базой данных. Парсит `.md` файлы в SQLite-таблицу `md`, предоставляя два MCP-инструмента: `md_select` (SELECT-запросы) и `md_change` (INSERT / UPDATE / DELETE с автоматической записью изменений обратно в исходные файлы).

## Быстрый старт

```bash
# Установка
pip install -e .

# Запуск (с указанием папок с .md)
DOC_ROOTS="myproject=./docs;specs=./specifications" md-sql-server
```

Сервер работает по протоколу MCP через stdio. Подключите его к вашему MCP-клиенту (например, Zed, Claude Desktop).

## DOC_ROOTS

Переменная окружения `DOC_ROOTS` задаёт один или несколько корней документации в формате:

```
alias1=/absolute/path1;alias2=C:/absolute/path2
```

Если переменная не задана, используется текущая рабочая директория с алиасом `default`.

## MCP-инструменты

### `md_select`

Выполняет произвольный SELECT-запрос к таблице `md`. Возвращает JSON-массив строк.

```
SELECT * FROM md WHERE type = 'heading' AND depth = 0
SELECT type, content FROM md WHERE root = 'myproject' ORDER BY pos_in_file
```

### `md_change`

Модифицирует документацию через DML (INSERT / UPDATE / DELETE). Каждая операция:
1. Вычисляет diff (до/после)
2. Хирургически патчит исходный `.md` файл (замена строк)
3. Возвращает список изменений

Ограничения безопасности:
- Только SELECT, INSERT, UPDATE, DELETE (DDL и транзакции запрещены)
- UPDATE и DELETE без WHERE запрещены
- Прямое изменение `id` и `version` запрещено
- Таймаут SQL-запроса: 15 секунд

## Таблица `md`

| Колонка      | Тип       | Описание                                        |
|-------------|-----------|--------------------------------------------------|
| id          | INTEGER   | Автоинкрементный первичный ключ                  |
| root        | TEXT      | Алиас корня документации                         |
| file        | TEXT      | Относительный путь к .md файлу                   |
| line_start  | INTEGER   | Начальная строка элемента в исходном файле (1-based) |
| line_end    | INTEGER   | Конечная строка элемента                         |
| pos_in_file | INTEGER   | Порядковая позиция элемента в документе (1..N)   |
| parent_pos  | INTEGER   | pos_in_file родительского элемента (NULL для корневых) |
| depth       | INTEGER   | Уровень вложенности                              |
| type        | TEXT      | Тип элемента (heading, paragraph, list_item, code_block, blockquote, table, strong, emphasis, link, image, inline_code, strikethrough, hr, frontmatter, html, html_inline) |
| content     | TEXT      | Текстовое содержимое элемента                    |
| attrs_json  | TEXT      | JSON-строка с атрибутами (level для heading, url для link и т.д.) |
| version     | INTEGER   | Версия строки для отслеживания изменений         |

## Типы элементов

**Блочные** (depth=0, есть line_start/line_end): heading, paragraph, code_block, list_item, blockquote, table, hr, frontmatter, html

**Инлайн** (depth>0, parent_pos указывает на родителя): strong, emphasis, inline_code, link, image, strikethrough, html_inline

Инлайн-элементы извлекаются как отдельные строки таблицы, но при патче файла обновляются через родительский блок.

## Структура проекта

```
mcp_md/
├── src/md_sql_server/
│   ├── config.py         # Конфигурация: DOC_ROOTS, RootConfig
│   ├── parser.py         # Парсинг .md через mistune в плоский список ParsedElement
│   ├── database.py       # SQLite (in-memory): схема, загрузка, reload
│   ├── reconstructor.py  # Обратная сборка Markdown из строк таблицы
│   ├── tools.py          # md_select и md_change: валидация, diff, хирургический патч
│   ├── watcher.py        # Live-синхронизация через watchdog
│   └── server.py         # Точка входа: MCP-сервер, регистрация инструментов
├── tests/
│   ├── fixtures/         # Тестовые .md файлы
│   ├── test_parser.py        # 24 юнит-теста парсера
│   ├── test_reconstructor.py # 22 юнит-теста реконструктора
│   └── test_integration.py   # 11 интеграционных тестов (сквозной цикл)
├── pyproject.toml
├── guide.md              # Правила и концепция системы
└── specificity.md        # Значимые аномалии и подсказки
```

## Запуск тестов

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v
```

## Зависимости

- `mistune >= 3.0` — парсинг Markdown в AST
- `watchdog >= 6.0` — отслеживание изменений файлов
- `mcp >= 1.0` — протокол Model Context Protocol
- Python >= 3.11
