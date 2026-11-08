"""Установка `spook install` / `spook uninstall`: приложение как инструмент `uv`, пользовательская служба
systemd, значок «Spook» в меню, Скиллы из репозитория и модель распознавания.

Внешние команды подменены записью вызовов, домашний каталог — временный. Настоящие проверщики
`desktop-file-validate` и `systemd-analyze --user verify` прогоняются на сгенерированных файлах.
"""

from __future__ import annotations

import configparser
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from spook.install import install, uninstall


class Recorder:
    """Подмена запуска внешних команд: запоминает вызовы."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str]) -> None:
        self.commands.append([str(part) for part in command])


def make_repo(tmp_path: Path, *skills: str) -> Path:
    repo = tmp_path / "repo"
    (repo / "skills").mkdir(parents=True)
    (repo / "pyproject.toml").write_text('[project]\nname = "spook"\n', encoding="utf-8")
    for name in skills:
        (repo / "skills" / name).mkdir()
        (repo / "skills" / name / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
    return repo


@pytest.fixture
def home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    return home


def read_ini(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str  # ключи с заглавными буквами
    parser.read(path, encoding="utf-8")
    return parser


def test_install_puts_tool_prepares_model_and_enables_service(tmp_path, home):
    repo = make_repo(tmp_path)
    run = Recorder()

    install(repo, home=home, port=8765, run=run)

    applications = home / ".local/share/applications"
    assert run.commands == [
        ["uv", "tool", "install", "--reinstall", str(repo)],
        [str(home / ".local/bin/spook"), "prepare"],
        ["update-desktop-database", str(applications)],
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "try-restart", "spook.service"],
        ["systemctl", "--user", "enable", "--now", "spook.service"],
    ]


def test_service_unit_runs_installed_spook_serve_with_path_to_user_programs(tmp_path, home):
    install(make_repo(tmp_path), home=home, port=8765, run=Recorder())

    unit_path = home / ".config/systemd/user/spook.service"
    unit = read_ini(unit_path)

    assert unit["Service"]["ExecStart"] == "%h/.local/bin/spook serve"
    assert unit["Service"]["Restart"] == "on-failure"
    # Environment= повторяется; configparser оставил бы только последнее значение.
    environment = [
        line.removeprefix("Environment=")
        for line in unit_path.read_text(encoding="utf-8").splitlines()
        if line.startswith("Environment=")
    ]
    path = next(item.removeprefix("PATH=") for item in environment if item.startswith("PATH=")).split(":")
    assert path[0] == "%h/.local/bin"  # Claude Code, uv и сама `spook` для `spook topic` Агента
    assert "/usr/bin" in path  # notify-send, xdg-open
    # Служба поднимается вместе с графическим сеансом: тогда у неё есть DISPLAY, WAYLAND_DISPLAY и
    # XDG_CURRENT_DESKTOP для открытия файлов (в default.target их ещё нет).
    assert unit["Install"]["WantedBy"] == "graphical-session.target"
    assert "graphical-session.target" in unit["Unit"]["After"]


def test_menu_entry_spook_opens_tab_at_service_address(tmp_path, home):
    install(make_repo(tmp_path), home=home, port=9123, run=Recorder())

    entry = read_ini(home / ".local/share/applications/spook.desktop")["Desktop Entry"]

    assert entry["Name"] == "Spook"
    assert entry["Type"] == "Application"
    assert entry["Exec"] == "xdg-open http://localhost:9123/"


@pytest.mark.skipif(shutil.which("desktop-file-validate") is None, reason="нет desktop-file-validate")
def test_menu_entry_passes_desktop_file_validate(tmp_path, home):
    install(make_repo(tmp_path), home=home, port=8765, run=Recorder())

    result = subprocess.run(
        ["desktop-file-validate", str(home / ".local/share/applications/spook.desktop")],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0 and result.stdout + result.stderr == "", result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="нет systemd-analyze")
def test_service_unit_passes_systemd_analyze_verify(tmp_path, home):
    install(make_repo(tmp_path), home=home, port=8765, run=Recorder())
    spook = home / ".local/bin/spook"  # `%h` раскрывается в HOME; ExecStart обязан существовать
    spook.parent.mkdir(parents=True)
    spook.write_text("#!/bin/sh\n", encoding="utf-8")
    spook.chmod(0o755)

    result = subprocess.run(
        ["systemd-analyze", "--user", "verify", str(home / ".config/systemd/user/spook.service")],
        env={**os.environ, "HOME": str(home)},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0 and result.stdout + result.stderr == "", result.stdout + result.stderr


def test_skills_from_repository_are_linked_into_user_skills(tmp_path, home):
    repo = make_repo(tmp_path, "spook-probe")

    install(repo, home=home, port=8765, run=Recorder())

    link = home / ".claude/skills/spook-probe"
    assert link.is_symlink()
    assert link.resolve() == (repo / "skills/spook-probe").resolve()
    assert (link / "SKILL.md").exists()


def test_foreign_skill_with_same_name_is_left_untouched(tmp_path, home, capsys):
    repo = make_repo(tmp_path, "spook-probe")
    foreign = home / ".claude/skills/spook-probe"
    foreign.mkdir(parents=True)
    (foreign / "SKILL.md").write_text("чужой", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (repo / "skills/spook-other").mkdir()
    (repo / "skills/spook-other/SKILL.md").write_text("---\n", encoding="utf-8")
    (home / ".claude/skills/spook-other").symlink_to(elsewhere)

    install(repo, home=home, port=8765, run=Recorder())

    assert not foreign.is_symlink()
    assert (foreign / "SKILL.md").read_text(encoding="utf-8") == "чужой"
    assert (home / ".claude/skills/spook-other").resolve() == elsewhere.resolve()
    output = capsys.readouterr().out
    assert "spook-probe" in output and "spook-other" in output


def test_link_to_vanished_copy_of_skill_is_replaced(tmp_path, home):
    repo = make_repo(tmp_path, "spook-probe")
    (home / ".claude/skills").mkdir(parents=True)
    (home / ".claude/skills/spook-probe").symlink_to(tmp_path / "old-worktree/skills/spook-probe")

    install(repo, home=home, port=8765, run=Recorder())

    assert (home / ".claude/skills/spook-probe").resolve() == (repo / "skills/spook-probe").resolve()


def test_repeated_install_is_safe_and_changes_nothing(tmp_path, home):
    repo = make_repo(tmp_path, "spook-probe")
    install(repo, home=home, port=8765, run=Recorder())
    files = {
        path: path.read_text(encoding="utf-8")
        for path in (home / ".config/systemd/user/spook.service", home / ".local/share/applications/spook.desktop")
    }

    run = Recorder()
    install(repo, home=home, port=8765, run=run)

    assert {path: path.read_text(encoding="utf-8") for path in files} == files
    assert (home / ".claude/skills/spook-probe").resolve() == (repo / "skills/spook-probe").resolve()
    # Модель скачивает `spook prepare` только если её нет; служба перезапускается на новой копии.
    assert [str(home / ".local/bin/spook"), "prepare"] in run.commands
    assert ["systemctl", "--user", "try-restart", "spook.service"] in run.commands


def test_uninstall_removes_service_menu_entry_skill_links_and_tool(tmp_path, home):
    repo = make_repo(tmp_path, "spook-probe")
    install(repo, home=home, port=8765, run=Recorder())
    foreign = home / ".claude/skills/someone-else"
    foreign.mkdir()

    run = Recorder()
    uninstall(repo, home=home, run=run)

    assert run.commands == [
        ["systemctl", "--user", "disable", "--now", "spook.service"],
        ["systemctl", "--user", "daemon-reload"],
        ["update-desktop-database", str(home / ".local/share/applications")],
        ["uv", "tool", "uninstall", "spook"],
    ]
    assert not (home / ".config/systemd/user/spook.service").exists()
    assert not (home / ".local/share/applications/spook.desktop").exists()
    assert not (home / ".claude/skills/spook-probe").is_symlink()
    assert foreign.is_dir()
