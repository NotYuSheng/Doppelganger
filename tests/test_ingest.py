"""Fast unit tests for the ingestion pipeline (stdlib unittest, no GPU/network).

Run from the repo root:

    python -m unittest discover -s tests -t .

These lock in the conversation-building behaviour (chaining, gap-splitting,
group-chat merging, role assignment, ShareGPT mapping) so you don't have to run
the full pipeline to know a change is safe.
"""

import json
import os
import sys
import tempfile
import unittest

# Make the repo root importable when run via `python tests/test_ingest.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingest import core, sharegpt
from ingest.adapters import available_sources, get_adapter
from ingest.adapters.telegram import TelegramAdapter
from ingest.message import NormalizedMessage

SELF = "Yu Sheng"


def _msg(frm, t, text=None, mtype="message", entities=None):
    msg = {"type": mtype, "from": frm, "date_unixtime": str(t)}
    if entities is not None:
        msg["text"] = entities
        msg["text_entities"] = entities
    else:
        msg["text"] = text
    return msg


def _fixture():
    """Synthetic Telegram export exercising the interesting edge cases."""
    return {
        "personal_information": {"first_name": "Yu", "last_name": "Sheng"},
        "chats": {"list": [
            {"id": 111, "messages": [
                _msg("Alice", 1000, "hey there"),
                _msg("Alice", 1010, "you around?"),                 # chained (10s)
                {"type": "service", "from": "Alice", "date_unixtime": "1012", "text": "pinned"},
                _msg(SELF, 1020, "yeah whats up"),
                _msg(SELF, 1100, "still here"),                     # 80s > 30 -> chain breaks, same role merges
                _msg("Alice", 1200, "cool"),
                _msg("Alice", 6000, "later message"),              # >3600s gap -> new conversation
                _msg(SELF, 6010, entities=[{"type": "plain", "text": "re"},
                                           {"type": "plain", "text": "ply"}]),
            ]},
            {"id": 222, "messages": [                               # group chat, two non-self senders
                _msg("Bob", 2000, "q1"),
                _msg("Carol", 2005, "q2"),                         # different sender, both -> user, merged
                _msg(SELF, 2010, "answer"),
                _msg("Bob", 2020, ""),                             # empty -> invalid, skipped
                _msg(SELF, 2030, "more"),                          # same role -> merged
            ]},
            {"id": 333, "messages": [                               # only self -> dropped (no user turn)
                _msg(SELF, 3000, "talking to myself"),
            ]},
        ]},
    }


def _write_fixture(dir_path):
    path = os.path.join(dir_path, "result.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_fixture(), f, ensure_ascii=False)
    return path


# Golden expected output for the fixture above.
EXPECTED_SHAREGPT = [
    {"conversations": [
        {"from": "human", "value": "hey there\nyou around?"},
        {"from": "gpt", "value": "yeah whats up\nstill here"},
        # trailing human turn ("cool") is trimmed so the sample ends on gpt
        # (LLaMA-Factory rejects odd-length / trailing-human conversations).
    ]},
    {"conversations": [
        {"from": "human", "value": "later message"},
        {"from": "gpt", "value": "reply"},
    ]},
    {"conversations": [
        {"from": "human", "value": "q1\nq2"},
        {"from": "gpt", "value": "answer\nmore"},
    ]},
]


class TelegramAdapterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = _write_fixture(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_detects_self_and_filters_invalid(self):
        msgs = TelegramAdapter().parse(self.path)
        # 8 valid in chat 111 minus 1 service = 7; chat 222 = 4 (empty dropped); chat 333 = 1.
        self.assertEqual(len(msgs), 7 + 4 + 1)
        self.assertTrue(all(m.text for m in msgs))            # no empty text
        self.assertTrue(any(m.sender_is_self for m in msgs))
        self.assertTrue(any(not m.sender_is_self for m in msgs))

    def test_entity_list_text_is_joined(self):
        msgs = TelegramAdapter().parse(self.path)
        reply = [m for m in msgs if m.timestamp == 6010][0]
        self.assertEqual(reply.text, "reply")
        self.assertTrue(reply.sender_is_self)

    def test_self_name_override(self):
        msgs = TelegramAdapter().parse(self.path, self_name="Alice")
        alice = [m for m in msgs if m.sender_id == "Alice"][0]
        self.assertTrue(alice.sender_is_self)

    def test_missing_from_becomes_unknown(self):
        # "from" can be missing/None (anonymous channel posts); sender_id must
        # stay a str rather than None.
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "result.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"personal_information": {"first_name": "Yu", "last_name": "Sheng"},
                           "chats": {"list": [{"id": 1, "messages": [
                               _msg(None, 100, "anon post"),
                               _msg(SELF, 110, "reply")]}]}}, f)
            msgs = TelegramAdapter().parse(path)
            anon = [m for m in msgs if m.timestamp == 100][0]
            self.assertEqual(anon.sender_id, "Unknown")
            self.assertFalse(anon.sender_is_self)

    def test_undetectable_self_name_raises(self):
        # Without personal_information, auto-detection yields "" — which would
        # silently drop every conversation. Must fail loudly instead.
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "result.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"chats": {"list": []}}, f)
            with self.assertRaises(ValueError):
                TelegramAdapter().parse(path)


class CoreTest(unittest.TestCase):
    def _samples(self):
        msgs = TelegramAdapter().parse(_write_fixture(tempfile.mkdtemp()))
        return core.build_samples(msgs)

    def test_golden_samples(self):
        samples = self._samples()
        self.assertEqual(sharegpt.to_sharegpt(samples), EXPECTED_SHAREGPT)

    def test_self_only_conversation_dropped(self):
        # chat 333 (only self) must not appear -> exactly 3 samples.
        self.assertEqual(len(self._samples()), 3)

    def test_gap_splits_conversations(self):
        # chat 111 yields two samples because of the >3600s gap.
        samples = self._samples()
        first_two = sharegpt.to_sharegpt(samples)[:2]
        self.assertEqual(first_two, EXPECTED_SHAREGPT[:2])


def _nm(chat, ts, sender, is_self, text, mid=None, reply=None):
    return NormalizedMessage(
        chat_id=chat, timestamp=ts, sender_id=sender, sender_is_self=is_self,
        text=text, message_id=mid, reply_to_id=reply,
    )


class ReplyThreadingTest(unittest.TestCase):
    def test_reply_stitches_gap_split_conversations(self):
        # Two messages an hour+ apart would split into two conversations, but the
        # second replies to the first -> they must end up in one sample.
        msgs = [
            _nm("c", 1000, "Alice", False, "you free this weekend?", mid="1"),
            _nm("c", 1000 + 8000, "Yu", True, "yeah sun works", mid="2", reply="1"),
        ]
        samples = core.build_samples(msgs)
        self.assertEqual(len(samples), 1)
        self.assertEqual([t["role"] for t in samples[0]], ["user", "assistant"])

    def test_no_reply_data_keeps_time_split(self):
        # Same timing, no reply link -> still two conversations (one is one-sided
        # and dropped), proving threading is a no-op without reply metadata.
        msgs = [
            _nm("c", 1000, "Alice", False, "you free this weekend?"),
            _nm("c", 1000 + 8000, "Yu", True, "yeah sun works"),
        ]
        self.assertEqual(core.build_samples(msgs), [])


class MultiSpeakerTest(unittest.TestCase):
    def _group(self):
        return [
            _nm("g", 1, "Bob", False, "q1"),
            _nm("g", 2, "Carol", False, "q2"),
            _nm("g", 3, "Yu", True, "answer"),
        ]

    def test_default_collapses_other_side(self):
        out = sharegpt.to_sharegpt(core.build_samples(self._group()))
        self.assertEqual(out[0]["conversations"][0], {"from": "human", "value": "q1\nq2"})

    def test_multi_speaker_labels_users_not_assistant(self):
        out = sharegpt.to_sharegpt(core.build_samples(self._group(), multi_speaker=True))
        convs = out[0]["conversations"]
        # Distinct speakers are labelled, then merged into one human turn so the
        # conversation alternates human/gpt (LLaMA-Factory rejects consecutive
        # same-role turns). The labels are preserved in the merged text...
        self.assertEqual(convs[0], {"from": "human", "value": "Bob: q1\nCarol: q2"})
        # ...but the owner's (assistant) turn is never labelled.
        self.assertEqual(convs[1], {"from": "gpt", "value": "answer"})


class ShareGptTest(unittest.TestCase):
    def test_role_mapping_and_drop_one_sided(self):
        samples = [
            [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "yo"}],
            [{"role": "user", "text": "only me"}],                # no assistant -> dropped
            [{"role": "assistant", "text": ""}, {"role": "user", "text": "x"}],  # empty + one-sided -> dropped
        ]
        out = sharegpt.to_sharegpt(samples)
        self.assertEqual(out, [{"conversations": [
            {"from": "human", "value": "hi"}, {"from": "gpt", "value": "yo"}]}])

    def test_jsonl_roundtrip(self):
        samples = [[{"role": "user", "text": "hi"}, {"role": "assistant", "text": "yo"}]]
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "out.jsonl")
            sharegpt.write_jsonl(samples, p)
            self.assertEqual(sharegpt.load_jsonl_samples(p), samples)


class CoerceAlternatingTest(unittest.TestCase):
    """LLaMA-Factory requires each conversation to start with human, strictly
    alternate, and end with gpt. to_sharegpt must coerce raw chats into that
    shape rather than emit samples that get silently dropped at train time.
    """

    def _conv(self, samples):
        return sharegpt.to_sharegpt(samples)[0]["conversations"]

    def test_leading_assistant_turn_is_stripped(self):
        # The other person messaged first -> drop the leading gpt turn.
        samples = [[
            {"role": "assistant", "text": "u there?"},
            {"role": "user", "text": "ya"},
            {"role": "assistant", "text": "ok"},
        ]]
        self.assertEqual(self._conv(samples), [
            {"from": "human", "value": "ya"},
            {"from": "gpt", "value": "ok"},
        ])

    def test_trailing_user_turn_is_stripped(self):
        # Conversation ends on a human turn (odd length) -> drop it so it ends gpt.
        samples = [[
            {"role": "user", "text": "hi"},
            {"role": "assistant", "text": "yo"},
            {"role": "user", "text": "u free?"},
        ]]
        self.assertEqual(self._conv(samples), [
            {"from": "human", "value": "hi"},
            {"from": "gpt", "value": "yo"},
        ])

    def test_consecutive_same_role_turns_are_merged(self):
        # Same-role runs (e.g. from reply-stitching) are merged, not left to
        # break alternation.
        samples = [[
            {"role": "user", "text": "a"},
            {"role": "user", "text": "b"},
            {"role": "assistant", "text": "c"},
            {"role": "assistant", "text": "d"},
        ]]
        self.assertEqual(self._conv(samples), [
            {"from": "human", "value": "a\nb"},
            {"from": "gpt", "value": "c\nd"},
        ])

    def test_no_usable_pair_is_dropped(self):
        # A lone human turn (after trimming) leaves nothing trainable.
        self.assertEqual(sharegpt.to_sharegpt([[{"role": "user", "text": "hello?"}]]), [])

    def test_output_is_even_length_and_well_formed(self):
        # Every emitted conversation must start human, end gpt, and alternate.
        for record in sharegpt.to_sharegpt(EXPECTED_SAMPLES_FOR_SHAPE):
            convs = record["conversations"]
            self.assertEqual(len(convs) % 2, 0)
            self.assertEqual(convs[0]["from"], "human")
            self.assertEqual(convs[-1]["from"], "gpt")
            expected = ["human", "gpt"] * (len(convs) // 2)
            self.assertEqual([c["from"] for c in convs], expected)


# Messy inputs that all must come out well-formed.
EXPECTED_SAMPLES_FOR_SHAPE = [
    [{"role": "assistant", "text": "first"}, {"role": "user", "text": "u"},
     {"role": "assistant", "text": "me"}],
    [{"role": "user", "text": "x"}, {"role": "user", "text": "y"},
     {"role": "assistant", "text": "z"}, {"role": "user", "text": "trailing"}],
]


class ValidatorSplitTest(unittest.TestCase):
    def test_apply_split_cuts_after_indices(self):
        from ingest.validator import _apply_split
        turns = [{"role": "user", "text": "a"}, {"role": "assistant", "text": "b"},
                 {"role": "user", "text": "c"}, {"role": "assistant", "text": "d"}]
        pieces = _apply_split(turns, [1])
        self.assertEqual(len(pieces), 2)
        self.assertEqual(pieces[0], turns[:2])
        self.assertEqual(pieces[1], turns[2:])

    def test_apply_split_ignores_out_of_range(self):
        from ingest.validator import _apply_split
        turns = [{"role": "user", "text": "a"}, {"role": "assistant", "text": "b"}]
        # Index at/after the last turn is meaningless -> no split.
        self.assertEqual(_apply_split(turns, [1, 9]), [turns])

    def test_has_both_roles(self):
        from ingest.validator import _has_both_roles
        self.assertTrue(_has_both_roles([{"role": "user"}, {"role": "assistant"}]))
        self.assertFalse(_has_both_roles([{"role": "user"}, {"role": "user"}]))


class _FakeOpenAI:
    """Stub OpenAI-compatible client returning canned JSON (no network)."""
    def __init__(self, text):
        import types
        msg = types.SimpleNamespace(content=text)
        resp = types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=lambda **kw: resp))


class ValidatorSplitPriorityTest(unittest.TestCase):
    def test_split_runs_even_when_scores_are_low(self):
        from ingest import validator, llm
        canned = ('{"coherence":0.2,"quality":0.2,"pairing":0.2,'
                  '"action":"split","split_after":[1],"reason":"two convos"}')
        orig_get, orig_should = llm.get_client, llm.should_validate
        llm.get_client = lambda: _FakeOpenAI(canned)
        llm.should_validate = lambda: True
        os.environ["LLM_MODEL"] = "x"
        try:
            sample = [{"role": "user", "text": "a"}, {"role": "assistant", "text": "b"},
                      {"role": "user", "text": "c"}, {"role": "assistant", "text": "d"}]
            out = validator.validate_samples([sample])
            # Low scores would previously drop it; now split runs first -> 2 pieces.
            self.assertEqual(len(out), 2)
        finally:
            llm.get_client, llm.should_validate = orig_get, orig_should
            os.environ.pop("LLM_MODEL", None)


class RegistryTest(unittest.TestCase):
    def test_telegram_registered(self):
        self.assertIn("telegram", available_sources())
        self.assertEqual(get_adapter("telegram").name, "telegram")

    def test_whatsapp_registered(self):
        self.assertIn("whatsapp", available_sources())
        self.assertEqual(get_adapter("whatsapp").name, "whatsapp")

    def test_unknown_source_raises(self):
        with self.assertRaises(ValueError):
            get_adapter("myspace")


class CliTest(unittest.TestCase):
    def test_end_to_end_sharegpt(self):
        from ingest.cli import main
        os.environ["LLM_VALIDATE"] = "false"  # no API calls
        with tempfile.TemporaryDirectory() as d:
            inp = _write_fixture(d)
            out = os.path.join(d, "chat_sharegpt.json")
            rc = main(["--source", "telegram", "--input", inp, "--output", out])
            self.assertEqual(rc, 0)
            with open(out, encoding="utf-8") as f:
                self.assertEqual(json.load(f), EXPECTED_SHAREGPT)

    def test_unknown_source_exit_code(self):
        from ingest.cli import main
        self.assertEqual(main(["--source", "nope"]), 2)

    def test_redact_applies_even_when_scan_skipped(self):
        # --redact must still redact when the scan is skipped (no silent leak).
        from ingest.cli import main
        os.environ["LLM_VALIDATE"] = "false"
        with tempfile.TemporaryDirectory() as d:
            inp = os.path.join(d, "result.json")
            with open(inp, "w", encoding="utf-8") as f:
                json.dump({"personal_information": {"first_name": "Yu", "last_name": "Sheng"},
                           "chats": {"list": [{"id": 1, "messages": [
                               _msg("Alice", 100, "mail me at a@b.com"),
                               _msg(SELF, 110, "ok")]}]}}, f)
            out = os.path.join(d, "out.json")
            rc = main(["--source", "telegram", "--input", inp, "--output", out,
                       "--skip-redact-scan", "--redact", "replace"])
            self.assertEqual(rc, 0)
            blob = json.dumps(json.load(open(out, encoding="utf-8")))
            self.assertIn("[EMAIL]", blob)
            self.assertNotIn("a@b.com", blob)


if __name__ == "__main__":
    unittest.main()
