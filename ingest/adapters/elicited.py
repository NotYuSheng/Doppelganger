"""Elicited data adapter: answers from reply drills (``data/elicited.jsonl``).

Unlike a chat export, this file contains only what the user typed in response
to prompts we wrote (see :mod:`ingest.drills`). Each drill becomes its own
two-sided conversation: the scenario's incoming message from "them", then the
user's reply, one message per line they typed. Who "you" are is known, so no
``--self-name`` is needed.

    python -m ingest --input elicited:data/elicited.jsonl
"""

import os
from datetime import datetime
from typing import List, Optional

from ingest.adapters.base import register
from ingest.drills import load_answers
from ingest.message import NormalizedMessage

_AUDIENCE_SENDER = {
    "friend": "Friend", "family": "Family", "colleague": "Colleague", "acquaintance": "Acquaintance",
}


def _timestamp(value: str, fallback: int) -> int:
    try:
        return int(datetime.fromisoformat(value).timestamp())
    except (TypeError, ValueError):
        return fallback


class ElicitedAdapter:
    name = "elicited"

    def parse(
        self, path: str, *, self_name: Optional[str] = None
    ) -> List[NormalizedMessage]:
        if not os.path.isfile(path):
            raise FileNotFoundError(
                f"{path!r} not found. Answer some reply drills first: "
                "python -m doppelganger drills"
            )
        me = self_name or "Me"
        messages: List[NormalizedMessage] = []
        for n, record in enumerate(load_answers(path)):
            base = _timestamp(record.get("answered_at"), fallback=n * 3600)
            chat_id = f"drill:{record.get('scenario', n)}:{n}"
            them = _AUDIENCE_SENDER.get(record.get("audience"), "Them")
            messages.append(NormalizedMessage(
                chat_id=chat_id, timestamp=base - 60, sender_id=them, sender_is_self=False,
                text=str(record.get("message", "")).strip(),
            ))
            for i, line in enumerate(record["reply"]):
                if str(line).strip():
                    messages.append(NormalizedMessage(
                        chat_id=chat_id, timestamp=base + i, sender_id=me, sender_is_self=True,
                        text=str(line).strip(),
                    ))
        print(f"[elicited] Parsed {sum(1 for m in messages if not m.sender_is_self)} drill answer(s).")
        return messages


register(ElicitedAdapter())
