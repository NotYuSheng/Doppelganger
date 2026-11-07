"""Shared parser for Meta's "Download Your Information" message exports.

Instagram and Facebook Messenger produce the same per-thread JSON in a
Download Your Information (DYI) export, requested with format **JSON**:

    <export root>/
    ├── your_instagram_activity/messages/inbox/alice_1234567890/message_1.json
    │   (older layouts: messages/inbox/..., Messenger: your_facebook_activity/...)
    └── personal_information/personal_information/personal_information.json

Each ``message_N.json`` (long threads are split into several) looks like::

    {
      "participants": [{"name": "Alice"}, {"name": "You"}],
      "messages": [
        {"sender_name": "Alice", "timestamp_ms": 1700000000000, "content": "hi"},
        ...
      ],
      "title": "Alice",
      "thread_path": "inbox/alice_1234567890"
    }

Quirks absorbed here, so the shared pipeline never sees them:

- **Mojibake.** Meta writes UTF-8 bytes as if each byte were a Latin-1
  character ("ðŸ‘" for "👍"), so every string is repaired with
  ``encode("latin-1").decode("utf-8")``. Strings that are already correct fail
  that round trip and are left unchanged.
- **Newest-first order.** Messages are listed newest first. We restore
  chronological order, keeping the original order for same-second messages.
- **No message ids or reply links**, so ``message_id`` / ``reply_to_id`` stay
  ``None`` and the pipeline groups by time.
- **Who "you" are isn't marked on each message.** It's resolved from
  ``--self-name``, then the export's ``personal_information.json``, then "the
  one participant present in every thread".
- **Non-text events** (reactions, likes, "sent an attachment", unsent messages,
  shared posts without a caption, calls) are dropped.

The format is not officially documented. The field names above come from
community parsers and should be re-checked against real exports when Meta
changes them.
"""

import json
import os
import re
from typing import Dict, Iterable, List, Optional, Set

from ingest.message import NormalizedMessage

_THREAD_FILE = re.compile(r"^message_\d+\.json$")

# Folder names whose threads aren't real conversations you took part in:
# unanswered requests from strangers and spam-filtered threads.
_SKIP_DIRS = {"message_requests", "filtered_threads"}

# Content Meta generates for non-text events. Matched against the whole message
# (case-insensitive) after mojibake repair. "{name}" lines appear both as
# "You sent an attachment." and "Alice sent an attachment.".
_SYSTEM_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^liked a message$",
        r"^reacted .{1,8} to (your|a) message$",
        r"^(.+ )?sent an attachment\.?$",
        r"^(.+ )?sent a (photo|video|voice message|gif|sticker|link)\.?$",
        r"^(.+ )?shared a (story|post|reel|link)\.?$",
        r"^(.+ )?unsent a message\.?$",
        r"^(.+ )?(started|ended|missed) (a|an|the|your)? ?(audio |video )?call\.?$",
        r"^(.+ )?(created|named) the group.*$",
        r"^(.+ )?(added|removed) .+ (to|from) the group\.?$",
        r"^(.+ )?left the group\.?$",
        r"^(.+ )?changed the group (name|photo).*$",
        r"^mentioned you in (their|a) story\.?$",
    )
]


def fix_mojibake(value: str) -> str:
    """Undo Meta's UTF-8-read-as-Latin-1 encoding of a string.

    Returns the input unchanged when it isn't mojibake: pure ASCII, or a string
    that can't be Latin-1 encoded or isn't valid UTF-8 afterwards.
    """
    if value.isascii():
        return value
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def _is_system(text: str) -> bool:
    return any(p.match(text) for p in _SYSTEM_PATTERNS)


def find_thread_files(path: str) -> Dict[str, List[str]]:
    """Map each thread directory to its ``message_N.json`` files.

    ``path`` may be the unzipped export root, any folder inside it (such as
    ``.../messages/inbox``), one thread folder, or a single ``message_N.json``.
    When walking a folder, message requests and spam-filtered threads are
    skipped.
    """
    if os.path.isfile(path):
        return {os.path.dirname(os.path.abspath(path)): [path]}
    if not os.path.isdir(path):
        raise FileNotFoundError(
            f"{path!r} not found. Point --input at the unzipped export folder "
            "(or a message_1.json inside it)."
        )

    threads: Dict[str, List[str]] = {}
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        files = sorted(f for f in filenames if _THREAD_FILE.match(f))
        if files:
            threads[dirpath] = [os.path.join(dirpath, f) for f in files]
    return threads


def _find_profile_names(path: str) -> Set[str]:
    """Owner names from ``personal_information.json``, if the export has one.

    Instagram stores them as
    ``{"profile_user": [{"string_map_data": {"Name": {"value": ...},
    "Username": {"value": ...}}}]}``. Missing or unexpected shapes give an
    empty set rather than an error.
    """
    if not os.path.isdir(path):
        return set()
    names: Set[str] = set()
    for dirpath, _dirnames, filenames in os.walk(path):
        if "personal_information.json" not in filenames:
            continue
        try:
            with open(os.path.join(dirpath, "personal_information.json"), encoding="utf-8") as f:
                data = json.load(f)
            for user in data.get("profile_user", []):
                fields = user.get("string_map_data", {})
                for key in ("Name", "Username"):
                    value = (fields.get(key) or {}).get("value")
                    if value:
                        names.add(fix_mojibake(value))
        except (OSError, ValueError, AttributeError):
            continue
    return names


def _load_thread(files: Iterable[str]):
    """Return ``(participants, messages)`` for one thread, oldest first."""
    participants: Set[str] = set()
    raw: list = []
    # message_1.json holds the newest messages; message_2.json the next-older
    # block, and so on. Read newest to oldest, then reverse the lot.
    for file in sorted(files, key=lambda f: int(re.search(r"(\d+)", os.path.basename(f)).group(1))):
        with open(file, encoding="utf-8") as f:
            data = json.load(f)
        for p in data.get("participants", []):
            name = p.get("name") if isinstance(p, dict) else None
            if name:
                participants.add(fix_mojibake(name))
        raw.extend(data.get("messages", []))
    raw.reverse()
    return participants, raw


def _resolve_self(
    source: str,
    self_name: Optional[str],
    path: str,
    thread_participants: List[Set[str]],
    senders: Set[str],
) -> str:
    """Work out which sender is the export owner (see module docstring)."""
    if self_name:
        return self_name

    for name in sorted(_find_profile_names(path)):
        if name in senders:
            print(f"[{source}] Detected self name from personal_information: {name!r}")
            return name

    if len(thread_participants) >= 2:
        common = set.intersection(*thread_participants)
        if len(common) == 1:
            name = next(iter(common))
            print(f"[{source}] Detected self name (in every thread): {name!r}")
            return name

    found = ", ".join(repr(s) for s in sorted(senders)) or "(none)"
    raise ValueError(
        f"Couldn't tell which sender is you in this {source} export. Pass "
        "--self-name with your display name exactly as it appears in your "
        f"messages. Senders found: {found}."
    )


def parse_export(
    source: str, path: str, self_name: Optional[str] = None
) -> List[NormalizedMessage]:
    """Parse a Meta DYI messages export into normalized messages."""
    threads = find_thread_files(path)
    if not threads:
        raise ValueError(
            f"No message_N.json files found under {path!r}. Make sure the export "
            "was requested in JSON format (not HTML) and is unzipped."
        )

    loaded = []
    for thread_dir, files in sorted(threads.items()):
        participants, raw = _load_thread(files)
        loaded.append((os.path.basename(thread_dir), participants, raw))

    senders = {
        fix_mojibake(m["sender_name"])
        for _, _, raw in loaded
        for m in raw
        if isinstance(m, dict) and m.get("sender_name")
    }
    me = _resolve_self(source, self_name, path, [p for _, p, _ in loaded if p], senders)
    if me not in senders:
        print(
            f"[{source}] Warning: self name {me!r} matches no sender in this "
            f"export (found: {', '.join(repr(s) for s in sorted(senders))}). "
            "Every message will be treated as 'not you' -> all conversations "
            "will be dropped. Check the spelling."
        )

    messages: List[NormalizedMessage] = []
    skipped = 0
    for chat_id, _participants, raw in loaded:
        for m in raw:
            if not isinstance(m, dict) or "timestamp_ms" not in m or not m.get("sender_name"):
                skipped += 1
                continue
            if m.get("is_unsent"):
                continue
            text = fix_mojibake(m.get("content") or "").strip()
            if not text or _is_system(text):
                continue
            sender = fix_mojibake(m["sender_name"])
            messages.append(
                NormalizedMessage(
                    chat_id=chat_id,
                    timestamp=int(m["timestamp_ms"]) // 1000,
                    sender_id=sender,
                    sender_is_self=(sender == me),
                    text=text,
                    message_id=None,
                    reply_to_id=None,
                )
            )
    if skipped:
        print(f"[{source}] Skipped {skipped} entries in an unrecognised shape.")
    print(f"[{source}] Parsed {len(messages)} messages from {len(loaded)} threads.")
    return messages
