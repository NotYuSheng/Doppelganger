"""Elicited data adapter: reply drills and interview answers (``data/elicited.jsonl``).

Unlike a chat export, this file contains only what the user typed in response
to prompts we wrote (see :mod:`ingest.drills` and :mod:`ingest.interview`).
Each drill or interview question becomes its own two-sided conversation: the
incoming message (or question) from "them", then the user's reply, one message
per line they typed. Who "you" are is known, so no ``--self-name`` is needed.

    python -m ingest --input elicited:data/elicited.jsonl
"""

import os
from datetime import datetime
from typing import List, Optional

from ingest.adapters.base import register
from ingest.drills import load_answers
from ingest.interview import load_turns
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
        records = [(r, f"drill:{r.get('scenario')}", _AUDIENCE_SENDER.get(r.get("audience"), "Them"),
                    r.get("message", "")) for r in load_answers(path)]
        records += [(r, f"interview:{r.get('topic')}", "Interviewer", r.get("question", ""))
                    for r in load_turns(path)]
        for n, (record, kind, them, prompt) in enumerate(records):
            base = _timestamp(record.get("answered_at"), fallback=n * 3600)
            chat_id = f"{kind}:{n}"
            messages.append(NormalizedMessage(
                chat_id=chat_id, timestamp=base - 60, sender_id=them, sender_is_self=False,
                text=str(prompt).strip(),
            ))
            for i, line in enumerate(record["reply"]):
                if str(line).strip():
                    messages.append(NormalizedMessage(
                        chat_id=chat_id, timestamp=base + i, sender_id=me, sender_is_self=True,
                        text=str(line).strip(),
                    ))
        drills_n = sum(1 for r in records if r[1].startswith("drill"))
        print(f"[elicited] Parsed {drills_n} drill answer(s) and {len(records) - drills_n} interview answer(s).")
        return messages


register(ElicitedAdapter())
