"""Разбор вывода запуска Claude Code: чем закончился запуск и почему, словами для пользователя.

Вывод — поток `--output-format stream-json --verbose`: по JSON-событию в строке. Итог берётся из
последнего события `result`; код возврата различает мало (0 и при успехе, и при блокировке хуком),
а `subtype` остаётся `"success"` даже при `is_error: true`. Признаки исходов — по пробе
(`docs/research/headless-skill-probe.md`, «Как приложению различать исходы»):

- блокировка хуком `UserPromptSubmit` (у пользователя — выключенный VPN): событие `system/informational`
  с `prompt_submit_outcome.kind: "blocked_by_hook"` или `result`, начинающийся с
  «UserPromptSubmit operation blocked by hook:»;
- исчерпан лимит подписки (в пробе не вызывался, по документации): `rate_limit_event` со
  `status: "rejected"`, `system/api_retry` с `error: "rate_limit"`, итог с `api_error_status: 429`
  или `terminal_reason: "blocking_limit"`;
- нет сети: итог с `is_error: true` и `api_error: "connection_failed"`;
- отказ в правах: непустой `permission_denials` в итоге (или события `system/permission_denied`).
  Запуск от этого не падает — это предупреждение; причиной ошибки отказ становится, только если
  Выжимки в итоге нет (это решает очередь по Заметке).

Строки, которые не разбираются как JSON, пропускаются: журнал хранит вывод как есть, разбор его не трогает.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

HOOK_BLOCKED = "Запуск заблокирован хуком (проверьте VPN)"
NO_NETWORK = "Нет сети"
RATE_LIMITED = "Исчерпан лимит подписки"
HOOK_BLOCKED_PREFIX = "UserPromptSubmit operation blocked by hook:"


@dataclass(frozen=True)
class Verdict:
    """Что говорит вывод: `failure` — причина ошибки (пусто, если запуск по выводу удался);
    `denials` — «Отказ в правах: …» или пусто."""

    failure: str = ""
    denials: str = ""


def read_output(stdout: str, stderr: str, returncode: int) -> Verdict:
    events = list(_events(stdout))
    result = next((event for event in reversed(events) if event.get("type") == "result"), None)
    denials = _denials(events, result)
    if _hook_blocked(events, result):
        return Verdict(HOOK_BLOCKED, denials)
    failed = returncode != 0 or (result is not None and (result.get("is_error") or _is_error_subtype(result)))
    if not failed:
        return Verdict("", denials)
    if _rate_limited(events, result):
        return Verdict(RATE_LIMITED, denials)
    if result is not None and result.get("api_error") == "connection_failed":
        return Verdict(NO_NETWORK, denials)
    return Verdict(_message(result, stdout, stderr, returncode), denials)


def _events(stdout: str):
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            yield event


def _hook_blocked(events: list[dict[str, Any]], result: dict[str, Any] | None) -> bool:
    for event in events:
        outcome = event.get("prompt_submit_outcome")
        if isinstance(outcome, dict) and outcome.get("kind") == "blocked_by_hook":
            return True
    return result is not None and str(result.get("result", "")).startswith(HOOK_BLOCKED_PREFIX)


def _rate_limited(events: list[dict[str, Any]], result: dict[str, Any] | None) -> bool:
    for event in events:
        info = event.get("rate_limit_info")
        if event.get("type") == "rate_limit_event" and isinstance(info, dict) and info.get("status") == "rejected":
            return True
        if event.get("subtype") == "api_retry" and event.get("error") == "rate_limit":
            return True
    if result is None:
        return False
    return (
        result.get("api_error_status") == 429
        or result.get("api_error") == "rate_limit"
        or result.get("terminal_reason") == "blocking_limit"
    )


def _is_error_subtype(result: dict[str, Any]) -> bool:
    return str(result.get("subtype", "")).startswith("error")


def _denials(events: list[dict[str, Any]], result: dict[str, Any] | None) -> str:
    if result is not None and isinstance(result.get("permission_denials"), list):
        items = [_denial(item) for item in result["permission_denials"] if isinstance(item, dict)]
    else:  # итога нет (запуск прерван) — отказы видны только событиями по ходу
        items = [
            _denial(event) for event in events if event.get("type") == "system" and event.get("subtype") == "permission_denied"
        ]
    return f"Отказ в правах: {'; '.join(items)}" if items else ""


def _denial(item: dict[str, Any]) -> str:
    tool = str(item.get("tool_name") or "инструмент")
    tool_input = item.get("tool_input")
    if isinstance(tool_input, dict):
        for key in ("file_path", "command", "url", "path", "pattern"):
            if tool_input.get(key):
                return f"{tool} {tool_input[key]}"
    return tool


def _message(result: dict[str, Any] | None, stdout: str, stderr: str, returncode: int) -> str:
    """Сообщение самого запуска: текст итога, ошибки итога, последняя строка stderr или не-JSON вывода."""
    if result is not None:
        if str(result.get("result", "")).strip():
            return _first_line(str(result["result"]))
        errors = [str(error) for error in result.get("errors") or [] if str(error).strip()]
        if errors:
            return "; ".join(_first_line(error) for error in errors)
    plain_stdout = "\n".join(line for line in stdout.splitlines() if not line.strip().startswith("{"))
    return _last_line(stderr) or _last_line(plain_stdout) or f"код возврата {returncode}"


def _first_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[0] if lines else ""


def _last_line(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1] if lines else ""
