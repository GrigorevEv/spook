from __future__ import annotations

import asyncio
import json
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Callable

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, PackageLoader, select_autoescape

from spook import notes as notes_module
from spook import settings as settings_module
from spook.agent import AgentQueue
from spook.audio import AudioInput, CaptureUnavailable, Source
from spook.clock import Clock
from spook.cutting import SpeechDetectorFactory
from spook.note import TOPIC_MAX_LENGTH
from spook.recognizer import Recognizer
from spook.session import (
    AgentChanged,
    BacklogChanged,
    CommandRejected,
    LevelsChanged,
    MarkAdded,
    ParagraphAdded,
    ParagraphUpdated,
    SessionService,
    SessionState,
    SessionView,
    StateChanged,
)
from spook.session_record import RECORD_NAME
from spook.settings import (
    EFFORTS,
    WHOLE_FILE_KEY,
    SessionKind,
    Settings,
    SettingsRejected,
    save_screen_settings,
    screen_values,
)
from spook.silero import SileroSpeechDetection
from spook.transcript import Mark

KEEPALIVE_SECONDS = 15
_RELOAD_PAGE = {"HX-Refresh": "true"}


def create_app(
    load_settings: Callable[[], Settings],
    audio_input: AudioInput,
    recognizer: Recognizer,
    clock: Clock,
    state_dir: Path | None = None,
    speech_detector: SpeechDetectorFactory | None = None,
    settings_path: Path | None = None,
) -> FastAPI:
    state_dir = state_dir or settings_module.state_dir()
    agent = AgentQueue(load_settings, log_dir=state_dir / "agent")
    service = SessionService(
        load_settings,
        audio_input,
        recognizer,
        clock,
        agent,
        speech_detector or SileroSpeechDetection(),
        record_path=state_dir / RECORD_NAME,
    )
    _restore_agent_queue_first(agent, service)
    templates = Environment(
        loader=PackageLoader("spook", "templates"),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    templates.tests["mark"] = lambda entry: isinstance(entry, Mark)
    templates.globals["TOPIC_MAX_LENGTH"] = TOPIC_MAX_LENGTH
    templates.globals["rewrite_warning"] = notes_module.REWRITE_WARNING
    agent_check_and_enqueue = threading.Lock()

    def render(name: str, **context: object) -> str:
        return templates.get_template(name).render(**context)

    streams = EventStreams()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        streams.reopen()
        yield

    app = FastAPI(title="Spook", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.close_event_streams = streams.close
    app.mount("/static", StaticFiles(packages=[("spook", "static")]), name="static")

    @app.get("/", response_class=HTMLResponse)
    def session_page() -> str:
        return render("session.html", view=service.view())

    def session_command(action: Callable[[], object]) -> HTMLResponse:
        try:
            action()
        except CommandRejected as rejected:
            return _rejected(render, str(rejected))
        return HTMLResponse("")

    async def meeting_requested(request: Request) -> bool:
        return (await request.form()).get("kind", "meeting") == "meeting"

    @app.post("/session/start", response_class=HTMLResponse)
    def start_session(meeting: bool = Depends(meeting_requested), topic: str = Form("")) -> HTMLResponse:
        if not meeting:
            return _rejected(render, "Неизвестный вид Сессии")
        return session_command(lambda: service.start(topic=topic))

    @app.post("/session/pause", response_class=HTMLResponse)
    def pause_session() -> HTMLResponse:
        return session_command(service.pause)

    @app.post("/session/resume", response_class=HTMLResponse)
    def resume_session() -> HTMLResponse:
        return session_command(service.resume)

    @app.post("/session/stop", response_class=HTMLResponse)
    def stop_session() -> HTMLResponse:
        return session_command(service.stop)

    @app.post("/session/topic", response_class=HTMLResponse)
    def set_topic(topic: str = Form("")) -> HTMLResponse:
        return session_command(lambda: service.set_topic(topic))

    def session_note() -> Path | None:
        view = service.view()
        return view.note_path if view.state is not SessionState.NONE else None

    @app.get("/notes", response_class=HTMLResponse)
    def notes_page() -> str:
        settings = load_settings()
        return render(
            "notes.html",
            entries=notes_module.list_notes(settings, agent, session_note()),
            agent_choices=notes_module.agent_choices(settings),
        )

    @app.post("/notes/wisp", response_class=HTMLResponse)
    def open_in_wisp(note: str = Form("")) -> HTMLResponse:
        settings = load_settings()
        try:
            path, _ = notes_module.find_note(settings, note)
            notes_module.open_uri(settings.programs.open, path.as_uri())
        except notes_module.NoteRefused as refused:
            return _rejected(render, str(refused))
        return HTMLResponse(render("_done.html", text=f"Открываю в Wisp: {path.name}"))

    @app.post("/notes/agent", response_class=HTMLResponse)
    def run_agent(
        note: str = Form(""), model: str = Form(""), effort: str = Form(""), confirm: str = Form("")
    ) -> HTMLResponse:
        with agent_check_and_enqueue:
            try:
                path = notes_module.run_agent(
                    load_settings(), agent, note, model, effort, confirm == "yes", session_note()
                )
            except notes_module.NoteRefused as refused:
                return _rejected(render, str(refused))
        return HTMLResponse(
            render("_done.html", text=f"Агент поставлен в очередь: {path.stem}"), headers=_RELOAD_PAGE
        )

    @app.get("/notes/row", response_class=HTMLResponse)
    def note_row(note: str = "") -> HTMLResponse:
        settings = load_settings()
        try:
            path, kind = notes_module.find_note(settings, note)
            entry = notes_module.describe(path, kind, agent, session_note())
        except notes_module.NoteRefused:
            entry = None
        if entry is None:
            return HTMLResponse("", headers=_RELOAD_PAGE)
        return HTMLResponse(
            render("_note_row.html", entry=entry, agent_choices=notes_module.agent_choices(settings))
        )

    @app.get("/agent/log", response_class=PlainTextResponse)
    def agent_log(note: str, kind: str = "") -> PlainTextResponse:
        try:
            journal = agent.read_log(note, SessionKind(kind) if kind else None)
        except ValueError:
            journal = None
        if journal is None:
            return PlainTextResponse("Журнала нет", status_code=404)
        return PlainTextResponse(journal)

    @app.post("/agent/topic", response_class=PlainTextResponse)
    def agent_topic(note: str = Form(), topic: str = Form("")) -> PlainTextResponse:
        try:
            new_path = service.rename_note(Path(note), topic)
        except CommandRejected as rejected:
            return PlainTextResponse(str(rejected), status_code=409)
        return PlainTextResponse(str(new_path))

    def settings_screen(values: dict[str, str], errors: dict[str, str], saved: bool, status_code: int) -> HTMLResponse:
        return HTMLResponse(
            render(
                "settings.html",
                values=values,
                errors=errors,
                saved=saved,
                kinds=list(SessionKind),
                efforts=EFFORTS,
                models=notes_module.MODELS,
                sources=[_device_choice(audio_input, source, values[f"sources.{source.value}"]) for source in Source],
                can_save=settings_path is not None,
            ),
            status_code=status_code,
        )

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(saved: str = "") -> HTMLResponse:
        return settings_screen(screen_values(load_settings()), {}, saved == "1", 200)

    @app.post("/settings", response_model=None)
    async def save_settings(request: Request) -> HTMLResponse | RedirectResponse:
        form = {key: value for key, value in (await request.form()).items() if isinstance(value, str)}
        values = {**screen_values(load_settings()), **form}
        if settings_path is None:
            return settings_screen(values, {WHOLE_FILE_KEY: "Здесь настройки не сохраняются: служба запущена без файла настроек"}, False, 409)
        try:
            save_screen_settings(settings_path, form)
        except SettingsRejected as rejected:
            return settings_screen(values, rejected.errors, False, 422)
        return RedirectResponse("/settings?saved=1", status_code=303)

    @app.get("/events")
    async def events() -> StreamingResponse:
        return StreamingResponse(
            _event_stream(service, render, streams),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def _restore_agent_queue_first(agent: AgentQueue, service: SessionService) -> None:
    agent.restore()
    service.restore()


def _device_choice(audio_input: AudioInput, source: Source, pinned: str) -> dict[str, object]:
    options = [("", "по умолчанию")]
    problem = ""
    try:
        devices = audio_input.devices(source)
    except CaptureUnavailable as error:
        devices, problem = [], f"Список устройств недоступен: {error}"
    options += [(device.name, device.description) for device in devices]
    if pinned and pinned not in {device.name for device in devices}:
        options.append((pinned, f"{pinned} (сейчас нет)"))
    return {"source": source, "options": options, "problem": problem}


def _rejected(render: Callable[..., str], reason: str) -> HTMLResponse:
    return HTMLResponse(render("_notice.html", reason=reason), status_code=409)


class EventStreams:
    def __init__(self) -> None:
        self.closing = False
        self._queues: set[asyncio.Queue] = set()

    def open(self, queue: asyncio.Queue) -> None:
        self._queues.add(queue)

    def forget(self, queue: asyncio.Queue) -> None:
        self._queues.discard(queue)

    def reopen(self) -> None:
        self.closing = False

    def close(self) -> None:
        self.closing = True
        for queue in self._queues:
            queue.put_nowait(_CLOSE)


_CLOSE = object()


async def _event_stream(
    service: SessionService, render: Callable[..., str], streams: EventStreams
) -> AsyncIterator[str]:
    view, subscription = service.subscribe(asyncio.get_running_loop())
    streams.open(subscription.queue)
    try:
        if streams.closing:
            return
        yield _full_state(render, view)
        while True:
            try:
                event = await asyncio.wait_for(subscription.queue.get(), timeout=KEEPALIVE_SECONDS)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            if event is _CLOSE:
                return
            if isinstance(event, StateChanged):
                yield _full_state(render, event.view)
            elif isinstance(event, ParagraphAdded):
                yield _sse("paragraph", render("_paragraph.html", paragraph=event.paragraph))
            elif isinstance(event, ParagraphUpdated):
                yield _sse("paragraph-update", render("_paragraph.html", paragraph=event.paragraph, replace=True))
            elif isinstance(event, MarkAdded):
                yield _sse("mark", render("_mark.html", mark=event.mark))
            elif isinstance(event, LevelsChanged):
                yield _sse("levels", _levels_json(event))
            elif isinstance(event, AgentChanged):
                yield _sse("agent", render("_agent.html", jobs=event.jobs))
            elif isinstance(event, BacklogChanged):
                yield _backlog(render, event.view)
    finally:
        streams.forget(subscription.queue)
        service.unsubscribe(subscription)


def _levels_json(event: LevelsChanged) -> str:
    return json.dumps(
        {
            source.value: {"rms": round(level.rms, 4), "peak": round(level.peak, 4)}
            for source, level in event.levels.items()
        }
    )


def _full_state(render: Callable[..., str], view: SessionView) -> str:
    return (
        _sse("state", render("_state.html", view=view))
        + _sse("transcript", render("_transcript.html", view=view))
        + _sse("agent", render("_agent.html", jobs=view.agent))
        + _backlog(render, view)
    )


def _backlog(render: Callable[..., str], view: SessionView) -> str:
    return _sse("backlog", render("_backlog.html", view=view))


def _sse(event: str, html: str) -> str:
    data = "".join(f"data: {line}\n" for line in html.strip().split("\n"))
    return f"event: {event}\n{data}\n"
