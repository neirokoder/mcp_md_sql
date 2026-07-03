## Выполнено ✅

### Правки кода
- `_render_element` — фикс двойного `get`
- `_build_insert_sql` → параметризованный запрос, `_sql_quote` удалена
- `_handle_update` — детерминированное отслеживание after-строк по `id`
- `_handle_delete` — проверка `matched no rows`
- `_validate_select` — запрет multi-statement
- `_validate_dml` — `_extract_set_columns` вместо regex
- `INSERT OR REPLACE` — поддержан (optional modifier в regex)
- Поле `version` удалено из схемы, INSERT, UPDATE, валидации, тестов
- `parse_doc_roots` — поддержка JSON-массива + fallback на legacy `;` формат
- `server.py` — приём roots от IDE через MCP протокол (lazy при list_tools)

### Тесты
- 103 теста (все проходят, 0 warnings)
- 30 типовых SELECT-запросов
- DELETE, SELECT, config, _parse_attrs — граничные случаи и отказоустойчивость
