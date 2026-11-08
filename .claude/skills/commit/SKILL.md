---
name: commit
description: Создание git-коммита по текущим изменениям (сообщение на английском)
disable-model-invocation: true
context: fork
model: haiku
effort: low
---

Закоммить текущие изменения. Пожелания к сообщению: $ARGUMENTS

Не менять git config, не пропускать hooks, не пушить.

## Сообщение

Язык английский, даже если `git log` или `$ARGUMENTS` на русском; имена API, путей и символов кода не переводить.

- Title: одна строка в повелительном наклонении («Fix null check in auth», не «Fixed…»).
- Body: пустая строка, затем список `-`, каждый пункт в повелительном наклонении, кратко, зачем.
- Trailer: пустая строка, затем `Co-Authored-By: Claude <noreply@anthropic.com>`.

```text
Fix race when refreshing a session

- serialize token rotation under a per-user lock
- add a regression test for parallel refresh

Co-Authored-By: Claude <noreply@anthropic.com>
```

## Шаги

1. Параллельно: `git status`, `git diff`, `git diff --staged`, `git log -n 10 --oneline` (смотреть длину и структуру, не язык).
2. Застейджить нужные файлы. Секреты (`.env`, ключи, credentials) не добавлять, при просьбе предупредить.
3. `git commit -m "$(cat <<'MSG' ... MSG)"` с сообщением по формату выше.
4. Hook отклонил — исправить и сделать новый коммит, не amend. Amend только по явной просьбе или если hook поправил файлы после успешного коммита, а HEAD создан в этой сессии и не запушен.
5. `git status`. При успехе выполнить `git log -1 --decorate` (из-за отсутствия tty decorate нужно указать явно) и ответить дословно его выводом, без своих комментариев. При ошибке — кратко причиной.
