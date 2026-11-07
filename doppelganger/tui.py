"""Small dependency-free terminal pickers: single select, multi select, text.

In a real terminal they're arrow-key menus (↑/↓ or j/k to move, Enter to
choose, Space to tick, q or Esc to cancel; number keys jump to an option).
When stdin isn't a terminal (scripts, CI, piped input), or
``DOPPELGANGER_PLAIN=1`` is set, they fall back to numbered prompts read with
``input()``, so everything still works non-interactively.

Each picker returns ``None`` when cancelled.
"""

import os
import sys
from typing import List, Optional, Sequence, Set, Union

_UP, _DOWN, _ENTER, _SPACE, _CANCEL = "up", "down", "enter", "space", "cancel"

BOLD, DIM, AMBER, RESET = "\x1b[1m", "\x1b[2m", "\x1b[38;2;242;176;76m", "\x1b[0m"


def interactive() -> bool:
    """Whether arrow-key menus can be used."""
    if os.environ.get("DOPPELGANGER_PLAIN"):
        return False
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


# ── Key reading ───────────────────────────────────────────────────────────────
def _read_key() -> str:
    """Read one keypress and name it (an arrow, enter, …) or return the char."""
    if os.name == "nt":
        import msvcrt
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            return {"H": _UP, "P": _DOWN}.get(msvcrt.getwch(), "")
    else:
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = os.read(fd, 1).decode(errors="ignore")
            if ch == "\x1b":
                seq = os.read(fd, 2).decode(errors="ignore") if _ready(fd) else ""
                return {"[A": _UP, "[B": _DOWN, "OA": _UP, "OB": _DOWN}.get(seq, _CANCEL if not seq else "")
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    if ch in ("\r", "\n"):
        return _ENTER
    if ch == " ":
        return _SPACE
    if ch in ("\x03", "q", "Q"):
        return _CANCEL
    if ch in ("k", "K"):
        return _UP
    if ch in ("j", "J"):
        return _DOWN
    return ch


def _ready(fd: int) -> bool:
    import select
    return bool(select.select([fd], [], [], 0.05)[0])


def _enable_ansi_on_windows() -> None:
    if os.name == "nt":
        os.system("")  # switches the console into VT (ANSI escape) mode


# ── Rendering helpers ─────────────────────────────────────────────────────────
class _Screen:
    """Redraws a block of lines in place."""

    def __init__(self) -> None:
        self.lines = 0

    def draw(self, rows: List[str]) -> None:
        out = sys.stdout
        if self.lines:
            out.write(f"\x1b[{self.lines}A")
        for row in rows:
            out.write("\x1b[2K" + row + "\n")
        out.flush()
        self.lines = len(rows)


def _hide_cursor() -> None:
    sys.stdout.write("\x1b[?25l")
    sys.stdout.flush()


def _show_cursor() -> None:
    sys.stdout.write("\x1b[?25h")
    sys.stdout.flush()


def _row(label: str, hint: str, active: bool, mark: Optional[bool] = None) -> str:
    pointer = f"{AMBER}❯{RESET}" if active else " "
    box = "" if mark is None else ("◉ " if mark else "◯ ")
    text = f"{BOLD}{label}{RESET}" if active else label
    tail = f"  {DIM}{hint}{RESET}" if hint else ""
    return f" {pointer} {box}{text}{tail}"


# ── Pickers ───────────────────────────────────────────────────────────────────
def select(
    title: str,
    options: Sequence[str],
    default: int = 0,
    hints: Optional[Sequence[str]] = None,
) -> Optional[int]:
    """Pick one option; returns its index, or None if cancelled."""
    if not options:
        return None
    hints = list(hints or [""] * len(options))
    if not interactive():
        return _plain_select(title, options, default, hints)

    _enable_ansi_on_windows()
    print(f"\n{BOLD}{title}{RESET}  {DIM}↑/↓ move · enter select · q cancel{RESET}")
    screen, pos = _Screen(), max(0, min(default, len(options) - 1))
    _hide_cursor()
    try:
        while True:
            screen.draw([_row(o, h, i == pos) for i, (o, h) in enumerate(zip(options, hints))])
            key = _read_key()
            if key == _UP:
                pos = (pos - 1) % len(options)
            elif key == _DOWN:
                pos = (pos + 1) % len(options)
            elif key.isdigit() and 1 <= int(key) <= len(options):
                pos = int(key) - 1
            elif key == _ENTER:
                return pos
            elif key == _CANCEL:
                return None
    finally:
        _show_cursor()


ACTION = "action"  # returned by multiselect when its action row is chosen


def multiselect(
    title: str,
    options: Sequence[str],
    checked: Optional[Set[int]] = None,
    hints: Optional[Sequence[str]] = None,
    select_all: bool = False,
    action: Optional[str] = None,
) -> Union[List[int], str, None]:
    """Tick any number of options; returns the ticked indexes (in order), or
    None if cancelled.

    ``select_all`` adds a "Select all" row at the top that ticks or unticks
    every option. ``action`` adds a plain row at the bottom (e.g. "Add another
    export…"); choosing it returns :data:`ACTION` instead of a list.
    """
    if not options and not action:
        return []
    hints = list(hints or [""] * len(options))
    ticked = set(checked or ())
    if not interactive():
        return _plain_multiselect(title, options, ticked, hints, select_all, action)

    # Rows: [select-all] + options + [action]
    top = 1 if select_all else 0
    count = top + len(options) + (1 if action else 0)
    _enable_ansi_on_windows()
    print(f"\n{BOLD}{title}{RESET}  {DIM}↑/↓ move · space tick · a all · enter confirm · q cancel{RESET}")
    screen, pos = _Screen(), 0
    _hide_cursor()
    try:
        while True:
            rows = []
            if select_all:
                rows.append(_row("Select all", "", pos == 0, len(ticked) == len(options)))
            rows += [_row(o, h, top + i == pos, i in ticked)
                     for i, (o, h) in enumerate(zip(options, hints))]
            if action:
                rows.append(_row(f"+ {action}", "", pos == count - 1))
            screen.draw(rows)
            key = _read_key()
            on_all = select_all and pos == 0
            on_action = bool(action) and pos == count - 1
            if key == _UP:
                pos = (pos - 1) % count
            elif key == _DOWN:
                pos = (pos + 1) % count
            elif key in ("a", "A") or (key == _SPACE and on_all):
                ticked = set() if len(ticked) == len(options) else set(range(len(options)))
            elif key == _SPACE and not on_action:
                ticked ^= {pos - top}
            elif key == _ENTER:
                return ACTION if on_action else sorted(ticked)
            elif key == _CANCEL:
                return None
    finally:
        _show_cursor()


def text(prompt: str, default: str = "") -> Optional[str]:
    """Ask for a line of text; returns the default for an empty answer, or None
    if cancelled (Ctrl-C / end of input)."""
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    return answer or default


# ── Plain (numbered) fallbacks ────────────────────────────────────────────────
def _plain_select(title, options, default, hints) -> Optional[int]:
    print(title)
    for i, (o, h) in enumerate(zip(options, hints), 1):
        print(f"  {i}. {o}" + (f"  ({h})" if h else ""))
    while True:
        answer = text("Choose a number", str(default + 1))
        if answer is None or answer.lower() in ("q", "quit"):
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer) - 1
        print("Please enter one of the numbers above.")


def _plain_multiselect(title, options, ticked, hints, select_all=False, action=None):
    print(title)
    for i, (o, h) in enumerate(zip(options, hints), 1):
        print(f"  [{'x' if i - 1 in ticked else ' '}] {i}. {o}" + (f"  ({h})" if h else ""))
    if action:
        print(f"      +. {action}  (type 'add')")
    default = ",".join(str(i + 1) for i in sorted(ticked)) or "none"
    extras = "'all', 'none'" + (", 'add'" if action else "") if select_all else "'none'" + (", 'add'" if action else "")
    while True:
        answer = text(f"Numbers to use, comma-separated ({extras})", default)
        if answer is None or answer.lower() in ("q", "quit"):
            return None
        low = answer.lower()
        if low == "none":
            return []
        if low == "all":
            return list(range(len(options)))
        if action and low == "add":
            return ACTION
        parts = [p.strip() for p in answer.split(",") if p.strip()]
        if parts and all(p.isdigit() and 1 <= int(p) <= len(options) for p in parts):
            return sorted({int(p) - 1 for p in parts})
        print("Please enter numbers from the list, separated by commas.")
