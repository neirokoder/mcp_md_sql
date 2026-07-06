# todo.md — md_initialize + remove root_path from tools

- [x] Plan approved
- [x] descriptions.py: MD_DEBUG → MD_INITIALIZE, update ROOT_DESC
- [x] server.py:
  - [x] list_tools: md_initialize(root) + md_select/md_change без root_path
  - [x] call_tool: route md_initialize → _handle_initialize, fallback → error с просьбой вызвать md_initialize
  - [x] _handle_initialize (новый): init conn, index, старт watcher + возврат диагностики
  - [x] Удалить auto-resolve блок и root_path логику из call_tool
- [x] tests/test_server.py: обновить _handle_debug → _handle_initialize тесты
- [x] Финальная проверка: 192 passed, 0 failed
