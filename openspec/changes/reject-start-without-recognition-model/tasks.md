## 1. «Старт» без модели распознавания отклоняется

- [ ] 1.1 Тест сценария «Модели распознавания нет» в `tests/test_start_without_recognition_model.py`: через HTTP-интерфейс службы (фикстура `spook`), распознаватель — `GigaamRecognizer` с пустой временной папкой модели; «Старт» Лекции и «Старт» Встречи получают 409 с причиной «Модель распознавания не скачана в <папка>: выполните `spook prepare`», состояние «нет Сессии», папки Лекций и Встреч не созданы, захват не начат. Проверка: тест падает, потому что «Старт» проходит (200 вместо 409).
- [ ] 1.2 `check_available()` у `Recognizer`: `GigaamRecognizer` бросает `RecognitionUnavailable`, если файлов модели нет, и модель не загружает, а `_load` вызывает ту же проверку; `ScriptedRecognizer` и `CyclingRecognizer` всегда готовы, `MissingRecognizer` никогда; `SessionService.start` вызывает проверку сразу после проверки состояния и превращает `RecognitionUnavailable` в `CommandRejected`. Проверка: тест 1.1 зелёный, `uv run pytest -q` зелёный, включая `tests/test_demo.py`.

## 2. После `spook prepare` «Старт» проходит без перезапуска службы

- [ ] 2.1 Тест сценария «После `spook prepare` «Старт» проходит без перезапуска службы» в том же файле: после отказа в ту же папку кладутся файлы с именами файлов модели и заглушками вместо содержимого, «Старт» Лекции получает 200, состояние «идёт», модель не загружена. Проверка: тест зелёный, и он краснеет, если временно загрузить модель в `check_available()` или запомнить итог проверки при запуске службы.

## 3. Проверки

- [ ] 3.1 `uv run pytest -q` и `openspec validate --all --strict` зелёные.

## Workflow follow-up

- `openspec-verify-change reject-start-without-recognition-model`, исправить найденное.
- `openspec-archive-change reject-start-without-recognition-model` — последний коммит PR.
- PR с `Fixes #31`.
