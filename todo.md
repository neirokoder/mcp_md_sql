# Сделано

## Тесты
- [x] Таймаут: progress_handler напрямую + `_set_timeout` через monkeypatch
- [x] Frontmatter: парсинг YAML frontmatter + реконструкция
- [x] Вложенные списки: 2 уровня + 3 уровня + вложенный paragraph
- [x] ФС-ошибки: ToolError при отсутствии файла для UPDATE и DELETE

## Улучшение _patch_file_insert
- [x] Проверка целостности: размер файла, контекст строк до/после вставки

## 20 сложных сценариев (test_scenarios.py)
- [x] 5 сложных .md документов в tests/fixtures/complex/
- [x] 10 SELECT-сценариев: TOC, code langs, tables, links, keyword search, frontmatter, stats, nested lists, hierarchy, complexity
- [x] 6 CHANGE-сценариев: mass rename, bulk update, delete section, insert paragraph/table, delete inline
- [x] 4 CHAIN-сценария: full lifecycle, complex UPDATE with json_extract, delete nested items, insert special chars
- [x] Все сценарии через `_handle_call_tool` (реальный серверный dispatch)
