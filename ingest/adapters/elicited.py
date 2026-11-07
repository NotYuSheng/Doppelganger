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

        # Each drill is its own conversation.
        drills = load_answers(path)
        for n, record in enumerate(drills):
            them = _AUDIENCE_SENDER.get(record.get("audience"), "Them")
            self._emit(messages, f"drill:{record.get('scenario')}:{n}", me,
                       [(them, record.get("message", ""), record["reply"])],
                       _timestamp(record.get("answered_at"), fallback=n * 3600))

        # Interview answers: consecutive answers on the same topic form ONE
        # conversation (opening question, answer, follow-up, answer, …), so a
        # follow-up like "How did you get into that?" keeps the answer it
        # refers to as context.
        turns = load_turns(path)
        runs: List[List[dict]] = []
        for record in turns:
            if runs and runs[-1][-1].get("topic") == record.get("topic"):
                runs[-1].append(record)
            else:
                runs.append([record])
        for n, run in enumerate(runs):
            self._emit(messages, f"interview:{run[0].get('topic')}:{n}", me,
                       [("Interviewer", r.get("question", ""), r["reply"]) for r in run],
                       _timestamp(run[0].get("answered_at"), fallback=n * 3600))

        print(f"[elicited] Parsed {len(drills)} drill answer(s) and {len(turns)} interview answer(s).")
        return messages

    @staticmethod
    def _emit(messages, chat_id, me, exchanges, base):
        """Append prompt/reply exchanges with strictly increasing timestamps
        (a couple of seconds apart, so they stay one conversation and in order)."""
        t = base
        for them, prompt, reply in exchanges:
            messages.append(NormalizedMessage(chat_id=chat_id, timestamp=t, sender_id=them,
                                              sender_is_self=False, text=str(prompt).strip()))
            t += 2
            for line in reply:
                if str(line).strip():
                    messages.append(NormalizedMessage(chat_id=chat_id, timestamp=t, sender_id=me,
                                                      sender_is_self=True, text=str(line).strip()))
                    t += 1
            t += 2


register(ElicitedAdapter())
