"""Tests for group-chat handling in sample building (#46).

    python -m unittest tests.test_group_chats
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingest import core, sources
from ingest.message import NormalizedMessage

ME = "Me"


def _m(sender, ts, text, chat="g", mid=None, reply=None):
    return NormalizedMessage(chat_id=chat, timestamp=ts, sender_id=sender,
                             sender_is_self=(sender == ME), text=text,
                             message_id=mid, reply_to_id=reply)


def _texts(samples):
    return [[t["text"] for t in s] for s in samples]


# A busy group: Bob and Carol chat, I answer Bob, then Dave and I talk directly.
GROUP = [
    _m("Bob", 0, "anyone up for lunch?"),
    _m("Carol", 10, "not me"),
    _m("Bob", 20, "how about you?"),
    _m(ME, 30, "sure, noon?"),
    _m("Bob", 40, "noon works"),
    _m("Carol", 50, "unrelated: my cat"),
    _m("Dave", 60, "did you see the game"),
    _m(ME, 70, "yes! wild ending"),
    _m("Dave", 80, "unreal"),
    _m(ME, 90, "rematch next week"),
]
ONE_TO_ONE = [_m("Alice", 0, "hi", chat="dm"), _m(ME, 10, "hey", chat="dm")]


class GroupDetectionTest(unittest.TestCase):
    def test_renamed_contact_is_not_a_group(self):
        # The other person renamed themselves mid-chat: still a 1:1.
        msgs = [_m("Bob", 0, "a", chat="dm"), _m(ME, 10, "b", chat="dm"),
                _m("Bob Tan", 20, "c", chat="dm"), _m(ME, 30, "d", chat="dm")]
        self.assertEqual(len(core.build_samples(msgs, group_chats="skip")), 1)

    def test_two_others_interleaving_is_a_group(self):
        msgs = [_m("Bob", 0, "a"), _m("Carol", 10, "b"), _m("Bob", 20, "c"), _m(ME, 30, "d")]
        self.assertEqual(core.build_samples(msgs, group_chats="skip"), [])

    def test_three_others_is_a_group(self):
        msgs = [_m("Bob", 0, "a"), _m("Carol", 10, "b"), _m("Dan", 20, "c"), _m(ME, 30, "d")]
        self.assertEqual(core.build_samples(msgs, group_chats="skip"), [])


class ReviewRegressionTest(unittest.TestCase):
    def test_reply_used_once_and_paired_with_its_real_target(self):
        # A:'q?'(1), B:'lol', me:'ans'(reply 1), me:'more', C:'x', me:'again'(reply 1)
        msgs = [_m("A", 0, "q?", mid="1"), _m("B", 10, "lol", mid="2"),
                _m(ME, 20, "ans", mid="3", reply="1"), _m(ME, 25, "more", mid="4"),
                _m("C", 30, "x", mid="5"), _m(ME, 40, "again", mid="6", reply="1")]
        samples = core.build_samples(msgs, group_chats="focus")
        self.assertEqual(_texts(samples), [["q?", "ans\nmore"], ["q?", "again"]])


class KeepModeTest(unittest.TestCase):
    def test_default_unchanged_one_sample_per_conversation(self):
        samples = core.build_samples(GROUP + ONE_TO_ONE)
        self.assertEqual(len(samples), 2)

    def test_stats(self):
        stats = {}
        core.build_samples(GROUP + ONE_TO_ONE, stats=stats)
        self.assertEqual(stats, {"chats": 2, "group_chats": 1, "samples": 2, "group_samples": 1})


class SkipModeTest(unittest.TestCase):
    def test_group_chats_left_out(self):
        samples = core.build_samples(GROUP + ONE_TO_ONE, group_chats="skip")
        self.assertEqual(_texts(samples), [["hi", "hey"]])

    def test_two_person_chat_is_not_a_group(self):
        samples = core.build_samples(ONE_TO_ONE, group_chats="skip")
        self.assertEqual(len(samples), 1)


class FocusModeTest(unittest.TestCase):
    def test_keeps_only_direct_exchanges(self):
        samples = core.build_samples(GROUP, group_chats="focus")
        self.assertEqual(_texts(samples), [
            ["how about you?", "sure, noon?", "noon works"],
            ["did you see the game", "yes! wild ending", "unreal", "rematch next week"],
        ])

    def test_stretch_without_me_dropped(self):
        msgs = [_m("Bob", 0, "a"), _m("Carol", 10, "b"), _m("Bob", 20, "c"), _m("Dan", 30, "d"),
                _m(ME, 40, "x")]  # my lone message after Dan -> Dan/me exchange
        samples = core.build_samples(msgs, group_chats="focus")
        self.assertEqual(_texts(samples), [["d", "x"]])

    def test_explicit_reply_across_other_speakers(self):
        # I reply to Bob's question after Carol and Dave spoke in between.
        msgs = [
            _m("Bob", 0, "what time is the meeting?", mid="1"),
            _m("Carol", 10, "lol", mid="2"),
            _m("Dave", 20, "same", mid="3"),
            _m(ME, 30, "3pm", mid="4", reply="1"),
            _m(ME, 35, "in room B", mid="5"),
            _m("Carol", 40, "ok", mid="6"),
        ]
        samples = core.build_samples(msgs, group_chats="focus")
        self.assertIn(["what time is the meeting?", "3pm\nin room B"], _texts(samples))

    def test_reply_already_inside_exchange_not_duplicated(self):
        msgs = [_m("Bob", 0, "q", mid="1"), _m(ME, 10, "a", mid="2", reply="1"),
                _m("Carol", 20, "x", mid="3"), _m("Dan", 30, "y", mid="4")]
        samples = core.build_samples(msgs, group_chats="focus")
        self.assertEqual(_texts(samples), [["q", "a"]])

    def test_reply_to_myself_ignored(self):
        msgs = [_m(ME, 0, "first", mid="1"), _m("Bob", 5, "b", mid="2"), _m("Carol", 6, "c", mid="3"),
                _m("Dan", 7, "d", mid="5"), _m(ME, 10, "adding to my point", mid="4", reply="1")]
        samples = core.build_samples(msgs, group_chats="focus")
        # Only the two direct exchanges; replying to my own message adds no pair.
        self.assertEqual(_texts(samples), [["first", "b"], ["d", "adding to my point"]])

    def test_one_to_one_chats_untouched(self):
        self.assertEqual(core.build_samples(ONE_TO_ONE, group_chats="focus"),
                         core.build_samples(ONE_TO_ONE))

    def test_multi_speaker_still_labels(self):
        samples = core.build_samples(GROUP, group_chats="focus", multi_speaker=True)
        self.assertEqual(samples[0][0]["text"], "Bob: how about you?")

    def test_invalid_mode_raises(self):
        with self.assertRaises(ValueError):
            core.build_samples(GROUP, group_chats="maybe")


class AdviceTest(unittest.TestCase):
    def _results(self, mode):
        spec = sources.SourceSpec("telegram", "x.json")
        stats = {}
        samples = core.build_samples(GROUP + ONE_TO_ONE, group_chats=mode, stats=stats)
        return [sources.SourceResult(spec, 12, len(samples), stats["group_chats"], stats["group_samples"])]

    def _capture(self, mode, multi_speaker=False):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            sources.print_group_advice(self._results(mode), mode, multi_speaker)
        return buf.getvalue()

    def test_keep_mode_suggests_focus(self):
        out = self._capture("keep")
        self.assertIn("1 of 2 sample(s) come from 1 group chat(s)", out)
        self.assertIn("--group-chats focus", out)
        self.assertIn("--multi-speaker", out)

    def test_focus_and_skip_report_what_happened(self):
        self.assertIn("kept 2 sample(s)", self._capture("focus"))
        self.assertIn("Skipped 1 group chat(s)", self._capture("skip"))

    def test_silent_without_groups(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        spec = sources.SourceSpec("telegram", "x.json")
        with redirect_stdout(buf):
            sources.print_group_advice([sources.SourceResult(spec, 2, 1)], "keep", False)
        self.assertEqual(buf.getvalue(), "")


class CliTest(unittest.TestCase):
    def test_flag_reaches_core(self):
        import json
        import tempfile
        from ingest.cli import main
        os.environ["LLM_VALIDATE"] = "false"
        msgs = [
            {"id": 1, "type": "message", "date_unixtime": "0", "from": "Bob", "text": "a"},
            {"id": 2, "type": "message", "date_unixtime": "10", "from": "Carol", "text": "b"},
            {"id": 4, "type": "message", "date_unixtime": "15", "from": "Bob", "text": "a2"},
            {"id": 3, "type": "message", "date_unixtime": "20", "from": "Me Me", "text": "c"},
        ]
        with tempfile.TemporaryDirectory() as d:
            inp = os.path.join(d, "result.json")
            with open(inp, "w") as f:
                json.dump({"personal_information": {"first_name": "Me", "last_name": "Me"},
                           "chats": {"list": [{"id": 1, "messages": msgs}]}}, f)
            out = os.path.join(d, "out.json")
            self.assertEqual(main(["--input", inp, "--output", out, "--skip-redact-scan",
                                   "--group-chats", "skip"]), 0)
            with open(out) as f:
                self.assertEqual(json.load(f), [])


if __name__ == "__main__":
    unittest.main()
