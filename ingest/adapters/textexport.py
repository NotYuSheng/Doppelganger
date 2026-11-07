"""Helpers shared by adapters for plain-text, one-chat-per-file exports.

LINE and KakaoTalk exports have the same shape once parsed: a list of
``[timestamp, sender, [text lines]]`` entries, no message ids, and no marker of
which sender is the owner beyond a header naming the chat partner. This module
holds the parts they'd otherwise duplicate.
"""

import os
import re
from typing import Callable, List, Optional

from ingest.message import NormalizedMessage

# Non-breaking / narrow / thin spaces that some phones put inside timestamps
# (e.g. before AM/PM), and invisible direction / zero-width marks that can sit
# before a timestamp or placeholder and stop it from matching.
_ODD_SPACES = re.compile("[   ]")
_INVISIBLE = re.compile("[​‎‏‪-‮⁦-⁩﻿]")


def normalize_line(line: str) -> str:
    """Turn odd spaces into plain spaces and drop invisible marks."""
    return _INVISIBLE.sub("", _ODD_SPACES.sub(" ", line))


def hour_24(hour: int, marker: Optional[str]) -> int:
    """Convert a 12-hour clock hour to 24-hour, given an AM/PM-style marker.

    Accepts English (AM/PM) and Korean/Japanese (오전/오후, 午前/午後) markers;
    no marker means the hour is already 24-hour.
    """
    if marker in ("PM", "오후", "午後") and hour < 12:
        return hour + 12
    if marker in ("AM", "오전", "午前") and hour == 12:
        return 0
    return hour


def resolve_self(
    source: str, senders: List[str], partner: Optional[str], self_name: Optional[str]
) -> str:
    """Work out which sender is the export owner.

    ``partner`` is the name a 1:1 chat's header gives for the other person.
    With exactly two senders and the partner among them, the other sender is
    the owner. Otherwise ``self_name`` is required.
    """
    if self_name:
        if self_name not in senders:
            print(
                f"[{source}] Warning: --self-name {self_name!r} matches no sender in "
                f"this export (found: {', '.join(repr(s) for s in senders)}). "
                "Every message will be treated as 'not you' -> all conversations "
                "will be dropped. Check the spelling."
            )
        return self_name
    if partner and len(senders) == 2 and partner in senders:
        me = next(s for s in senders if s != partner)
        print(f"[{source}] Detected self name (the sender who isn't {partner!r}): {me!r}")
        return me
    raise ValueError(
        f"Couldn't tell which sender is you in this {source} export. Pass "
        "--self-name with your display name exactly as it appears in the chat. "
        f"Senders found: {', '.join(repr(s) for s in senders)}."
    )


def to_messages(
    source: str,
    path: str,
    entries: List[list],
    self_name: str,
    clean: Callable[[str], str],
) -> List[NormalizedMessage]:
    """Build normalized messages from ``[timestamp, sender, [lines]]`` entries.

    Lines are joined with newlines, then passed through ``clean`` (which returns
    "" for placeholders); empty results are dropped. The file name is the
    ``chat_id``, since each export holds one conversation.
    """
    chat_id = os.path.splitext(os.path.basename(path))[0] or source
    messages: List[NormalizedMessage] = []
    for ts, sender, lines in entries:
        text = clean("\n".join(lines))
        if not text:
            continue
        messages.append(
            NormalizedMessage(
                chat_id=chat_id,
                timestamp=ts,
                sender_id=sender,
                sender_is_self=(sender == self_name),
                text=text,
                message_id=None,
                reply_to_id=None,
            )
        )
    print(f"[{source}] Parsed {len(messages)} messages.")
    return messages
