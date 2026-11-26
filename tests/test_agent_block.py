from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape

from conftest import NO_SPEECH, speech

FIRST_NOTE = "2026-11-06 14-02 Встреча"
SECOND_NOTE = "2026-11-06 15-02 Встреча"


@dataclass(frozen=True)
class ShownJob:
    state: str
    reason: str = ""


def agent_block(page: str) -> str:
    return page.split('<section id="agent"', 1)[1].split(">", 1)[1].split("</section>", 1)[0]


def shown_jobs(fragment: str) -> dict[str, ShownJob]:
    jobs = {}
    for job in fragment.split('<div class="agent-job"')[1:]:
        name = re.search(r'<p class="note-name">([^<]*)</p>', job).group(1)
        state = re.search(r'<span class="summary">([^<]*)</span>', job).group(1)
        reason = re.search(r'<p class="error">([^<]*)</p>', job)
        jobs[unescape(name)] = ShownJob(unescape(state).removesuffix("…"), unescape(reason.group(1)) if reason else "")
    return jobs


def jobs_on_screen(spook) -> dict[str, ShownJob]:
    return shown_jobs(agent_block(spook.page().text))


def test_session_without_utterances_leaves_agent_block_empty(spook):
    spook.recognizer.say(NO_SPEECH)
    spook.start_meeting()

    with spook.events() as events:
        spook.feed_system(speech(20))
        spook.stop()
        events.wait_for("state", containing="нет Сессии")

    assert agent_block(spook.page().text).strip() == ""
    assert spook.agent.calls() == [] and spook.notify.calls() == []


def test_screen_shows_running_for_first_note_and_queued_for_second_then_both_done_in_order(spook):
    spook.agent.play("wait")
    spook.recognizer.say("Первая встреча.")
    spook.start_meeting()
    with spook.events() as events:
        spook.feed_system(speech(20))
        events.wait_for("paragraph", containing="Первая встреча.")
        spook.stop()
        events.wait_for("agent", containing="работает")
    spook.clock.advance(60 * 60)
    spook.recognizer.say("Вторая встреча.")
    spook.start_meeting()
    with spook.events() as events:
        spook.feed_system(speech(20))
        events.wait_for("paragraph", containing="Вторая встреча.")
        spook.stop()
        events.wait_for("agent", containing="в очереди")

    assert jobs_on_screen(spook) == {FIRST_NOTE: ShownJob("работает"), SECOND_NOTE: ShownJob("в очереди")}

    spook.wait_until(lambda: len(spook.agent.calls()) == 1)
    spook.agent.release()
    spook.wait_until(lambda: len(spook.agent.calls()) == 2)
    spook.agent.release()
    spook.wait_until(lambda: jobs_on_screen(spook) == {FIRST_NOTE: ShownJob("готово"), SECOND_NOTE: ShownJob("готово")})
    assert [call["argv"][1] for call in spook.agent.calls()] == [
        f"/spook-meeting {spook.meetings_dir / FIRST_NOTE}.md",
        f"/spook-meeting {spook.meetings_dir / SECOND_NOTE}.md",
    ]
