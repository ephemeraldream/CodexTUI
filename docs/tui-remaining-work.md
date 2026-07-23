# Что еще осталось по TUI

Дата среза: 2026-07-17.
Ветка: `tui_gnhf_major_update`.
Основные источники: `.gnhf/runs/tui-tui-357c9e/notes.md`, `docs/renderer-plan.md`, `README.md`.

## Короткий вывод

TUI уже близок к состоянию "как задумано" для ежедневного использования.
Основная поверхность готова: список сессий, чистый preview, in-TUI streaming, follow-up prompts, new prompts, scrollback review, width-aware footer, role blocks, Markdown preview, tables, status lines, token and rate-limit lines, error styling, folded long tool output previews, refresh, files mode, empty states, and full-width chrome.
Оставшаяся работа больше похожа на финальную приемку и несколько архитектурных долгов, а не на продолжение большого UI-строительства.

## Что обязательно проверить перед завершением ветки

- Прогнать ручной smoke test в настоящем терминале через `ctui tui`.
- Проверить обычный размер терминала около 80x24.
- Проверить узкий терминал около 50 колонок.
- Проверить первый запуск без истории или с пустыми фильтрами.
- Проверить длинный transcript с Markdown, таблицами, code fences и tool output.
- Проверить live stream нового prompt через `n`.
- Проверить resume stream выбранной сессии через `Enter`.
- Проверить post-stream review с arrows и PageUp/PageDown.
- Проверить failure stream, где Codex или tool command завершается с ошибкой.
- Проверить, что default TUI нигде не показывает raw JSON или внутренние `--json` детали.
- Проверить no-color terminal режим, если окружение позволяет быстро воспроизвести его.

## Что осталось из исходного renderer plan

- Полная block-model архитектура еще не реализована как отдельные `render_model.py` и `render_blocks.py`.
- Исторический preview и live stream уже визуально сближены, но еще не используют единый typed block renderer.
- Длинный tool output сейчас складывается до компактного preview до попадания в curses, поэтому настоящий expand or collapse toggle в TUI невозможен без сохранения полного output в модели.
- Клавиша `t` для toggle folded tool output из renderer plan еще не добавлена.
- Регрессионный golden harness для визуальных renderer snapshots еще не добавлен.
- Реальные scrubbed stream fixtures стоит вынести из локального harness в `tests/fixtures/`, если найдутся новые безопасные event shapes.

## Что можно считать неблокирующим

- Цвета не обязаны пиксельно совпадать с Codex VSCode plugin, потому что terminal themes сильно различаются.
- Mouse interaction не нужен для текущей версии.
- Persisted folded state per session можно отложить.
- Debug-only raw event inspection можно отложить, пока default views остаются чистыми.
- Compression остается явно не реализованной v0.1 фичей и не относится к завершению TUI.

## Самый разумный следующий шаг

Следующий маленький шаг - провести ручной TUI smoke test в реальном терминале и записать только конкретные визуальные дефекты.
Если smoke test не найдет явных проблем с layout, wrapping, focus, stream review или raw JSON leaks, ветку можно считать близкой к красивому TUI и завершать.
Если дефект найдется, лучше исправить один видимый дефект за итерацию и снова прогнать focused TUI tests plus full unittest discovery.

## Критерии "готово"

- `python3 -m unittest discover -s tests` проходит.
- `PYTHONPATH=src python3 -m codex_tui --version` проходит.
- `PYTHONPATH=src python3 -m codex_tui doctor` проходит без failures.
- `git diff --check` не находит whitespace issues.
- Ручной `ctui tui` выглядит аккуратно в обычном и узком терминале.
- Preview and stream panes остаются readable после scroll, PageUp/PageDown, mode switching, new prompt и resume prompt.
- Raw Codex JSON не попадает в default пользовательские views.
- Известный warning `ctui command is not on PATH` можно считать окруженческим, если запуск идет через `PYTHONPATH=src python3 -m codex_tui`.
