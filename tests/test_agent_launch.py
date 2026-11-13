"""Запуск настоящего Claude Code и причины ошибок — на заглушке, которая проигрывает вывод из пробы.

Образцы в `tests/data/claude/` — строки вывода `--output-format stream-json --verbose` из пробы
(задача 02; сырые выводы — в архиве истории (см. `AGENTS.md`), тег `tran-v1` в `.scratch/tran-v1/artifacts/02-headless-skill-probe/`):

- `hook-blocked.jsonl` — № 03, блокировка хуком `UserPromptSubmit` (код возврата 0);
- `success-with-denials.jsonl` — № 04 без сообщений модели: хуки, `init`, загрузка лимита,
  два `permission_denied`, итог `result` с `permission_denials` (код 0);
- `no-network.jsonl` — № 07 (десять `api_retry`) и итог того же сценария из № 08 (код 1);
- `bad-flags.stderr` — № 02, неверные флаги: stdout пуст, причина в stderr (код 1);
- `rate-limit.jsonl`, `api-error.jsonl` — в пробе не вызывались; собраны из настоящих строк по
  документации: `rate_limit_event` со `status: "rejected"`, `api_retry` с `error: "rate_limit"`,
  итог с `is_error: true` и `api_error_status` 429 или 500.
"""

from __future__ import annotations

from pathlib import Path

from conftest import SseEvent, speech

NOTE_NAME = "2026-11-06 14-02 Лекция"
SAMPLES_DIR = Path(__file__).parent / "data" / "claude"


def run_agent(spook, scenario: str, until: str) -> SseEvent:
    """Лекция с одной Репликой, «Стоп», Агент по сценарию заглушки; событие `agent` с итогом `until`."""
    spook.agent.play(scenario)
    spook.recognizer.say("Реплика.")
    spook.start_lecture()
    with spook.events() as events:
        spook.audio.feed(speech(20))
        events.wait_for("paragraph", containing="Реплика.")
        spook.stop()
        return events.wait_for("agent", containing=until)


def journal(spook) -> str:
    return (spook.state_dir / "agent" / "lecture" / f"{NOTE_NAME}.log").read_text(encoding="utf-8")


def test_meeting_agent_works_in_meetings_folder(spook):
    spook.agent.play("summary")
    spook.recognizer.say("Привет.")
    spook.start_meeting()
    with spook.events() as events:
        spook.audio.feed(speech(20))
        events.wait_for("paragraph", containing="Привет.")
        spook.stop()
        events.wait_for("agent", containing="готово")

    assert [call["cwd"] for call in spook.agent.calls()] == [str(spook.meetings_dir)]


def denied_rules(*folders) -> list[str]:
    """Запреты запуска: правка `.claude/` на любой глубине в рабочем каталоге и Хранилищах (настройки
    и хуки следующего запуска) и правка `_tools/`, `_templates/` Вики. Форма `Edit(//абсолютный путь)`
    по документации Claude Code (Permissions, «Read and Edit»): `//` — от корня файловой системы,
    правило `Edit` действует на все встроенные инструменты записи."""
    *folders, wiki = folders
    return [
        *(f"Edit(/{folder}/**/.claude/**)" for folder in folders),
        f"Edit(/{wiki}/_tools/**)",
        f"Edit(/{wiki}/_templates/**)",
    ]


def test_lecture_agent_is_denied_editing_claude_settings_and_wiki_tools_whatever_skill_allows(spook):
    run_agent(spook, "summary", until="готово")

    [call] = spook.agent.calls()
    flag = call["argv"].index("--disallowedTools")
    # Рабочий каталог Лекции — корень Вики, он первым; затем папки Встреч и Лекций.
    assert call["argv"][flag + 1 :] == denied_rules(spook.wiki_dir, spook.meetings_dir, spook.lectures_dir, spook.wiki_dir)


def test_meeting_agent_is_denied_the_same_and_its_folder_claude_settings(spook):
    spook.agent.play("summary")
    spook.recognizer.say("Привет.")
    spook.start_meeting()
    with spook.events() as events:
        spook.audio.feed(speech(20))
        events.wait_for("paragraph", containing="Привет.")
        spook.stop()
        events.wait_for("agent", containing="готово")

    [call] = spook.agent.calls()
    flag = call["argv"].index("--disallowedTools")
    # Рабочий каталог Встречи — папка Встреч, она первой; затем папка Лекций и Вики.
    rules = denied_rules(spook.meetings_dir, spook.lectures_dir, spook.wiki_dir, spook.wiki_dir)
    assert call["argv"][flag + 1 : flag + 1 + len(rules)] == rules
    assert call["argv"][flag + 1 + len(rules)] == "--append-system-prompt-file"


def test_run_blocked_by_hook_gives_error_asking_to_check_vpn_and_journal_keeps_output_as_is(spook):
    agent = run_agent(spook, "replay hook-blocked 0", until="ошибка")

    assert "Запуск заблокирован хуком (проверьте VPN)" in agent.data
    assert "Запуск заблокирован хуком (проверьте VPN)" in spook.page().text
    assert (SAMPLES_DIR / "hook-blocked.jsonl").read_text(encoding="utf-8") in journal(spook)


def test_run_without_network_gives_error_no_network(spook):
    agent = run_agent(spook, "replay no-network 1", until="ошибка")

    assert "Нет сети" in agent.data
    assert "Код возврата: 1. Выжимка: ошибка — Нет сети" in journal(spook)


def test_run_that_hit_subscription_limit_gives_error_limit_exhausted(spook):
    agent = run_agent(spook, "replay rate-limit 1", until="ошибка")

    assert "Исчерпан лимит подписки" in agent.data
    assert "Исчерпан лимит подписки" in spook.page().text


def test_successful_run_with_permission_denials_is_done_with_warning_naming_denied_tools(spook):
    agent = run_agent(spook, "replay success-with-denials 0 summary", until="готово")

    denials = (
        "Отказ в правах: Read /home/ev/.cache/tran-probe/notes/outside.md;"
        " Bash cat /home/ev/.cache/tran-probe/notes/outside.md"
    )
    assert denials in agent.data
    assert denials in spook.page().text
    assert denials in journal(spook)


def test_run_with_permission_denials_and_no_summary_gives_error_with_denials_as_reason(spook):
    agent = run_agent(spook, "replay success-with-denials 0", until="ошибка")

    assert "ошибка" in agent.data
    assert "Отказ в правах: Read /home/ev/.cache/tran-probe/notes/outside.md" in agent.data
    assert "Выжимки в Заметке нет" not in agent.data


def test_unrecognized_api_error_is_shown_with_message_of_the_run(spook):
    agent = run_agent(spook, "replay api-error 1", until="ошибка")

    assert "API Error: 500 Internal server error" in agent.data


def test_run_that_failed_before_output_shows_its_stderr(spook):
    agent = run_agent(spook, "replay bad-flags 1", until="ошибка")

    assert "Error: When using --print, --output-format=stream-json requires --verbose" in agent.data


def test_run_longer_than_timeout_is_stopped_and_gives_error_time_exceeded(spook):
    spook.write_settings(agent={"timeout_minutes": 0.02})  # 1,2 секунды; заглушка висит 30

    agent = run_agent(spook, "hang", until="ошибка")

    assert "Превышено время" in agent.data
    assert "Превышено время" in journal(spook)


def test_launch_can_be_wrapped_by_command_from_settings_with_timeout_in_seconds(spook):
    spook.write_settings(agent={"timeout_minutes": 2, "wrapper": "env SPOOK_UNIT_LIMIT={timeout}"})

    run_agent(spook, "summary", until="готово")

    assert journal(spook).startswith(f"$ env SPOOK_UNIT_LIMIT=120 {spook.agent.path} -p ")
    assert len(spook.agent.calls()) == 1


SKILL_WITH_MODEL = (
    "---\n"
    "name: spook-lecture\n"
    "description: Выжимка Лекции.\n"
    "model: sonnet\n"
    "effort: high\n"
    "allowed-tools:\n"
    "  - Read\n"
    "---\n"
    "\n"
    "Заметка: $ARGUMENTS\n"
)

MODEL_IN_SKILL_WARNING = (
    "В шапке Скилла spook-lecture указаны model, effort — они перебивают модель и effort из настроек Spook."
)


def test_model_and_effort_in_project_skill_header_give_warning_on_page_and_in_journal(spook):
    skill = spook.wiki_dir / ".claude" / "skills" / "spook-lecture" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(SKILL_WITH_MODEL, encoding="utf-8")

    agent = run_agent(spook, "summary", until="готово")

    assert MODEL_IN_SKILL_WARNING in agent.data
    assert MODEL_IN_SKILL_WARNING in spook.page().text
    assert MODEL_IN_SKILL_WARNING in journal(spook)


def test_model_in_user_skill_header_gives_warning(spook):
    # Каталог Claude Code пользователя в тестах — `$CLAUDE_CONFIG_DIR` из обвязки (`conftest.py`).
    skill = spook.state_dir.parent.parent / "claude-config" / "skills" / "spook-lecture" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(SKILL_WITH_MODEL.replace("effort: high\n", ""), encoding="utf-8")

    agent = run_agent(spook, "summary", until="готово")

    assert "В шапке Скилла spook-lecture указаны model — они перебивают" in agent.data


def test_model_mentioned_only_in_skill_text_below_header_gives_no_warning(spook):
    skill = spook.wiki_dir / ".claude" / "skills" / "spook-lecture" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        SKILL_WITH_MODEL.replace("model: sonnet\n", "").replace("effort: high\n", "")
        + "\nmodel: так в тексте Скилла можно писать.\n",
        encoding="utf-8",
    )

    agent = run_agent(spook, "summary", until="готово")

    assert "В шапке Скилла" not in agent.data
