#!/usr/bin/env python3
"""Build the CLI banner (ingest/banner.py) and the README demo GIF.

- ingest/banner.py: lean parrot + amber wordmark, printed at CLI startup.
- demo/demo.gif: a real recording of ``python -m doppelganger`` (menu and
  guided setup, then the pipeline) made by demo/record_demo.py and rendered
  with agg.
"""
import os
import subprocess
import sys

import pyfiglet

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGG = os.environ.get("AGG", "agg")
GAP = "   "

AMBER = (242, 176, 76)

RESET = "\x1b[0m"

with open(os.path.join(ROOT, "demo/mascot.txt"), encoding="utf-8") as _f:
    parrot = _f.read().rstrip("\n").split("\n")
PW = max(len(l) for l in parrot)


def _fig(t):
    ls = [l.rstrip() for l in pyfiglet.figlet_format(t, font="ansi_shadow", width=200).rstrip("\n").split("\n")]
    while ls and not ls[-1].strip(): ls.pop()
    while ls and not ls[0].strip(): ls.pop(0)
    return ls


word = _fig("Doppel") + _fig("ganger")
TOP = (len(parrot) - len(word)) // 2
WW = max(len(l) for l in word)
BLOCK_W = PW + len(GAP) + WW


def lean_rows(on, off):
    """Parrot + wordmark only (for the CLI banner)."""
    r = []
    for i, pl in enumerate(parrot):
        wl = word[i - TOP] if 0 <= i - TOP < len(word) else ""
        wl = f"{on}{wl}{off}" if wl else ""
        r.append((pl.ljust(PW) + GAP + wl).rstrip())
    return r


def write_banner_module():
    body = "\n".join(lean_rows("<C>", "<R>"))
    mod = (
        '"""ASCII startup banner: a parrot in a mirror (it mimics your voice; the\n'
        'mirror is the doppelganger) beside the wordmark, in a gold-to-coral gradient.\n'
        'Regenerate via demo/build_final.py. DOPPELGANGER_NO_BANNER=1 silences it."""\n\n'
        'import os\n\n'
        '_GRAD_TOP, _GRAD_BOTTOM = (255, 196, 84), (233, 84, 64)  # gold -> coral, top to bottom\n'
        '_RESET = "\\x1b[0m"\n\n'
        '_BANNER = r"""\n' + body + '\n"""\n\n\n'
        'def _colour(i: int, n: int) -> str:\n'
        '    """Truecolor escape for wordmark line ``i`` of ``n`` along the gradient."""\n'
        '    t = i / max(1, n - 1)\n'
        '    r, g, b = (round(a + (z - a) * t) for a, z in zip(_GRAD_TOP, _GRAD_BOTTOM))\n'
        '    return f"\\x1b[1;38;2;{r};{g};{b}m"\n\n\n'
        'def print_banner() -> None:\n'
        '    if os.environ.get("DOPPELGANGER_NO_BANNER"):\n'
        '        return\n'
        '    from ingest.console import use_utf8_output\n'
        '    use_utf8_output()  # the braille art can\'t be written in Windows\' legacy code page\n'
        '    lines = _BANNER.split("\\n")\n'
        '    marked = [i for i, line in enumerate(lines) if "<C>" in line]\n'
        '    for k, i in enumerate(marked):\n'
        '        lines[i] = lines[i].replace("<C>", _colour(k, len(marked))).replace("<R>", _RESET)\n'
        '    print("\\n".join(lines) + "\\n")\n'
    )
    with open(os.path.join(ROOT, "ingest/banner.py"), "w", encoding="utf-8") as f:
        f.write(mod)


def render_gif():
    """Record the real CLI session, then render it to demo/demo.gif."""
    import record_demo
    record_demo.record()
    subprocess.run(
        [AGG, "--font-size", "16", "--theme", "dracula", "--idle-time-limit", "2.5",
         "--last-frame-duration", "4", record_demo.CAST, os.path.join(ROOT, "demo/demo.gif")],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, check=True,
    )


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    write_banner_module()
    render_gif()
    print("wrote ingest/banner.py and demo/demo.gif")
