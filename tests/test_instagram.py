"""Unit tests for the Instagram adapter and the shared Meta DYI parser.

Run from the repo root:

    python -m unittest tests.test_instagram

All fixtures are synthetic. They lock in the Meta-specific quirks: mojibake
repair, newest-first ordering across split ``message_N.json`` files, non-text
event filtering, request/filtered thread skipping, and how "you" is resolved.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingest.adapters import available_sources, get_adapter
from ingest.adapters.instagram import InstagramAdapter
from ingest.adapters.meta import fix_mojibake

SELF = "Yu Sheng"


def _mojibake(s: str) -> str:
    """Encode a string the way Meta's export does (UTF-8 read as Latin-1)."""
    return s.encode("utf-8").decode("latin-1")


def _msg(sender, ts_s, content=None, **extra):
    m = {"sender_name": sender, "timestamp_ms": ts_s * 1000}
    if content is not None:
        m["content"] = content
    m.update(extra)
    return m


def _write_thread(root, box, thread, messages, participants=("Alice", SELF), part=1):
    """Write one message_N.json. ``messages`` are given oldest-first, as they're
    easier to read that way, and stored newest-first like Meta does."""
    d = os.path.join(root, "your_instagram_activity", "messages", box, thread)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"message_{part}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "participants": [{"name": p} for p in participants],
                "messages": list(reversed(messages)),
                "title": participants[0],
                "thread_path": f"{box}/{thread}",
            },
            f,
        )
    return path


def _write_profile(root, name, username="yusheng"):
    d = os.path.join(root, "personal_information", "personal_information")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "personal_information.json"), "w", encoding="utf-8") as f:
        json.dump(
            {"profile_user": [{"string_map_data": {
                "Name": {"value": name, "href": "", "timestamp": 0},
                "Username": {"value": username, "href": "", "timestamp": 0},
            }}]},
            f,
        )


class MojibakeTest(unittest.TestCase):
    def test_repairs_emoji_and_accents(self):
        self.assertEqual(fix_mojibake(_mojibake("déjà vu 👍")), "déjà vu 👍")

    def test_leaves_correct_strings_alone(self):
        for s in ("plain ascii", "已经是正确的", "👍"):
            self.assertEqual(fix_mojibake(s), s)


class ParseTest(unittest.TestCase):
    def test_basic_thread_chronological(self):
        with tempfile.TemporaryDirectory() as d:
            _write_thread(d, "inbox", "alice_123", [
                _msg("Alice", 100, "hey"),
                _msg(SELF, 110, "hi!"),
                _msg("Alice", 120, "how are you"),
            ])
            msgs = InstagramAdapter().parse(d, self_name=SELF)
        self.assertEqual([m.text for m in msgs], ["hey", "hi!", "how are you"])
        self.assertEqual([m.timestamp for m in msgs], [100, 110, 120])
        self.assertEqual([m.sender_is_self for m in msgs], [False, True, False])
        self.assertTrue(all(m.chat_id == "alice_123" for m in msgs))
        self.assertTrue(all(m.message_id is None and m.reply_to_id is None for m in msgs))

    def test_split_files_merge_in_order(self):
        # message_1.json holds the newest block, message_2.json the older one.
        with tempfile.TemporaryDirectory() as d:
            _write_thread(d, "inbox", "alice_123", [
                _msg("Alice", 300, "newest a"), _msg(SELF, 310, "newest b")], part=1)
            _write_thread(d, "inbox", "alice_123", [
                _msg("Alice", 100, "oldest a"), _msg(SELF, 110, "oldest b")], part=2)
            msgs = InstagramAdapter().parse(d, self_name=SELF)
        self.assertEqual([m.text for m in msgs], ["oldest a", "oldest b", "newest a", "newest b"])

    def test_same_second_order_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            _write_thread(d, "inbox", "t", [
                {"sender_name": "Alice", "timestamp_ms": 100_001, "content": "first"},
                {"sender_name": "Alice", "timestamp_ms": 100_500, "content": "second"},
            ])
            msgs = InstagramAdapter().parse(d, self_name=SELF)
        self.assertEqual([m.text for m in msgs], ["first", "second"])

    def test_mojibake_repaired_in_text_and_sender(self):
        with tempfile.TemporaryDirectory() as d:
            _write_thread(d, "inbox", "t", [
                _msg(_mojibake("Zoë"), 100, _mojibake("ça va? 😊")),
                _msg(SELF, 110, "oui"),
            ], participants=(_mojibake("Zoë"), SELF))
            msgs = InstagramAdapter().parse(d, self_name=SELF)
        self.assertEqual(msgs[0].sender_id, "Zoë")
        self.assertEqual(msgs[0].text, "ça va? 😊")

    def test_non_text_events_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            _write_thread(d, "inbox", "t", [
                _msg("Alice", 100, "real message"),
                _msg("Alice", 101, "Liked a message"),
                _msg("Alice", 102, "Reacted ❤ to your message"),
                _msg("Alice", 103, "Alice sent an attachment."),
                _msg(SELF, 104, "You sent an attachment."),
                _msg("Alice", 105, "Alice shared a story."),
                _msg("Alice", 106),                      # media only, no content
                _msg("Alice", 107, "oops", is_unsent=True),
                _msg(SELF, 108, "real reply"),
            ])
            msgs = InstagramAdapter().parse(d, self_name=SELF)
        self.assertEqual([m.text for m in msgs], ["real message", "real reply"])

    def test_oldest_first_file_still_chronological(self):
        # Some hand-made / third-party files are already oldest-first.
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "message_1.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"participants": [{"name": "Alice"}, {"name": SELF}], "messages": [
                    _msg("Alice", 100, "first"), _msg(SELF, 110, "second")]}, f)
            msgs = InstagramAdapter().parse(path, self_name=SELF)
        self.assertEqual([m.text for m in msgs], ["first", "second"])

    def test_single_file_with_nonstandard_name(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "messages.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"participants": [], "messages": [_msg("Alice", 100, "hi")]}, f)
            msgs = InstagramAdapter().parse(path, self_name=SELF)
        self.assertEqual([m.text for m in msgs], ["hi"])

    def test_real_export_shapes(self):
        # Shapes seen in public sample exports: null content, trailing space on
        # reaction notices, mojibake'd emoji, typed Call/Share entries.
        with tempfile.TemporaryDirectory() as d:
            _write_thread(d, "inbox", "t", [
                _msg("Alice", 100, "hello", is_geoblocked_for_viewer=False),
                _msg("Alice", 101, None, photos=[{"uri": "photos/1.jpg"}]) | {"content": None},
                _msg("Alice", 102, _mojibake("Reacted 👍 to your message ")),
                _msg("Alice", 103, "Should we call?", type="Call", call_duration=60),
                _msg("Alice", 104, "The video chat ended."),
                _msg("Alice", 105, "Alice missed your call."),
                _msg(SELF, 106, None, type="Share", share={"link": "https://x"}),
                _msg(SELF, 107, _mojibake("👍")),
            ])
            msgs = InstagramAdapter().parse(d, self_name=SELF)
        self.assertEqual([m.text for m in msgs], ["hello", "👍"])

    def test_requests_and_filtered_threads_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            _write_thread(d, "inbox", "friend", [_msg("Alice", 100, "keep")])
            _write_thread(d, "message_requests", "stranger", [_msg("Spam", 100, "drop")],
                          participants=("Spam", SELF))
            _write_thread(d, "filtered_threads", "spam", [_msg("Spam", 100, "drop")],
                          participants=("Spam", SELF))
            msgs = InstagramAdapter().parse(d, self_name=SELF)
        self.assertEqual([m.text for m in msgs], ["keep"])

    def test_single_file_input(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_thread(d, "inbox", "alice_123", [
                _msg("Alice", 100, "hey"), _msg(SELF, 110, "hi")])
            msgs = InstagramAdapter().parse(path, self_name=SELF)
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0].chat_id, "alice_123")

    def test_no_threads_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                InstagramAdapter().parse(d, self_name=SELF)

    def test_missing_path_raises(self):
        with self.assertRaises(FileNotFoundError):
            InstagramAdapter().parse("/nonexistent/instagram-export", self_name=SELF)


class SelfDetectionTest(unittest.TestCase):
    def test_from_personal_information(self):
        with tempfile.TemporaryDirectory() as d:
            _write_profile(d, SELF)
            _write_thread(d, "inbox", "t", [_msg("Alice", 100, "hey"), _msg(SELF, 110, "hi")])
            msgs = InstagramAdapter().parse(d)
        self.assertEqual([m.sender_is_self for m in msgs], [False, True])

    def test_from_common_participant(self):
        with tempfile.TemporaryDirectory() as d:
            _write_thread(d, "inbox", "a", [_msg("Alice", 100, "hey"), _msg(SELF, 110, "hi")],
                          participants=("Alice", SELF))
            _write_thread(d, "inbox", "b", [_msg("Bob", 100, "yo"), _msg(SELF, 110, "sup")],
                          participants=("Bob", SELF))
            msgs = InstagramAdapter().parse(d)
        self.assertEqual({m.sender_id for m in msgs if m.sender_is_self}, {SELF})

    def test_ambiguous_raises_listing_senders(self):
        with tempfile.TemporaryDirectory() as d:
            _write_thread(d, "inbox", "a", [_msg("Alice", 100, "hey"), _msg(SELF, 110, "hi")])
            with self.assertRaises(ValueError) as ctx:
                InstagramAdapter().parse(d)
        self.assertIn("--self-name", str(ctx.exception))
        self.assertIn("'Alice'", str(ctx.exception))

    def test_explicit_self_name_wins(self):
        with tempfile.TemporaryDirectory() as d:
            _write_profile(d, "Someone Else")
            _write_thread(d, "inbox", "t", [_msg("Alice", 100, "hey"), _msg(SELF, 110, "hi")])
            msgs = InstagramAdapter().parse(d, self_name=SELF)
        self.assertEqual([m.sender_is_self for m in msgs], [False, True])


class RegistryAndCliTest(unittest.TestCase):
    def test_registered(self):
        self.assertIn("instagram", available_sources())
        self.assertEqual(get_adapter("instagram").name, "instagram")

    def test_end_to_end(self):
        from ingest.cli import main
        os.environ["LLM_VALIDATE"] = "false"
        with tempfile.TemporaryDirectory() as d:
            _write_thread(d, "inbox", "alice_123", [
                _msg("Alice", 100, "are you coming saturday?"),
                _msg(SELF, 110, "yeah for sure"),
            ])
            out = os.path.join(d, "out.json")
            rc = main(["--source", "instagram", "--input", d, "--output", out,
                       "--self-name", SELF, "--skip-redact-scan"])
            self.assertEqual(rc, 0)
            with open(out, encoding="utf-8") as f:
                data = json.load(f)
        self.assertEqual(len(data), 1)
        blob = json.dumps(data)
        self.assertIn("are you coming saturday?", blob)
        self.assertIn("yeah for sure", blob)


if __name__ == "__main__":
    unittest.main()
