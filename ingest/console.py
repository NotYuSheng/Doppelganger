"""Make terminal output safe on Windows.

On Windows, when output is redirected (``python -m ingest > log.txt``, a pipe,
CI), Python writes in the legacy code page (e.g. cp1252), which can't encode the
banner's braille art or the menu's symbols, and printing them crashes. Writing
UTF-8 instead fixes that: real consoles already handle Unicode, and redirected
output becomes a UTF-8 file.

Other platforms are left alone: a non-UTF-8 locale there is a deliberate
choice, and re-encoding would garble output that currently prints correctly.
"""

import os
import sys


def use_utf8_output() -> None:
    """On Windows, switch stdout/stderr to UTF-8 if they're using something else.

    stdout replaces anything still unwritable with "?"; stderr keeps Python's
    default backslash escapes so error messages lose no detail.
    """
    if os.name != "nt":
        return
    for stream, errors in ((sys.stdout, "replace"), (sys.stderr, "backslashreplace")):
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "").replace("_", "")
        if encoding == "utf8" or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors=errors)
        except (ValueError, OSError):
            pass
