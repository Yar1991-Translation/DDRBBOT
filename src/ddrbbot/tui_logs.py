from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    from rich.text import Text
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Footer, Header, Input, RichLog, Select, Static
except ImportError as exc:  # pragma: no cover - optional dependency
    raise SystemExit(
        "Textual is not installed. Run `pip install -e '.[tui]'` first."
    ) from exc


LEVEL_STYLES: dict[str, str] = {
    "DEBUG": "dim",
    "INFO": "cyan",
    "WARNING": "yellow",
    "ERROR": "bold red",
    "CRITICAL": "bold white on red",
}

LEVEL_ORDER = ("ALL", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

_TEXT_LINE_RE = re.compile(
    r"^(?P<ts>\S+\s+\S+)\s+(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+"
    r"\[(?P<name>[^\]]+)\]\s+(?P<msg>.*)$"
)


@dataclass
class LogLine:
    raw: str
    level: str
    name: str
    timestamp: str
    message: str

    @classmethod
    def parse(cls, raw: str) -> "LogLine":
        stripped = raw.rstrip("\r\n")
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                data = json.loads(stripped)
                return cls(
                    raw=stripped,
                    level=str(data.get("level") or "INFO").upper(),
                    name=str(data.get("logger") or ""),
                    timestamp=str(data.get("ts") or ""),
                    message=str(data.get("message") or ""),
                )
            except json.JSONDecodeError:
                pass
        match = _TEXT_LINE_RE.match(stripped)
        if match:
            return cls(
                raw=stripped,
                level=match.group("level").upper(),
                name=match.group("name"),
                timestamp=match.group("ts"),
                message=match.group("msg"),
            )
        return cls(raw=stripped, level="", name="", timestamp="", message=stripped)


class FileTailer:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh = None
        self._inode: int | None = None
        self._size: int = 0
        self._buffer: str = ""

    def _open(self) -> None:
        if not self.path.exists():
            return
        self._fh = self.path.open("r", encoding="utf-8", errors="replace")
        self._fh.seek(0, os.SEEK_END)
        try:
            stat = self.path.stat()
            self._inode = getattr(stat, "st_ino", None)
            self._size = stat.st_size
        except OSError:
            self._inode = None
            self._size = 0

    def _reopen_if_rotated(self) -> None:
        if not self.path.exists():
            return
        try:
            stat = self.path.stat()
        except OSError:
            return
        inode = getattr(stat, "st_ino", None)
        rotated = (
            self._fh is None
            or (self._inode is not None and inode is not None and inode != self._inode)
            or stat.st_size < self._size
        )
        if rotated:
            if self._fh is not None:
                try:
                    self._fh.close()
                except Exception:
                    pass
            self._fh = self.path.open("r", encoding="utf-8", errors="replace")
            self._inode = inode
            self._size = 0
            self._buffer = ""

    def read_new_lines(self) -> list[str]:
        if self._fh is None:
            self._open()
        self._reopen_if_rotated()
        if self._fh is None:
            return []
        chunk = self._fh.read()
        if not chunk:
            return []
        try:
            self._size = self.path.stat().st_size
        except OSError:
            pass
        self._buffer += chunk
        if "\n" not in self._buffer:
            return []
        parts = self._buffer.split("\n")
        self._buffer = parts.pop()
        return [p for p in parts if p]


class LogTUI(App):
    CSS = """
    Screen { layout: vertical; }
    #filters { height: 3; padding: 0 1; }
    #filters Select { width: 20; }
    #filters Input { width: 1fr; }
    RichLog { border: solid $accent; }
    #status { height: 1; padding: 0 1; color: $text-muted; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("p", "toggle_pause", "Pause/Resume"),
        Binding("g", "scroll_end", "Bottom"),
        Binding("c", "clear", "Clear"),
    ]

    def __init__(self, log_path: Path, poll_interval: float = 0.5) -> None:
        super().__init__()
        self.log_path = log_path
        self.poll_interval = poll_interval
        self.tailer = FileTailer(log_path)
        self.level_filter = "ALL"
        self.name_filter = ""
        self.paused = False
        self.lines_seen = 0
        self.lines_shown = 0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="filters"):
            yield Select(
                options=[(name, name) for name in LEVEL_ORDER],
                value="ALL",
                id="level",
                allow_blank=False,
            )
            yield Input(placeholder="logger/name substring filter...", id="name")
        yield RichLog(highlight=False, markup=False, wrap=False, id="log")
        yield Static(id="status")
        yield Footer()

    async def on_mount(self) -> None:
        self._update_status()
        self.set_interval(self.poll_interval, self._tick)

    async def _tick(self) -> None:
        if self.paused:
            return
        try:
            new_lines = self.tailer.read_new_lines()
        except Exception as exc:  # pragma: no cover - IO error
            self._write_error(f"tail error: {exc}")
            return
        if not new_lines:
            return
        self.lines_seen += len(new_lines)
        self._append(new_lines)

    def _append(self, lines: Iterable[str]) -> None:
        log = self.query_one("#log", RichLog)
        name_needle = self.name_filter.strip().lower()
        level = self.level_filter
        for raw in lines:
            parsed = LogLine.parse(raw)
            if level != "ALL" and parsed.level != level:
                if not (level == "ALL" and not parsed.level):
                    if parsed.level != level:
                        continue
            if name_needle and name_needle not in parsed.name.lower():
                continue
            log.write(self._render_line(parsed))
            self.lines_shown += 1
        self._update_status()

    @staticmethod
    def _render_line(parsed: LogLine) -> Text:
        style = LEVEL_STYLES.get(parsed.level, "")
        text = Text()
        if parsed.timestamp:
            text.append(parsed.timestamp + " ", style="dim")
        if parsed.level:
            text.append(f"{parsed.level:<8}", style=style or "")
        if parsed.name:
            text.append(f"[{parsed.name}] ", style="magenta")
        text.append(parsed.message)
        if not parsed.timestamp and not parsed.level:
            return Text(parsed.raw)
        return text

    def _write_error(self, message: str) -> None:
        log = self.query_one("#log", RichLog)
        log.write(Text(message, style="bold red"))

    def _update_status(self) -> None:
        status = self.query_one("#status", Static)
        state = "PAUSED" if self.paused else "LIVE"
        status.update(
            f"{state} | file={self.log_path} | seen={self.lines_seen} shown={self.lines_shown} "
            f"| level={self.level_filter} | name~'{self.name_filter}'"
        )

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "level":
            self.level_filter = str(event.value)
            self._update_status()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "name":
            self.name_filter = event.value
            self._update_status()

    def action_toggle_pause(self) -> None:
        self.paused = not self.paused
        self._update_status()

    def action_scroll_end(self) -> None:
        self.query_one("#log", RichLog).scroll_end(animate=False)

    def action_clear(self) -> None:
        self.query_one("#log", RichLog).clear()
        self.lines_shown = 0
        self._update_status()


def _resolve_log_file(explicit: str | None) -> Path:
    candidate = explicit or os.getenv("LOG_FILE", "").strip()
    if not candidate:
        raise SystemExit(
            "No log file specified. Pass --file PATH or set LOG_FILE env."
        )
    return Path(candidate).expanduser()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="DDRBBOT log TUI viewer")
    parser.add_argument("--file", "-f", help="Log file path (defaults to $LOG_FILE).")
    parser.add_argument(
        "--interval",
        type=float,
        default=0.5,
        help="Polling interval in seconds (default 0.5).",
    )
    args = parser.parse_args(argv)
    path = _resolve_log_file(args.file)
    LogTUI(path, poll_interval=max(0.1, args.interval)).run()


if __name__ == "__main__":
    main()
