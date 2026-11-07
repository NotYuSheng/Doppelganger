#!/usr/bin/env python3
"""Record the README demo: the real ``python -m doppelganger`` guided setup.

Runs the app in a pseudo-terminal inside a throwaway folder that holds two
synthetic exports (demo/sample_export.json as a Telegram export and
demo/sample_whatsapp.txt as a WhatsApp chat), sends the keystrokes a user
would, and writes what the terminal showed to demo/demo_cli.cast (asciinema
v2). ``build_final.py`` renders that to the GIF with agg.

Nothing here is staged: every frame is the app's own output. POSIX only (it
uses the pty module).

    python demo/record_demo.py
"""

import json
import os
import pty
import select
import shutil
import struct
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAST = os.path.join(ROOT, "demo", "demo_cli.cast")
# Tall enough for the banner + menu on the first screen; later output scrolls.
COLS, ROWS = 100, 33

UP, DOWN, ENTER, SPACE = "\x1b[A", "\x1b[B", "\r", " "

# (text to wait for, keys to send one by one, pause before each key in seconds)
SCRIPT = [
    ("What would you like to do?", [DOWN] * 6, 0.3),    # walk the menu down to "auto"
    (None, [ENTER], 0.6),
    ("Which should be used?", [SPACE, SPACE], 0.9),      # "Select all": untick all, tick all
    (None, [ENTER], 0.9),
    ("Which of these is you", [DOWN], 1.4),              # pick yourself in the WhatsApp chat
    (None, [ENTER], 0.6),
    ("Group chats", [ENTER], 1.3),                       # keep
    ("Sensitive-data scan", [ENTER], 1.5),               # Auto
    ("What would you like to do?", [], 3.5),             # pipeline ran; back at the menu
    (None, ["q"], 0.0),
]


def _workspace() -> str:
    d = tempfile.mkdtemp(prefix="doppelganger-demo-")
    os.makedirs(os.path.join(d, "data"))
    shutil.copy(os.path.join(ROOT, "demo", "sample_export.json"), os.path.join(d, "data", "result.json"))
    shutil.copy(os.path.join(ROOT, "demo", "sample_whatsapp.txt"),
                os.path.join(d, "data", "WhatsApp Chat with Sam.txt"))
    shutil.copytree(os.path.join(ROOT, "configs"), os.path.join(d, "configs"))
    return d


def record() -> None:
    work = _workspace()
    env = dict(os.environ, PYTHONPATH=ROOT, TERM="xterm-256color", LLM_VALIDATE="false",
               COLUMNS=str(COLS), LINES=str(ROWS))
    env.pop("DOPPELGANGER_PLAIN", None)
    env.pop("DOPPELGANGER_NO_BANNER", None)

    pid, fd = pty.fork()
    if pid == 0:  # child: the app
        os.chdir(work)
        os.execvpe(sys.executable, [sys.executable, "-m", "doppelganger"], env)

    import fcntl
    import termios
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))

    start = time.time()
    events, screen = [], ""

    def pump(seconds: float) -> None:
        nonlocal screen
        end = time.time() + seconds
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.02)
            if r:
                try:
                    data = os.read(fd, 65536).decode(errors="replace")
                except OSError:
                    return
                if data:
                    events.append([round(time.time() - start, 3), "o", data])
                    screen += data

    def wait_for(text: str, timeout: float = 30) -> None:
        nonlocal screen
        end = time.time() + timeout
        while text not in screen and time.time() < end:
            pump(0.05)
        if text not in screen:
            raise RuntimeError(f"timed out waiting for {text!r}")
        screen = screen[screen.index(text) + len(text):]

    try:
        for wait, keys, pause in SCRIPT:
            if wait:
                wait_for(wait)
            pump(0.3 if keys else pause)
            for key in keys:
                pump(pause)
                os.write(fd, key.encode())
        pump(1.0)
    finally:
        try:
            os.kill(pid, 9)
        except OSError:
            pass
        shutil.rmtree(work, ignore_errors=True)

    with open(CAST, "w", encoding="utf-8") as f:
        f.write(json.dumps({"version": 2, "width": COLS, "height": ROWS}) + "\n")
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    print(f"wrote {CAST} ({len(events)} events, {events[-1][0]:.1f}s)")


if __name__ == "__main__":
    record()
