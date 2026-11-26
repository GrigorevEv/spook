from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

SERVICE = "spook.service"
TOOL = "spook"

Runner = Callable[[list[str]], None]

SERVICE_UNIT = """\
[Unit]
Description=Spook: слушает звук и пишет Заметки
After=graphical-session.target pipewire.service wireplumber.service
Wants=pipewire.service
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart=%h/.local/bin/spook serve
Restart=on-failure
RestartSec=3
Environment=PATH=%h/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=graphical-session.target
"""


class InstallError(Exception):
    pass


def desktop_entry(port: int) -> str:
    return f"""\
[Desktop Entry]
Type=Application
Name=Spook
Comment=Заметки Встреч
Exec=xdg-open http://localhost:{port}/
Icon=audio-input-microphone
Terminal=false
Categories=Utility;
Keywords=spook;заметки;встреча;
StartupNotify=false
"""


def run_command(command: list[str]) -> None:
    print("$", " ".join(command), flush=True)
    try:
        subprocess.run(command, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise InstallError(f"Шаг установки не удался: {' '.join(command)}: {error}") from error


def install(repo: Path, home: Path, port: int, run: Runner = run_command) -> None:
    run(["uv", "tool", "install", "--reinstall", str(repo)])
    run([str(_spook(home)), "prepare"])
    _unlink_repo_skills(home / ".claude" / "skills", repo)
    _link_skills(repo, home)

    applications = _applications(home)
    _write(applications / "spook.desktop", desktop_entry(port))
    run(["update-desktop-database", str(applications)])

    _write(_unit(home), SERVICE_UNIT)
    run(["systemctl", "--user", "daemon-reload"])
    run(["systemctl", "--user", "try-restart", SERVICE])
    run(["systemctl", "--user", "enable", "--now", SERVICE])
    print(f"Spook установлен: служба {SERVICE} включена, вкладка — http://localhost:{port}/")


def uninstall(repo: Path, home: Path, run: Runner = run_command) -> None:
    unit = _unit(home)
    if unit.exists():
        run(["systemctl", "--user", "disable", "--now", SERVICE])
        unit.unlink()
        run(["systemctl", "--user", "daemon-reload"])
    desktop = _applications(home) / "spook.desktop"
    if desktop.exists():
        desktop.unlink()
        run(["update-desktop-database", str(_applications(home))])
    _unlink_repo_skills(_user_skills(home), repo)
    _unlink_repo_skills(home / ".claude" / "skills", repo)
    run(["uv", "tool", "uninstall", TOOL])
    print("Spook удалён; настройки, модели и журналы остались на месте.")


def find_repo(start: Path) -> Path | None:
    for folder in (start, *start.parents):
        project = folder / "pyproject.toml"
        if project.is_file() and 'name = "spook"' in project.read_text(encoding="utf-8"):
            return folder
    return None


def _unlink_repo_skills(folder: Path, repo: Path) -> None:
    for link in folder.glob("*"):
        if link.is_symlink() and link.resolve().parent == (repo / "skills").resolve():
            link.unlink()
            print(f"Скилл {link.name} отключён")


def _link_skills(repo: Path, home: Path) -> None:
    source = repo / "skills"
    skills = sorted(path for path in source.glob("*") if (path / "SKILL.md").is_file()) if source.is_dir() else []
    if not skills:
        return
    target_dir = _user_skills(home)
    old_skill = target_dir / "spook-lecture"
    if old_skill.is_symlink() and old_skill.resolve() == (source / old_skill.name).resolve():
        old_skill.unlink()
    target_dir.mkdir(parents=True, exist_ok=True)
    for skill in skills:
        link = target_dir / skill.name
        if link.is_symlink() and link.resolve() == skill.resolve():
            continue
        if link.is_symlink() and not link.exists():
            link.unlink()
        if link.exists() or link.is_symlink():
            print(f"Скилл {skill.name} не подключён: {link} уже занят чужим Скиллом")
            continue
        link.symlink_to(skill.resolve(), target_is_directory=True)
        print(f"Скилл {skill.name} подключён: {link} → {skill.resolve()}")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _spook(home: Path) -> Path:
    return home / ".local" / "bin" / TOOL


def _unit(home: Path) -> Path:
    return home / ".config" / "systemd" / "user" / SERVICE


def _applications(home: Path) -> Path:
    return home / ".local" / "share" / "applications"


def _user_skills(home: Path) -> Path:
    return home / ".agents" / "skills"
