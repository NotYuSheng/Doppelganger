"""Shared parser for Meta's message exports (Instagram and Facebook Messenger).

Two export shapes are understood, and an ``--input`` folder may contain either
or both (for Messenger, unzip both downloads into one folder):

**1. Download Your Information (DYI), format JSON.** One folder per
conversation, with long threads split across ``message_1.json``,
``message_2.json``, …::

    <export root>/your_instagram_activity/messages/inbox/alice_123/message_1.json
    (Messenger: your_facebook_activity/messages/{inbox,archived_threads,e2ee_cutover}/…,
     older exports: messages/inbox/…)

    {"participants": [{"name": "Alice"}, {"name": "You"}],
     "messages": [{"sender_name": "Alice", "timestamp_ms": 1700000000000,
                   "content": "hi", "type": "Generic"}, ...],
     "title": "Alice", "thread_path": "inbox/alice_123"}

**2. Messenger end-to-end encrypted chats ("Download secure storage data").**
Since late 2023 most Messenger chats are end-to-end encrypted and are **not** in
DYI. Their separate download is a flat folder with one file per conversation
(``alice_1.json``) and a ``media/`` folder next to them::

    {"participants": ["You", "Alice"], "threadName": "Alice",
     "messages": [{"senderName": "Alice", "text": "hi",
                   "timestamp": 1700000000000, "type": "Generic",
                   "isUnsent": false, "media": [], "reactions": []}, ...]}

Quirks absorbed here, so the shared pipeline never sees them:

- **Mojibake.** Meta writes UTF-8 bytes as if each byte were a Latin-1
  character ("ðŸ‘" for "👍"), so every string is repaired with
  ``encode("latin-1").decode("utf-8")``. Strings that are already correct fail
  that round trip and are left unchanged.
- **Newest-first order** (DYI). Messages are put back in chronological order,
  keeping the original order for same-millisecond messages.
- **Overlap between the two downloads.** A chat that switched to encryption can
  appear in both (``e2ee_cutover/`` in DYI and the secure-storage file), so
  exact duplicates (same sender, millisecond and text) are dropped.
- **No message ids or reply links**, so ``message_id`` / ``reply_to_id`` stay
  ``None`` and the pipeline groups by time.
- **Who "you" are isn't marked on each message.** It's resolved from
  ``--self-name``, then the export's profile file, then "the one participant
  present in every thread".
- **Non-text events** (reactions, likes, "sent an attachment", unsent messages,
  shared posts without a caption, calls, group notices) are dropped.

Neither shape is officially documented. The field names above come from
community parsers (e.g. msgvault, chatpack) and public sample exports, and
should be re-checked when Meta changes them.
"""

import json
import os
import re
from typing import Dict, List, NamedTuple, Optional, Set, Tuple

from ingest.message import NormalizedMessage

_DYI_FILE = re.compile(r"^message_\d+\.json$")

# Folder names whose threads aren't real conversations you took part in:
# unanswered requests from strangers and spam-filtered threads. Media folders
# hold no thread JSON, so skipping them just saves time.
_SKIP_DIRS = {"message_requests", "filtered_threads", "media", "photos", "videos",
              "audio", "gifs", "files", "stickers_used"}

# Message "type" values that never carry text you wrote.
_SYSTEM_TYPES = {"Call", "Subscribe", "Unsubscribe"}

# Content Meta generates for non-text events, matched against the whole message
# (case-insensitive) after mojibake repair. Where a pattern names a person
# ("Alice sent an attachment.", "You called Alice."), the named ``who`` /
# ``target`` must be "You" or someone in the thread. Without that check, real
# messages like "You called it!" or "I left the chat" would be thrown away.
_SYSTEM_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^liked a message\.?$",
        r"^reacted .{1,8} to (your|a) message\.?$",
        r"^(?P<who>.+) sent an attachment\.$",
        r"^sent an attachment\.$",
        r"^(?P<who>.+) sent a (photo|video|voice message|gif|sticker|link)\.$",
        r"^(?P<who>.+) shared a (story|post|reel|link)\.$",
        r"^(?P<who>.+) unsent a message\.$",
        r"^(?P<who>.+) (started|ended|missed) (a|an|the|your) (audio |video )?(call|chat)\.$",
        r"^(started|ended|missed) an? (audio |video )?(call|chat)$",
        r"^(audio|video) (call|chat) ended$",
        r"^the (audio |video )?(call|chat) ended\.$",
        r"^you called (?P<target>.+)\.$",
        r"^(?P<who>.+) called you\.$",
        r"^(?P<who>.+) missed your (audio |video )?call\.$",
        r"^you missed an? (audio |video )?call from (?P<target>.+)\.$",
        r"^(?P<who>.+) (created|named) the group\b.*$",
        r"^(?P<who>.+) (added|removed) (?P<target>.+) (to|from) the group\.$",
        r"^(?P<who>.+) left the (group|chat|conversation)\.$",
        r"^(?P<who>.+) changed the group (name|photo)\b.*$",
        r"^mentioned you in (their|a) story\.?$",
    )
]


class _Msg(NamedTuple):
    """One message, already normalized from either export shape."""

    sender: str
    ts_ms: int
    text: str
    unsent: bool
    kind: str  # Meta's "type" ("Generic", "Call", ...) or ""


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


def _is_system(text: str, names: Set[str]) -> bool:
    """Whether ``text`` is a Meta-generated event notice.

    ``names`` holds the lower-cased names of the thread's participants and
    senders; "you" is always accepted.
    """
    for p in _SYSTEM_PATTERNS:
        m = p.match(text)
        if not m:
            continue
        people = [v for k, v in m.groupdict().items() if v is not None]
        if all(v.strip().lower() in names or v.strip().lower() == "you" for v in people):
            return True
    return False


def _read_json(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _is_e2ee_thread(data) -> bool:
    """Whether parsed JSON is a secure-storage (E2EE) thread file."""
    return (
        isinstance(data, dict)
        and isinstance(data.get("messages"), list)
        and isinstance(data.get("participants"), list)
        and all(isinstance(p, str) for p in data["participants"])
    )


def find_threads(path: str, cache: Optional[Dict[str, object]] = None) -> Dict[str, List[str]]:
    """Map a thread id to the JSON file(s) holding that conversation.

    ``path`` may be an unzipped export root (DYI, secure storage, or both
    unzipped into one folder), any folder inside it, one DYI thread folder, or a
    single thread file. DYI threads are keyed by their folder name
    (``alice_123``), secure-storage threads by file name (``alice_1``). The same
    conversation can appear in more than one place (``inbox/`` and
    ``e2ee_cutover/``), so files with the same key are combined, not replaced.
    Message requests and spam-filtered threads are skipped.

    Secure-storage files have no fixed name, so they're recognised by shape.
    Only folders where they live (named ``messages``, or the ``--input`` folder
    itself) are probed. Parsed files are kept in ``cache`` so they aren't read
    twice.
    """
    if os.path.isfile(path):
        if _DYI_FILE.match(os.path.basename(path)):
            return {os.path.basename(os.path.dirname(os.path.abspath(path))): [path]}
        return {os.path.splitext(os.path.basename(path))[0]: [path]}
    if not os.path.isdir(path):
        raise FileNotFoundError(
            f"{path!r} not found. Point --input at the unzipped export folder "
            "(or a thread .json file inside it)."
        )

    root = os.path.abspath(path)
    threads: Dict[str, List[str]] = {}
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        dyi = sorted(f for f in filenames if _DYI_FILE.match(f))
        if dyi:
            threads.setdefault(os.path.basename(dirpath), []).extend(
                os.path.join(dirpath, f) for f in dyi)
            continue
        if os.path.basename(dirpath) != "messages" and os.path.abspath(dirpath) != root:
            continue
        for f in sorted(filenames):
            if not f.endswith(".json"):
                continue
            full = os.path.join(dirpath, f)
            try:
                data = _read_json(full)
            except (OSError, ValueError):
                continue
            if _is_e2ee_thread(data):
                if cache is not None:
                    cache[full] = data
                threads.setdefault(os.path.splitext(f)[0], []).append(full)
    return threads


def _find_profile_names(path: str) -> Set[str]:
    """The owner's names from the export's profile file, if there is one.

    Instagram: ``personal_information.json`` →
    ``profile_user[0].string_map_data.{Name,Username}.value``.
    Facebook: ``profile_information.json`` → ``profile_v2.name.full_name``.
    Missing or unexpected shapes give an empty set rather than an error.
    """
    if not os.path.isdir(path):
        return set()
    names: Set[str] = set()
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and d != "inbox"]
        for fname in ("personal_information.json", "profile_information.json"):
            if fname not in filenames:
                continue
            try:
                data = _read_json(os.path.join(dirpath, fname))
                for user in data.get("profile_user", []) or []:
                    fields = user.get("string_map_data", {})
                    for key in ("Name", "Username"):
                        value = (fields.get(key) or {}).get("value")
                        if value:
                            names.add(fix_mojibake(value))
                full = ((data.get("profile_v2") or {}).get("name") or {}).get("full_name")
                if full:
                    names.add(fix_mojibake(full))
            except (OSError, ValueError, AttributeError):
                continue
    return names


def _part_number(file: str) -> int:
    """The N in ``message_N.json`` (0 for a file named anything else)."""
    m = re.search(r"(\d+)", os.path.basename(file))
    return int(m.group(1)) if m else 0


def _normalize(m) -> Optional[_Msg]:
    """Turn one raw message (either shape) into a :class:`_Msg`, or None."""
    if not isinstance(m, dict):
        return None
    sender = m.get("sender_name") or m.get("senderName")
    ts = m.get("timestamp_ms", m.get("timestamp"))
    if not sender or not isinstance(ts, (int, float)):
        return None
    text = m.get("content") if "content" in m else m.get("text")
    return _Msg(
        sender=fix_mojibake(sender),
        ts_ms=int(ts),
        text=fix_mojibake(text or "").strip(),
        unsent=bool(m.get("is_unsent") or m.get("isUnsent")),
        kind=m.get("type") or "",
    )


def _load_thread(
    files: List[str], cache: Optional[Dict[str, object]] = None
) -> Tuple[Set[str], List[_Msg], int]:
    """Return ``(participants, messages oldest-first, unrecognised count)``."""
    participants: Set[str] = set()
    raw: list = []
    # message_1.json holds the newest messages; message_2.json the next-older
    # block, and so on. Read newest to oldest, then reverse the lot.
    for file in sorted(files, key=_part_number):
        try:
            data = cache.pop(file) if cache and file in cache else _read_json(file)
        except (OSError, ValueError) as e:
            print(f"[meta] Warning: skipping unreadable file {file!r}: {e}")
            continue
        if not isinstance(data, dict):
            print(f"[meta] Warning: skipping {file!r}: not a conversation file")
            continue
        for p in data.get("participants", []):
            name = p.get("name") if isinstance(p, dict) else p
            if isinstance(name, str) and name:
                participants.add(fix_mojibake(name))
        raw.extend(data.get("messages", []))
    raw.reverse()

    msgs: List[_Msg] = []
    bad = 0
    for m in raw:
        n = _normalize(m)
        if n is None:
            bad += 1
        else:
            msgs.append(n)
    # Reversing gives chronological order for newest-first DYI files and keeps
    # same-millisecond order; the stable sort also fixes files that were
    # already oldest-first (secure storage, some third-party files).
    msgs.sort(key=lambda m: m.ts_ms)
    return participants, msgs, bad


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
            print(f"[{source}] Detected self name from your profile: {name!r}")
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
    """Parse a Meta messages export into normalized messages."""
    cache: Dict[str, object] = {}
    threads = find_threads(path, cache)
    if not threads:
        raise ValueError(
            f"No conversation files found under {path!r}. Make sure the export "
            "was requested in JSON format (not HTML) and is unzipped."
        )

    loaded = []
    skipped = 0
    for chat_id, files in sorted(threads.items()):
        participants, msgs, bad = _load_thread(files, cache)
        skipped += bad
        loaded.append((chat_id, participants, msgs))

    senders = {m.sender for _, _, msgs in loaded for m in msgs}
    me = _resolve_self(source, self_name, path, [p for _, p, _ in loaded if p], senders)
    if me not in senders:
        print(
            f"[{source}] Warning: self name {me!r} matches no sender in this "
            f"export (found: {', '.join(repr(s) for s in sorted(senders))}). "
            "Every message will be treated as 'not you' -> all conversations "
            "will be dropped. Check the spelling."
        )

    messages: List[NormalizedMessage] = []
    seen: Set[Tuple[str, int, str]] = set()
    duplicates = 0
    for chat_id, participants, msgs in loaded:
        names = {n.lower() for n in participants} | {m.sender.lower() for m in msgs}
        for m in msgs:
            if m.unsent or m.kind in _SYSTEM_TYPES or not m.text or _is_system(m.text, names):
                continue
            key = (m.sender, m.ts_ms, m.text)
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            messages.append(
                NormalizedMessage(
                    chat_id=chat_id,
                    timestamp=m.ts_ms // 1000,
                    sender_id=m.sender,
                    sender_is_self=(m.sender == me),
                    text=m.text,
                    message_id=None,
                    reply_to_id=None,
                )
            )
    if skipped:
        print(f"[{source}] Skipped {skipped} entries in an unrecognised shape.")
    if duplicates:
        print(f"[{source}] Dropped {duplicates} messages that appeared in both downloads.")
    print(f"[{source}] Parsed {len(messages)} messages from {len(loaded)} threads.")
    return messages
