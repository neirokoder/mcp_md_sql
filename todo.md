# План: три архитектурных дефекта

## Очередность

1. **server.py** — `_format_select_result`: +extra-колонки как JSON-блок
2. **tools.py** — `_split_values`: depth скобок + кавычки
3. **tools.py** — `_handle_insert`: exec подзапроса для pos_in_file
4. **tools.py** — `_handle_update`: убрать reload, сдвиг line_start/line_end
5. **tools.py** — `_handle_delete`: reload + preserve ids
6. Тесты: обновить существующие, добавить новые

## Статус

- [x] План утверждён
- [x] 1. `_format_select_result` — extra-колонки
- [x] 2. `_split_values` — depth скобок
- [x] 3. `_handle_insert` — exec подзапроса
- [x] 4. `_handle_update` — без reload, shift line_start
- [x] 5. `_handle_delete` — reload + preserve ids
- [x] 6. Обновить тесты
- [x] 7. Прогнать тесты
