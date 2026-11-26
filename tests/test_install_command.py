from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from test_install import make_repo, read_ini

RECORDER = """\
#!{python}
import json, sys
with open({calls!r}, "a", encoding="utf-8") as calls:
    calls.write(json.dumps(sys.argv, ensure_ascii=False) + "\\n")
"""


@dataclass
class Installation:
    repo: Path
    home: Path
    programs: Path
    calls: Path
    result: subprocess.CompletedProcess[str]

    def commands(self) -> list[list[str]]:
        return [json.loads(line) for line in self.calls.read_text(encoding="utf-8").splitlines()]


def put_recorder(path: Path, calls: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(RECORDER.format(python=sys.executable, calls=str(calls)), encoding="utf-8")
    path.chmod(0o755)


def install_command(tmp_path: Path, settings: str) -> Installation:
    repo = make_repo(tmp_path, "spook-probe")
    home = tmp_path / "home"
    programs = tmp_path / "bin"
    calls = tmp_path / "calls.jsonl"
    for name in ("uv", "update-desktop-database", "systemctl"):
        put_recorder(programs / name, calls)
    put_recorder(home / ".local/bin/spook", calls)
    config_home = tmp_path / "config"
    (config_home / "spook").mkdir(parents=True)
    (config_home / "spook/settings.toml").write_text(settings, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "spook.cli", "install", "--repo", str(repo)],
        env={"HOME": str(home), "XDG_CONFIG_HOME": str(config_home), "PATH": str(programs)},
        capture_output=True,
        encoding="utf-8",
        timeout=60,
    )
    return Installation(repo, home, programs, calls, result)


def test_install_command_writes_menu_entry_with_port_from_settings_file(tmp_path):
    installation = install_command(tmp_path, "port = 9123\n")

    assert installation.result.returncode == 0, installation.result.stderr
    entry = read_ini(installation.home / ".local/share/applications/spook.desktop")["Desktop Entry"]
    assert entry["Name"] == "Spook"
    assert entry["Type"] == "Application"
    assert entry["Exec"] == "xdg-open http://localhost:9123/"


def test_install_command_calls_installation_steps_in_order(tmp_path):
    installation = install_command(tmp_path, "port = 9123\n")

    assert installation.result.returncode == 0, installation.result.stderr
    programs, home = installation.programs, installation.home
    assert installation.commands() == [
        [str(programs / "uv"), "tool", "install", "--reinstall", str(installation.repo.resolve())],
        [str(home / ".local/bin/spook"), "prepare"],
        [str(programs / "update-desktop-database"), str(home / ".local/share/applications")],
        [str(programs / "systemctl"), "--user", "daemon-reload"],
        [str(programs / "systemctl"), "--user", "try-restart", "spook.service"],
        [str(programs / "systemctl"), "--user", "enable", "--now", "spook.service"],
    ]
    assert (home / ".agents/skills/spook-probe").resolve() == (installation.repo / "skills/spook-probe").resolve()
