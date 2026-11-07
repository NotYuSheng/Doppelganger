"""Make terminal output safe on every platform.

On Windows, when output is redirected (``python -m ingest > log.txt``, a pipe,
CI), Python writes in the legacy code page (e.g. cp1252), which can't encode the
banner's braille art or the menu's symbols, and printing them crashes. Writing
UTF-8 instead fixes that: real consoles already handle Unicode, and redirected
output becomes a UTF-8 file. Anything that still can't be written is replaced
rather than raising.
"""

import sys


def use_utf8_output() -> None:
    """Switch stdout/stderr to UTF-8 if they're using something else."""
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "").replace("_", "")
        if encoding == "utf8" or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # e.g. a stream that's already been written to oddly
            pass
