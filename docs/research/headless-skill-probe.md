# Проба фонового запуска Скилла в Claude Code

Задача 02 (архив истории, см. `CLAUDE.md`: `git show tran-v1:.scratch/tran-v1/issues/02-headless-skill-probe.md`), проверка того, что ADR-0003 оставил непроверенным. Дата: 2026-11-07. Claude Code 2.1.319 (`/home/ev/.local/bin/claude`), подписка пользователя, модель пробы `claude-haiku-5-5`, effort `low`. Сырые выводы всех запусков — в архиве истории (см. `CLAUDE.md`), тег `tran-v1` в `.scratch/tran-v1/artifacts/02-headless-skill-probe/` (архив истории, см. `CLAUDE.md`: `git show tran-v1:<путь>`).

## Как проводилась

**Чистое окружение.** Каждый запуск — отдельный юнит `systemd-run --user --wait --pipe --collect --quiet -p WorkingDirectory=<папка вида> -p RuntimeMaxSec=180 /home/ev/.local/bin/claude … </dev/null`. Проверено скриптом `envcheck.sh`: в окружении юнита нет ни одной переменной `CLAUDE*`, stdin и stdout — не терминал, код возврата программы доходит до вызывающего (`exit 3` → `rc=3`), stderr проходит через `--pipe`. Программа вызвана по полному пути, функция `claude` из zsh не участвует. Хуки пользователя (`SessionStart`, `UserPromptSubmit` с проверкой VPN) при этом выполняются как в обычной сессии; VPN был включён.

**Подставная раскладка** (временная, в домашнем каталоге — иначе не проверить права вида `~/…/**`; удалена после пробы):

```
~/.cache/tran-probe/
  notes/
    работа/                               ← папка вида, рабочий каталог запуска
      .claude/skills/tran-probe/SKILL.md  ← временный проектный Скилл
      2026-11-06 14-02 Проба.md           ← Заметка по формату спецификации
    outside.md                            ← файл вне папки вида: «этот файл лежит вне папки вида»
  wiki/Словарь терминов.md                ← вне папки вида, но разрешён правилом в шапке
```

Настоящие Хранилища, `~/.claude/settings.json` и `~/.claude/skills/` не менялись и не читались Агентом. Скилл положен как проектный (`<папка вида>/.claude/skills/`), а не пользовательский: по журналу отладки оба каталога загружаются одним механизмом («Loading skills from: managed=…, user=~/.claude/skills, project=[<cwd>/.claude/skills]»), но сам пользовательский каталог в пробе не задействован.

**Скилл `tran-probe`** (варианты — `SKILL-v1…v4.md` в артефактах). Шапка без `model` и `effort`, права только путями:

```yaml
allowed-tools:
  - Read(~/.cache/tran-probe/notes/работа/**)
  - Edit(~/.cache/tran-probe/notes/работа/**)
  - Write(~/.cache/tran-probe/notes/работа/**)
  - Read(~/.cache/tran-probe/wiki/**)
  - Glob
  - Grep
```

Текст Скилла: прочитать Заметку и Словарь, попытаться получить `outside.md` инструментом Read, через Bash (`cat`), через Glob и Grep (во втором варианте — ещё создать файл снаружи инструментом Write), вставить над `## Транскрипт` Выжимку-заглушку и строку-итог по каждой попытке без содержимого файла, ничего ниже заголовка не трогать. Вариант «без Выжимки» только читает Заметку и отвечает одной строкой; в нём же в шапке стоят `model: sonnet` и `effort: high` для проверки, что сильнее — шапка или флаги.

**Блокировка хуком** имитирована файлом настроек через `--settings` с хуком `UserPromptSubmit`, который пишет причину в stderr и завершается кодом 2 (`settings-block-hook.json`, `block-hook.sh`). VPN не трогался; хук пользователя при этом тоже отработал (кодом 0).

**Отсутствие сети** имитировано переменной `ANTHROPIC_BASE_URL=https://api.nonexistent.tran-probe.invalid` у юнита (ошибка DNS, как при выключенной сети). `-p IPAddressDeny=any` для пользовательских юнитов не действует — `curl` из такого юнита получил ответ.

Целостность Заметки проверялась `check-note.sh`: контрольные суммы свойств (между `---`) и хвоста от `## Транскрипт` до конца, а также поиск текста `outside.md` в Заметке; дополнительно текст искался во всех stdout/stderr.

## Запуски

Все команды — в файлах `NN-*.cmd`. Общая часть: `systemd-run … /home/ev/.local/bin/claude -p "/tran-probe /home/ev/.cache/tran-probe/notes/работа/2026-11-06 14-02 Проба.md" --model claude-haiku-5-5 --effort low` плюс флаги из таблицы.

| № | Сценарий | Флаги | rc | Время | Что в выводе |
|---|---|---|---|---|---|
| 01 | Блокировка хуком | `--output-format json --permission-mode default --settings <хук exit 2>` | **0** | 2 с | Одна строка JSON: `subtype:"success"`, `is_error:false`, `num_turns:0`, `duration_api_ms:0`, `total_cost_usd:0`, `modelUsage:{}`, нет `terminal_reason`; `result` = `"UserPromptSubmit operation blocked by hook:\n[<команда хука>]: <stderr хука>\n\n\nOriginal prompt: …"`. stderr пуст. До модели дело не дошло. |
| 02 | То же, stream-json без `--verbose` | `--output-format stream-json` | 1 | 1 с | stdout пуст; stderr: `Error: When using --print, --output-format=stream-json requires --verbose`. |
| 03 | То же, stream-json | `--output-format stream-json --verbose --include-hook-events` | 0 | 1 с | События `system/hook_started` и `system/hook_response` (у хука пробы `exit_code:2`, `outcome:"error"`, `stderr` с причиной; у хука VPN `exit_code:0`), `system/init` (`permissionMode:"default"`, `model:"claude-haiku-5-5"`, `tran-probe` в `skills`), `system/informational` с `level:"warning"`, `prevent_continuation:true`, `prompt_submit_outcome:{kind:"blocked_by_hook", prompt_kept:false, reason:"<stderr хука>"}`, затем `result` как в № 01. |
| 04 | **Успех + отказы в правах**, режим default | `--output-format stream-json --verbose --permission-mode default` | 0 | 12 с | `num_turns:8`, стоимость $0,0044, `modelUsage:{claude-haiku-5-5}`. Заметка: Выжимка-заглушка вставлена над `## Транскрипт`, свойства и Транскрипт байт в байт прежние. Словарь (вне cwd, разрешён правилом `Read(~/…/wiki/**)`) **прочитан**. `outside.md`: Read — отказ, Bash `cat` — отказ, Glob/Grep — таких инструментов нет. `permission_denials`: Read и Bash. Подробности ниже. |
| 05 | Успех + отказы, режим acceptEdits, попытка Write снаружи | `--output-format json --permission-mode acceptEdits` | 0 | 17 с | `num_turns:9`, $0,0053, `terminal_reason:"completed"`, `api_error_status:null`, `result:"готово"`. Заметка дописана так же корректно. `permission_denials`: Read, Bash и **Write** `…/notes/outside-write.md` — файл не создан. acceptEdits не открывает запись за пределы рабочего каталога. |
| 06 | **Завершение без Выжимки**; в шапке `model: sonnet`, `effort: high` против флагов haiku/low | `--output-format json --permission-mode default --debug-file …` | 0 | 4 с | `subtype:"success"`, `is_error:false`, `num_turns:2`, `result:"проба без записи: Выжимка не написана"`, `permission_denials:[]` — по выводу неотличимо от успеха; Заметка не изменилась. `modelUsage` содержит **только `claude-sonnet-5-5`** ($0,0507 — в десять раз дороже haiku); в журнале отладки `dispatching to firstParty model=claude-sonnet-5-5`; в теле запроса (журнал сессии `~/.claude/projects/…`) `"output_config":{"effort":"high"}`, тогда как в № 04–05 там `"effort":"low"`. |
| 07 | Нет сети (ошибка DNS), stream-json, лимит 180 с | `--output-format stream-json --verbose`, `ANTHROPIC_BASE_URL=<несуществующий хост>` | 1 | 181 с | После `init` — десять событий `system/api_retry` (`attempt` 1…10, `max_retries:10`, `retry_delay_ms` 537 → 1063 → 2113 → 4629 → 9016 → 17485 → 37789 → 38505 → 34831 → 35924, `error_status:null`, `error:"unknown"`); `result` не успел появиться — юнит убит по `RuntimeMaxSec`. |
| 08 | Нет сети, json, лимит 400 с | `--output-format json` | **1** | 183 с | Claude Code сдался сам через 182 с: `subtype:"success"`, **`is_error:true`**, `terminal_reason:"api_error"`, `api_error:"connection_failed"`, `api_error_status:null`, `num_turns:1`, `stop_reason:"stop_sequence"`, `total_cost_usd:0`, `result:"API Error: Can't reach the API server — check your internet or DNS (ENOTFOUND)"`. stderr пуст. |

Платных запусков три (№ 04–06), суммарно около $0,06 по оценке Claude Code; остальные до модели не дошли.

### Отказы в правах подробно (№ 04–05)

Что получила модель в `tool_result` (`is_error:true`) и что пришло в поток событием `system/permission_denied`:

- **Read `…/notes/outside.md`** — `decision_reason_type:"workingDir"`, `decision_reason:"Path is outside allowed working directories"`, сообщение `Claude requested permissions to read from <путь>, but you haven't granted it yet.`
- **Bash `cat …/notes/outside.md`** — `cat in '<путь>' was blocked. For security, Claude Code may only concatenate files from the allowed working directories for this session: '<cwd>'.` Это отдельная встроенная защита для `cat`-подобных команд; произвольная команда без правила `Bash(...)` в шапке отклонялась бы как неразрешённая. Правило `Bash(...)` путями не ограничивается — разрешённая программа читает всё, что может прочитать процесс.
- **Glob и Grep** — `No such tool available: Glob. Glob is not available in this session — find files with 'find' via the Bash tool instead.` (аналогично для Grep). В 2.1.319 этих инструментов нет в списке `tools` события `init`; записи `Glob`/`Grep` в `allowed-tools` (в том числе у скилла `wiki`) ничего не дают, а поиск по файлам возможен только через Bash — то есть без правила `Bash(...)` Агент искать файлы не может.
- **Write `…/notes/outside-write.md`** (acceptEdits) — отклонён, файл не создан.

Чтение внутри рабочего каталога и чтение Словаря вне его по правилу `Read(~/.cache/tran-probe/wiki/**)` прошли без вопросов; Edit Заметки прошёл в режиме default благодаря правилу `Edit(~/…/работа/**)` из шапки. Итого: **права с путями `~/…/**` из шапки Скилла при запуске без диалога действуют** — и для Read за пределами cwd, и для Edit. Содержимое `outside.md` не попало ни в один stdout/stderr, ни в Заметку.

### Флаги, настройки, шапка

- `--model` перебивает `model` из `~/.claude/settings.json` (там `fable`, работала haiku). `--effort low` действовал (тело запроса `effort:"low"`).
- **`model` и `effort` в шапке Скилла перебивают флаги запуска** (№ 06). Приложение не может переопределить их флагами, поэтому в Скиллах Spook их быть не должно; приложению стоит проверять шапку выбранного Скилла и предупреждать, если они там есть.
- `--permission-mode default` принимается, в `init` виден как `permissionMode:"default"`; в справке 2.1.319 это значение называется `manual`. По документации без флага запуск `-p` стартует в том же режиме.
- `default` и `acceptEdits` для нашего Скилла дали одинаковый результат; `acceptEdits` ничем не помогает (Edit разрешён шапкой) и шире по правам — для службы достаточно `default`.
- `--bare` не подходит: отключает хуки (в том числе проверку VPN) и вход по подписке (нужен `ANTHROPIC_API_KEY`).
- `stream-json` требует `--verbose`; `--include-hook-events` добавляет события хуков. `json` — одна строка с теми же полями, что у события `result`.

## Как приложению различать исходы

Код возврата различает мало: 0 при успехе, **0 при блокировке хуком** и при завершении без Выжимки, 1 при ошибке API (нет сети), 1 при неверных флагах (текст в stderr, stdout пуст), 143 при SIGTERM (по документации). Решать нужно по JSON (`--output-format json` или событие `result` в `stream-json`); `subtype` при этом остаётся `"success"` даже при `is_error:true`, ориентироваться на него нельзя.

| Исход | Признаки |
|---|---|
| Успех | `is_error:false`, `num_turns ≥ 1`, `terminal_reason:"completed"`, в `modelUsage` ожидаемая модель; **и обязательно** — над `## Транскрипт` в Заметке появился текст. Только по выводу успех от «Агент ничего не написал» не отличить (№ 06). |
| Завершение без Выжимки | Всё как при успехе, но Заметка выше `## Транскрипт` пуста (кроме свойств) или не изменилась. Проверять содержимое, а не вывод. |
| Блокировка хуком (VPN) | `num_turns:0`, `duration_api_ms:0`, `modelUsage:{}`, нет `terminal_reason`, `result` начинается с `UserPromptSubmit operation blocked by hook:`; stderr хука внутри `result`. В `stream-json` надёжнее: `system/informational` с `prompt_submit_outcome.kind:"blocked_by_hook"` и `reason`, а с `--include-hook-events` — `hook_response` с `hook_event:"UserPromptSubmit"`, `exit_code:2`. |
| Отказ в правах | Запуск **не** падает: `is_error:false`, непустой `permission_denials` (`tool_name`, `tool_input`); в `stream-json` — `system/permission_denied` с `message`, `decision_reason_type`. Для службы это предупреждение в журнал, а не ошибка: Агент продолжает работу. |
| Нет сети | `is_error:true`, `api_error:"connection_failed"`, `terminal_reason:"api_error"`, `api_error_status:null`, `result` начинается с `API Error:`; rc=1. До итога — ~3 минуты (10 повторов). В `stream-json` первое `system/api_retry` с `error_status:null` приходит через секунду — можно прервать раньше и сообщить «нет сети». |
| Исчерпан лимит подписки | **Не вызван в пробе.** По документации Agent SDK: `rate_limit_event` с `rate_limit_info.status:"rejected"` (`"allowed_warning"` — близко к лимиту) и `errorCode:"credits_required"` для исчерпанной подписки; `system/api_retry` с `error:"rate_limit"` и `error_status:429`; в `result` ожидаются `is_error:true`, `api_error_status:429`, `terminal_reason` вида `"api_error"` или `"blocking_limit"`. В каждом удачном запуске приходило `rate_limit_event` со `status:"allowed"`, `rateLimitType`, `resetsAt` и `unifiedWindows.{five_hour,seven_day}.{utilization,resetsAt,status}` — приложение может показывать загрузку лимитов и предупреждать до запуска. Точный текст `result` при лимите нужно записать из журнала при первом настоящем случае. |
| Зависание / таймаут | `result` так и не приходит; юнит убивается по `RuntimeMaxSec` (rc=1) или SIGTERM (143). Таймаут службы должен быть больше ~4 минут, иначе сценарий «нет сети» всегда будет выглядеть как таймаут. |

Другие документированные `subtype` ошибок: `error_max_turns`, `error_during_execution`, `error_max_budget_usd`, `error_max_structured_output_retries` — у них поле `errors: string[]` вместо `result`; в пробе не встретились.

## Рекомендованная команда для службы

```sh
systemd-run --user --wait --pipe --collect --quiet \
  -p WorkingDirectory=<папка вида> -p RuntimeMaxSec=<таймаут, не меньше 300> \
  /home/ev/.local/bin/claude -p "/<скилл> <путь к Заметке>" \
  --output-format stream-json --verbose --include-hook-events \
  --permission-mode default --permission-prompts none \
  --model <модель> --effort <effort> \
  </dev/null
```

- Запускать по полному пути из окружения без переменных `CLAUDE*`, stdin — `/dev/null`, без TTY. Если служба сама работает как пользовательский юнит systemd, `systemd-run` можно заменить обычным `subprocess` с чистым `env` и `cwd`; `systemd-run` даёт в придачу `RuntimeMaxSec` и уборку процессов.
- `stream-json` вместо `json`: видны `api_retry`, `permission_denied`, `rate_limit_event` и блокировка хуком по ходу работы; весь поток и есть Журнал Агента из спецификации. Итоговые поля берутся из последнего события `result`.
- `--permission-prompts none` (документировано с 2.1.259) — чтобы Агенту прямо сообщалось, что подтверждать некому, и он не повторял отклонённые попытки. В пробе не проверялось: отказы и без него приходили сразу.
- Путь к Заметке передавать как есть (пробелы в имени не помешали); Скилл получает его в `$ARGUMENTS`.
- Перед запуском сверить шапку Скилла: нет `model`/`effort`, иначе выбор модели в настройках приложения не сработает.

## Что осталось неизвестным

- Вид вывода при исчерпанном лимите подписки — не вызвать нарочно; выше только документация и форма «разрешающего» `rate_limit_event`.
- Пробовался проектный Скилл в `<cwd>/.claude/skills/`, а не пользовательский в `~/.claude/skills/`; механизм один, но сам каталог не проверен.
- «Нет сети» имитировалось ошибкой DNS; при обрыве соединения с таймаутами время до итога может быть больше 3 минут.
- `--permission-prompts none` и `--no-session-persistence` не запускались.
- Как Агент обходится без Glob/Grep при настоящей работе (поиск связанных заметок): спецификация и так передаёт названия заметок Хранилища вместе с запуском; для `spook-lecture` правила `Bash(...)` скилла `wiki` остаются без изменений.
