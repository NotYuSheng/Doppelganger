"""Signal chat-export adapter.

Parses the folder produced by Signal's **Export chat history** (Settings →
Chats → Export chat history; Android, Desktop and iOS since 2026)::

    signal-export-<timestamp>/
    ├── metadata.json
    ├── main.jsonl        ← one JSON object ("frame") per line
    └── files/            ← media, only if exported "with media"

Unlike the other sources, this format is **defined by public code**:
``main.jsonl`` is Signal's backup format (``Backup.proto`` in
signalapp/libsignal) serialized with the Protobuf JSON mapping by libsignal's
``BackupJsonExporter``, which also strips disappearing and view-once messages.
So field names are camelCase and 64-bit numbers are usually strings::

    {"version": "1", "backupTimeMs": "1715636551000", ...}        ← header line
    {"account": {"givenName": "Boba", "familyName": "Fett", ...}}
    {"recipient": {"id": "1", "self": {}}}
    {"recipient": {"id": "4", "contact": {"profileGivenName": "Han", ...}}}
    {"recipient": {"id": "6", "group": {"snapshot": {"title": {"title": "Crew"}}}}}
    {"chat": {"id": "1", "recipientId": "4"}}
    {"chatItem": {"chatId": "1", "authorId": "4", "dateSent": "1700000000000",
                  "incoming": {...},
                  "standardMessage": {"text": {"body": "hi"},
                                      "quote": {"targetSentTimestamp": "…", "authorId": "1"}}}}

What the export gives us that most sources don't:

- **Who "you" are is explicit** (the ``self`` recipient and ``outgoing`` items),
  so no ``--self-name`` is needed.
- **Reply links.** A quote names the quoted message's author and sent time,
  which is how Signal identifies messages, so ``message_id`` /
  ``reply_to_id`` are set to ``"<authorId>:<dateSent>"``.
"""

import json
import os
from typing import Dict, List, Optional

from ingest.adapters.base import register
from ingest.message import NormalizedMessage

# U+FFFC marks where a mention sits in a message body; the mentioned person is
# given separately in bodyRanges.
_MENTION = "￼"


def _int(value) -> Optional[int]:
    """Protobuf JSON writes 64-bit ints as strings; accept either."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _join(*parts) -> str:
    return " ".join(p.strip() for p in parts if isinstance(p, str) and p.strip())


def _contact_name(contact: dict) -> str:
    """Best display name for a contact, mirroring what the app shows."""
    nickname = contact.get("nickname") or {}
    for name in (
        _join(nickname.get("given"), nickname.get("family")),
        contact.get("systemNickname"),
        _join(contact.get("systemGivenName"), contact.get("systemFamilyName")),
        _join(contact.get("profileGivenName"), contact.get("profileFamilyName")),
        contact.get("username"),
        str(contact["e164"]) if contact.get("e164") else None,
    ):
        if name and name.strip():
            return name.strip()
    return "Unknown"


def _resolve_mentions(text: dict, names_by_aci: Dict[str, str]) -> str:
    """Replace mention placeholders in a message body with "@Name".

    ``bodyRanges`` offsets count UTF-16 code units, as in the apps, not Python
    code points, so an emoji before a mention shifts them. The body is sliced
    in UTF-16 to keep the offsets right.
    """
    body = text.get("body") or ""
    if _MENTION not in body:
        return body
    units = body.encode("utf-16-le")
    mention = _MENTION.encode("utf-16-le")
    mentions = sorted(
        (r for r in text.get("bodyRanges") or [] if r.get("mentionAci")),
        key=lambda r: _int(r.get("start")) or 0,
        reverse=True,  # replace from the end so earlier offsets stay valid
    )
    for r in mentions:
        start = 2 * (_int(r.get("start")) or 0)
        end = start + 2 * (_int(r.get("length")) or 1)
        if units[start:end] == mention * ((end - start) // 2):
            name = names_by_aci.get(r["mentionAci"])
            replacement = f"@{name}".encode("utf-16-le") if name else b""
            units = units[:start] + replacement + units[end:]
    return units.decode("utf-16-le").replace(_MENTION, "")


def _find_main_jsonl(path: str) -> str:
    if os.path.isfile(path):
        return path
    if os.path.isdir(path):
        direct = os.path.join(path, "main.jsonl")
        if os.path.isfile(direct):
            return direct
        # Allow pointing at the folder that contains signal-export-<ts>/.
        for dirpath, _dirnames, filenames in sorted(os.walk(path)):
            if "main.jsonl" in filenames:
                return os.path.join(dirpath, "main.jsonl")
    raise FileNotFoundError(
        f"No main.jsonl found at {path!r}. Point --input at the "
        "signal-export-<timestamp> folder from Export chat history."
    )


class SignalAdapter:
    name = "signal"

    def parse(
        self, path: str, *, self_name: Optional[str] = None
    ) -> List[NormalizedMessage]:
        main = _find_main_jsonl(path)

        names: Dict[int, str] = {}          # recipient id -> display name
        self_ids = set()                    # recipient id(s) of the owner
        names_by_aci: Dict[str, str] = {}   # for resolving @mentions
        chat_recipient: Dict[int, int] = {} # chat id -> recipient id
        account_name = ""
        items: List[dict] = []
        bad_lines = 0

        with open(main, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    frame = json.loads(line)
                except ValueError:
                    bad_lines += 1
                    continue
                if not isinstance(frame, dict):
                    bad_lines += 1
                    continue
                if "account" in frame:
                    acct = frame["account"] or {}
                    account_name = _join(acct.get("givenName"), acct.get("familyName")) or acct.get("username") or ""
                elif "recipient" in frame:
                    r = frame["recipient"] or {}
                    rid = _int(r.get("id"))
                    if rid is None:
                        continue
                    if "self" in r:
                        self_ids.add(rid)
                    elif "contact" in r:
                        contact = r["contact"] or {}
                        names[rid] = _contact_name(contact)
                        if contact.get("aci"):
                            names_by_aci[contact["aci"]] = names[rid]
                    elif "group" in r:
                        title = (((r["group"] or {}).get("snapshot") or {}).get("title") or {}).get("title")
                        names[rid] = title or f"group {rid}"
                elif "chat" in frame:
                    c = frame["chat"] or {}
                    cid, rid = _int(c.get("id")), _int(c.get("recipientId"))
                    if cid is not None and rid is not None:
                        chat_recipient[cid] = rid
                elif "chatItem" in frame:
                    items.append(frame["chatItem"] or {})

        me = self_name or account_name or "Me"
        for rid in self_ids:
            names[rid] = me
        if self_name:
            print(f"[signal] Note: Signal marks your own messages, so --self-name "
                  f"is only used as your display name ({self_name!r}).")

        messages: List[NormalizedMessage] = []
        skipped = 0
        for item in items:
            std = item.get("standardMessage")
            text_obj = (std or {}).get("text") or {}
            text = _resolve_mentions(text_obj, names_by_aci).strip() if std else ""
            chat_id, author, sent = (_int(item.get(k)) for k in ("chatId", "authorId", "dateSent"))
            if not text or chat_id is None or author is None or sent is None:
                skipped += 1  # stickers, calls, group updates, deleted, media-only…
                continue
            is_self = "outgoing" in item or author in self_ids
            quote = (std or {}).get("quote") or {}
            q_author, q_sent = _int(quote.get("authorId")), _int(quote.get("targetSentTimestamp"))
            chat_name = names.get(chat_recipient.get(chat_id), f"chat {chat_id}")
            messages.append(
                NormalizedMessage(
                    chat_id=f"{chat_name} ({chat_id})",
                    timestamp=sent // 1000,
                    sender_id=me if is_self else names.get(author, f"recipient {author}"),
                    sender_is_self=is_self,
                    text=text,
                    message_id=f"{author}:{sent}",
                    reply_to_id=f"{q_author}:{q_sent}" if q_author is not None and q_sent is not None else None,
                )
            )

        if not items and not names:
            raise ValueError(
                f"{main!r} doesn't look like a Signal chat export (no recipients "
                "or chat items found)."
            )
        if bad_lines:
            print(f"[signal] Skipped {bad_lines} unreadable lines.")
        print(f"[signal] Parsed {len(messages)} text messages "
              f"({skipped} non-text items skipped) from {len(chat_recipient)} chats.")
        return messages


register(SignalAdapter())
