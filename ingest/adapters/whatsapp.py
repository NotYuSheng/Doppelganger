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
  name, never "me", so ``--self-name`` is required — we raise (listing the
  senders found) rather than silently mislabel every turn.

Date-field order (D/M vs M/D) is genuinely ambiguous from a single date, so the
adapter first *scans the whole file* for a date that disambiguates (a first
field > 12 means D/M; a second field > 12 means M/D) and applies that order to
every line. Only a file whose every date is <= 12/12 stays ambiguous, and there
we fall back to D/M (the WhatsApp default outside the US), warning once.
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
# Bare (uncaptured) timestamp sub-pattern, reused in several regexes below.
_TS_BARE = r"\d{1,4}[.\-/]\d{1,2}[.\-/]\d{1,4}(?:,\s*|\s+)\d{1,2}[:.]\d{2}(?:[:.]\d{2})?\s*(?:[APap]\.?[Mm]\.?)?"
_TS = r"(?P<ts>" + _TS_BARE + r")"

_LINE_PATTERNS = [
    # iOS: whole prefix bracketed, closing "]" then "Sender: text".
    re.compile(r"^\[\s*" + _TS + r"\s*\]\s+(?P<sender>[^:]{1,100}?):\s(?P<text>.*)$"),
    # Android: "timestamp - Sender: text".
    re.compile(r"^" + _TS + r"\s+-\s+(?P<sender>[^:]{1,100}?):\s(?P<text>.*)$"),
]

# System/notification lines have a full timestamp prefix + separator but no
# "Sender:" — e.g. "Messages and calls are end-to-end encrypted." or
# "Alice created group ...". They match neither _LINE_PATTERN (no "Sender: ")
# but we must still tell them apart from a wrapped continuation line whose text
# happens to start with a date (e.g. "13/04/2025 was the standup"). The
# distinguisher is the prefix *separator*: a real WhatsApp entry always closes
# the timestamp with "] " (iOS) or " - " (Android), which body text almost never
# reproduces. So a line is a system notice only when it carries the whole prefix
# *including that separator*; anything else is treated as continuation and kept.
_HAS_PREFIX = re.compile(
    r"^(?:\[\s*" + _TS_BARE + r"\s*\]\s|" + _TS_BARE + r"\s+-\s)"
)

# Candidate strptime time-part formats (the "H:M[:S] [AM/PM]" tail). The date
# part is prepended per detected field order below. We normalize separators to
# "/" and insert ", " first so one format string per shape covers each.
_TIME_FORMATS = [
    "%H:%M:%S",
    "%H:%M",
    "%I:%M:%S %p",
    "%I:%M %p",
]

# Full format lists for each resolved date-field order (day-first vs month-first,
# 4- and 2-digit years). Built once from _TIME_FORMATS.
def _build_formats(date_fmts):
    return [f"{d}, {t}" for d in date_fmts for t in _TIME_FORMATS]


_FORMATS_DAYFIRST = _build_formats(["%d/%m/%Y", "%d/%m/%y"])
_FORMATS_MONTHFIRST = _build_formats(["%m/%d/%Y", "%m/%d/%y"])

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
}

# Non-breaking / narrow-no-break spaces show up inside iOS timestamps and around
# the AM/PM marker; normalize them to plain spaces before parsing.
_ODD_SPACES = re.compile(r"[   ]")

# Invisible bidi/formatting marks WhatsApp injects around media placeholders
# and RTL text (LRM/RLM, the LRE..PDF range, and zero-width space). Stripped
# so placeholder matching and text see the plain content.
_INVISIBLE = re.compile("[\u200b\u200e\u200f\u202a-\u202e]")


def _normalize_ts(raw: str) -> Optional["tuple[str, str]"]:
    """Split a raw timestamp chunk into normalized ``(date, time)`` parts.

    Normalizes locale spellings (``.``/``-``/``/`` date separators, ``HH.MM``
    time separators, ``a.m.``/``AM`` markers, odd spaces). Returns ``None`` when
    the chunk doesn't look like a date+time at all.
    """
    s = re.sub(r"\s+", " ", _ODD_SPACES.sub(" ", raw).strip())
    # Split date from time on the comma/space between them (either or both; some
    # locales emit "DD/MM/YYYY,HH:MM" with no space).
    m = re.match(
        r"^(?P<date>\d{1,4}[.\-/]\d{1,2}[.\-/]\d{1,4})(?:,\s*|\s+)(?P<rest>.+)$", s
    )
    if not m:
        return None
    date = m.group("date").replace(".", "/").replace("-", "/")
    rest = m.group("rest")
    rest = re.sub(r"(\d{1,2})\.(\d{2})", r"\1:\2", rest)  # HH.MM -> HH:MM
    # Normalize "a.m."/"p. m."/"am" -> "AM"/"PM" (consuming any trailing dot).
    rest = re.sub(
        r"([APap])\.?\s?[Mm]\.?", lambda mm: mm.group(1).upper() + "M", rest
    )
    return date, re.sub(r"\s+", " ", rest).strip()


def _parse_timestamp(raw: str, dayfirst: bool = True) -> Optional[int]:
    """Parse a WhatsApp timestamp chunk into unix seconds.

    ``dayfirst`` selects the date-field order (D/M when True, else M/D) — the
    caller resolves it once per file via :func:`_detect_dayfirst`, since a single
    date can't disambiguate. Returns ``None`` if no known format matches, so the
    caller can treat the line as a continuation rather than crash.
    """
    parts = _normalize_ts(raw)
    if parts is None:
        return None
    s = f"{parts[0]}, {parts[1]}"
    formats = _FORMATS_DAYFIRST if dayfirst else _FORMATS_MONTHFIRST
    for fmt in formats:
        try:
            return int(datetime.strptime(s, fmt).timestamp())
        except ValueError:
            continue
    return None


def _detect_dayfirst(raw_timestamps: "list[str]") -> Optional[bool]:
    """Resolve date-field order for a whole file from its dates.

    A first field > 12 forces day-first (D/M); a second field > 12 forces
    month-first (M/D). Returns True/False when the file's dates settle it, or
    ``None`` when every date is ambiguous (<= 12/12) and the caller should apply
    a default.
    """
    for raw in raw_timestamps:
        parts = _normalize_ts(raw)
        if parts is None:
            continue
        fields = parts[0].split("/")
        if len(fields) != 3:
            continue
        first, second = int(fields[0]), int(fields[1])
        if first > 12:
            return True   # first field can only be a day
        if second > 12:
            return False  # second field can only be a day
    return None


def _clean_text(text: str) -> str:
    """Strip a message body, dropping media/omitted placeholders to empty."""
    t = _INVISIBLE.sub("", _ODD_SPACES.sub(" ", text)).strip()
    if t.lower() in _MEDIA_PLACEHOLDERS:
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

        # Resolve the file's date-field order (D/M vs M/D) up front from all its
        # dates — a single date can't disambiguate. Default to day-first (the
        # WhatsApp default outside the US) when every date is <= 12/12.
        message_ts = [hit[0] for hit in (_match_line(l) for l in lines) if hit]
        dayfirst = _detect_dayfirst(message_ts)
        if dayfirst is None:
            dayfirst = True
            if message_ts:
                print(
                    "[whatsapp] Date order is ambiguous (all dates <= 12/12); "
                    "assuming day/month/year. If your export is US-format "
                    "(month/day), timestamps may be wrong."
                )

        # Main pass: reconstruct logical messages, joining continuation lines
        # (a message's own newlines land on lines with no timestamp prefix).
        raw_entries: "list[tuple[int, str, str]]" = []  # (timestamp, sender, text)
        cur: "Optional[list]" = None  # [ts_int, sender, [text_lines]]
        for line in lines:
            hit = _match_line(line)
            if hit:
                ts_raw, sender, text = hit
                ts = _parse_timestamp(ts_raw, dayfirst=dayfirst)
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
