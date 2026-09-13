"""WhatsApp chat-export (``.txt``) adapter.

Parses the plain-text file produced by WhatsApp's per-chat
**Export chat** feature (``⋮ > More > Export chat > Without media``) into
normalized messages. All WhatsApp-specific parsing lives here; the downstream
pipeline never sees a raw WhatsApp line.

Unlike Telegram's JSON export, WhatsApp gives us:

- **Plain text**, one message per (possibly multi-line) entry, prefixed with a
  timestamp and sender. The exact prefix format varies by phone locale — date
  order (D/M vs M/D), separators, 12- vs 24-hour clock, and whether the whole
  prefix is wrapped in ``[...]``. We handle the common shapes with a small set
  of tolerant regexes and fall back to trying each.
- **No message ids and no reply metadata**, so ``message_id`` / ``reply_to_id``
  stay ``None`` and the shared pipeline groups by time alone (exactly the
  documented fallback in :mod:`ingest.message`).
- **No marker of who "you" are.** WhatsApp labels every message with a contact
  name, never "me", so ``--self-name`` is effectively required. If it isn't
  given we try to guess (the sole 1:1 counterpart's *other* name), but we raise
  rather than silently mislabel when the guess is ambiguous.
"""

import os
import re
from datetime import datetime
from typing import List, Optional

from ingest.adapters.base import register
from ingest.message import NormalizedMessage

# WhatsApp separates the "<timestamp> - <sender>: <text>" prefix from the body
# with either " - " (Android) or, when the prefix is bracketed, "] " (iOS).
# We match the timestamp + sender greedily-but-safely and keep the rest as text.
#
# Two prefix shapes cover the vast majority of real exports:
#   iOS:      [12/03/2024, 14:32:11] Alice: hello
#   Android:  12/03/2024, 14:32 - Alice: hello
# The date/time chunk itself is left loose (digits, separators, am/pm) and
# parsed separately so we don't hard-code one locale's field order.
_TS = r"(?P<ts>\d{1,4}[.\-/]\d{1,2}[.\-/]\d{1,4},?\s+\d{1,2}[:.]\d{2}(?:[:.]\d{2})?\s*(?:[APap]\.?[Mm]\.?)?)"

_LINE_PATTERNS = [
    # iOS: whole prefix bracketed, closing "]" then "Sender: text".
    re.compile(r"^\[\s*" + _TS + r"\s*\]\s+(?P<sender>[^:]{1,100}?):\s(?P<text>.*)$"),
    # Android: "timestamp - Sender: text".
    re.compile(r"^" + _TS + r"\s+-\s+(?P<sender>[^:]{1,100}?):\s(?P<text>.*)$"),
]

# System/notification lines have a timestamp prefix but no "Sender:" — e.g.
# "Messages and calls are end-to-end encrypted." or "Alice created group ...".
# They match neither pattern above (no "Sender: " with a space after the colon),
# so they're naturally skipped as continuation lines with no open message; we
# drop those unless they continue a real message. This regex just detects that a
# line *starts* with a timestamp prefix at all, to tell continuations apart from
# system notices.
_HAS_PREFIX = re.compile(r"^\[?\s*" + _TS)

# Candidate strptime formats, tried in order. WhatsApp emits a limited set; we
# normalize separators to "/" and ", " first so one format string covers each.
_DT_FORMATS = [
    "%d/%m/%Y, %H:%M:%S",
    "%d/%m/%Y, %H:%M",
    "%m/%d/%Y, %H:%M:%S",
    "%m/%d/%Y, %H:%M",
    "%d/%m/%y, %H:%M:%S",
    "%d/%m/%y, %H:%M",
    "%m/%d/%y, %H:%M:%S",
    "%m/%d/%y, %H:%M",
    "%d/%m/%Y, %I:%M:%S %p",
    "%d/%m/%Y, %I:%M %p",
    "%m/%d/%Y, %I:%M:%S %p",
    "%m/%d/%Y, %I:%M %p",
    "%d/%m/%y, %I:%M:%S %p",
    "%d/%m/%y, %I:%M %p",
    "%m/%d/%y, %I:%M:%S %p",
    "%m/%d/%y, %I:%M %p",
]

# Placeholder bodies WhatsApp writes for stripped media/omitted content. These
# carry no text worth training on; we drop them so the message becomes empty and
# is filtered out.
_MEDIA_PLACEHOLDERS = {
    "<media omitted>",
    "<médias omis>",
    "image omitted",
    "video omitted",
    "audio omitted",
    "sticker omitted",
    "gif omitted",
    "document omitted",
    "contact card omitted",
    "this message was deleted",
    "you deleted this message",
    "null",
}

# Non-breaking / narrow-no-break spaces show up inside iOS timestamps and around
# the AM/PM marker; normalize them to plain spaces before parsing.
_ODD_SPACES = re.compile(r"[   ]")


def _parse_timestamp(raw: str) -> Optional[int]:
    """Best-effort parse of a WhatsApp timestamp chunk into unix seconds.

    Normalizes the many locale spellings (``.``/``-``/``/`` date separators,
    ``HH.MM`` time separators, ``a.m.``/``AM`` markers, odd spaces) into the
    canonical ``D/M/Y, H:M[:S] [AM/PM]`` shape the format list expects.

    Returns ``None`` if no known format matches, so the caller can treat the
    line as a continuation rather than crash on one unusual export.
    """
    s = _ODD_SPACES.sub(" ", raw).strip()
    s = re.sub(r"\s+", " ", s)
    # Split into date and time on the first comma-or-space before the clock, so
    # we can normalize each half's separators independently.
    m = re.match(r"^(?P<date>\d{1,4}[.\-/]\d{1,2}[.\-/]\d{1,4}),?\s+(?P<rest>.+)$", s)
    if not m:
        return None
    date = m.group("date").replace(".", "/").replace("-", "/")
    rest = m.group("rest")
    # Time separator HH.MM -> HH:MM (leave the AM/PM marker alone).
    rest = re.sub(r"(\d{1,2})\.(\d{2})", r"\1:\2", rest)
    # Normalize "a.m."/"p. m."/"am" -> "AM"/"PM" (consuming any trailing dot).
    rest = re.sub(
        r"([APap])\.?\s?[Mm]\.?",
        lambda mm: mm.group(1).upper() + "M",
        rest,
    )
    s = re.sub(r"\s+", " ", f"{date}, {rest}").strip()
    for fmt in _DT_FORMATS:
        try:
            return int(datetime.strptime(s, fmt).timestamp())
        except ValueError:
            continue
    return None


def _clean_text(text: str) -> str:
    """Strip a message body, dropping media/omitted placeholders to empty."""
    t = _ODD_SPACES.sub(" ", text).strip()
    if t.lower().strip("‎‎") in _MEDIA_PLACEHOLDERS:
        return ""
    return t


def _match_line(line: str):
    """Return (ts_raw, sender, text) if ``line`` starts a new message, else None."""
    for pat in _LINE_PATTERNS:
        m = pat.match(line)
        if m:
            return m.group("ts"), m.group("sender").strip(), m.group("text")
    return None


class WhatsAppAdapter:
    name = "whatsapp"

    def parse(
        self, path: str, *, self_name: Optional[str] = None
    ) -> List[NormalizedMessage]:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()

        # First pass: reconstruct logical messages, joining continuation lines
        # (a message's own newlines land on lines with no timestamp prefix).
        raw_entries: "list[tuple[int, str, str]]" = []  # (timestamp, sender, text)
        cur: "Optional[list]" = None  # [ts_int, sender, [text_lines]]
        for line in lines:
            hit = _match_line(line)
            if hit:
                ts_raw, sender, text = hit
                ts = _parse_timestamp(ts_raw)
                if ts is None:
                    # Unparseable prefix: treat as continuation of current msg
                    # rather than losing the line.
                    if cur is not None:
                        cur[2].append(line)
                    continue
                if cur is not None:
                    raw_entries.append((cur[0], cur[1], "\n".join(cur[2])))
                cur = [ts, sender, [text]]
            elif cur is not None and not _HAS_PREFIX.match(line):
                # A wrapped line of the current message (or a blank line within
                # it). System notices carry a prefix but no sender, so they DON'T
                # start a message and DON'T continue one either — but they also
                # don't match _match_line, so they'd land here. Guard: only keep
                # continuations that lack a timestamp prefix entirely.
                cur[2].append(line)
            # else: prefixed line that isn't a sender message (system notice) ->
            # skip it, leaving the current message intact.
        if cur is not None:
            raw_entries.append((cur[0], cur[1], "\n".join(cur[2])))

        senders = sorted({s for _, s, _ in raw_entries})
        # WhatsApp never marks the owner (every message just carries a contact
        # name), so we can't guess "you" — require --self-name rather than
        # silently mislabel every turn.
        if not self_name:
            found = ", ".join(repr(s) for s in senders) or "(none)"
            raise ValueError(
                "WhatsApp exports don't mark which sender is you. Pass "
                "--self-name with your display name exactly as it appears in the "
                f"chat. Senders found: {found}."
            )
        if self_name not in senders:
            print(
                f"[whatsapp] Warning: --self-name {self_name!r} matches no sender "
                f"in this export (found: {', '.join(repr(s) for s in senders)}). "
                "Every message will be treated as 'not you' -> all conversations "
                "will be dropped. Check the spelling."
            )
        print(f"[whatsapp] Using self name: {self_name!r}")

        # A WhatsApp export is a single conversation; use the file name as a
        # stable chat id so multiple exports don't collide.
        chat_id = os.path.splitext(os.path.basename(path))[0] or "whatsapp"

        messages: List[NormalizedMessage] = []
        for ts, sender, text in raw_entries:
            cleaned = _clean_text(text)
            if not cleaned:
                continue
            messages.append(
                NormalizedMessage(
                    chat_id=chat_id,
                    timestamp=ts,
                    sender_id=sender,
                    sender_is_self=(sender == self_name),
                    text=cleaned,
                    message_id=None,
                    reply_to_id=None,
                )
            )
        return messages


register(WhatsAppAdapter())
