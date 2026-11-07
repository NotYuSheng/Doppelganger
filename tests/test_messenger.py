"""Unit tests for the Facebook Messenger adapter (stdlib unittest, no network).

Run from the repo root:

    python -m unittest tests.test_messenger

Shared Meta behaviour (mojibake, ordering, event filtering) is covered in
tests/test_instagram.py. These lock in what's Messenger-specific: the
end-to-end encrypted "secure storage" shape, both downloads unzipped into one
folder, de-duplicating their overlap, and Facebook's profile file.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingest.adapters import available_sources, get_adapter
from ingest.adapters.messenger import MessengerAdapter

SELF = "Yu Sheng"


def _mojibake(s: str) -> str:
    return s.encode("utf-8").decode("latin-1")


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return path


def _dyi_thread(root, box, thread, messages, participants=("Alice", SELF)):
    """DYI thread; ``messages`` are (sender, seconds, content) oldest-first."""
    return _write(
        os.path.join(root, "your_facebook_activity", "messages", box, thread, "message_1.json"),
        {
            "participants": [{"name": p} for p in participants],
            "messages": [
                {"sender_name": s, "timestamp_ms": t * 1000, "content": c, "type": "Generic"}
                for s, t, c in reversed(messages)
            ],
            "title": participants[0],
            "thread_path": f"{box}/{thread}",
        },
    )


def _e2ee_thread(root, name, messages, participants=(SELF, "Alice"), **extra):
    """Secure-storage thread; ``messages`` are dicts or (sender, seconds, text)."""
    msgs = []
    for m in messages:
        if isinstance(m, tuple):
            s, t, text = m
            m = {"senderName": s, "text": text, "timestamp": t * 1000, "type": "Generic",
                 "isUnsent": False, "media": [], "reactions": []}
        msgs.append(m)
    return _write(
        os.path.join(root, "your_activity_across_facebook", "messages", name + ".json"),
        {"participants": list(participants), "threadName": participants[-1], "messages": msgs, **extra},
    )


class SecureStorageTest(unittest.TestCase):
    def test_e2ee_thread_parsed(self):
        with tempfile.TemporaryDirectory() as d:
            _e2ee_thread(d, "alice_1", [
                ("Alice", 100, "hey"),
                ("Yu Sheng", 110, "hi!"),
            ])
            msgs = MessengerAdapter().parse(d, self_name=SELF)
        self.assertEqual([(m.text, m.sender_is_self) for m in msgs], [("hey", False), ("hi!", True)])
        self.assertEqual(msgs[0].timestamp, 100)
        self.assertEqual(msgs[0].chat_id, "alice_1")

    def test_e2ee_mojibake_unsent_and_media_only(self):
        with tempfile.TemporaryDirectory() as d:
            _e2ee_thread(d, "alice_1", [
                ("Alice", 100, _mojibake("café time? ☕")),
                {"senderName": SELF, "text": "oops", "timestamp": 105_000, "type": "Generic",
                 "isUnsent": True, "media": [], "reactions": []},
                {"senderName": "Alice", "text": "", "timestamp": 106_000, "type": "Generic",
                 "isUnsent": False, "media": [{"uri": "./media/photo.jpg"}], "reactions": []},
                (SELF, 110, "sure"),
            ])
            msgs = MessengerAdapter().parse(d, self_name=SELF)
        self.assertEqual([m.text for m in msgs], ["café time? ☕", "sure"])

    def test_e2ee_oldest_first_kept_in_order(self):
        with tempfile.TemporaryDirectory() as d:
            _e2ee_thread(d, "a_1", [("Alice", 100, "one"), (SELF, 200, "two"), ("Alice", 300, "three")])
            msgs = MessengerAdapter().parse(d, self_name=SELF)
        self.assertEqual([m.text for m in msgs], ["one", "two", "three"])

    def test_single_e2ee_file_input(self):
        with tempfile.TemporaryDirectory() as d:
            path = _e2ee_thread(d, "alice_1", [("Alice", 100, "hey"), (SELF, 110, "hi")])
            msgs = MessengerAdapter().parse(path, self_name=SELF)
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0].chat_id, "alice_1")

    def test_unrelated_json_ignored(self):
        # Settings/profile JSON in the export must not be mistaken for threads.
        with tempfile.TemporaryDirectory() as d:
            _e2ee_thread(d, "alice_1", [("Alice", 100, "hey")])
            _write(os.path.join(d, "your_activity_across_facebook", "messages", "messaging_settings.json"), {"settings": []})
            _write(os.path.join(d, "your_activity_across_facebook", "messages", "list.json"), [1, 2, 3])
            msgs = MessengerAdapter().parse(d, self_name=SELF)
        self.assertEqual([m.chat_id for m in msgs], ["alice_1"])


class BothDownloadsTest(unittest.TestCase):
    def test_dyi_and_e2ee_in_one_folder(self):
        with tempfile.TemporaryDirectory() as d:
            _dyi_thread(d, "inbox", "bob_123", [("Bob", 100, "old chat"), (SELF, 110, "yep")],
                        participants=("Bob", SELF))
            _dyi_thread(d, "archived_threads", "carol_9", [("Carol", 50, "archived")],
                        participants=("Carol", SELF))
            _e2ee_thread(d, "alice_1", [("Alice", 200, "new chat"), (SELF, 210, "hello")])
            msgs = MessengerAdapter().parse(d, self_name=SELF)
        self.assertEqual({m.chat_id for m in msgs}, {"bob_123", "carol_9", "alice_1"})
        self.assertEqual(len(msgs), 5)

    def test_cutover_overlap_deduplicated(self):
        # A chat that moved to encryption can be in DYI's e2ee_cutover/ AND in
        # the secure-storage download.
        with tempfile.TemporaryDirectory() as d:
            _dyi_thread(d, "e2ee_cutover", "alice_123", [
                ("Alice", 100, "before switch"), (SELF, 110, "same msg")])
            _e2ee_thread(d, "alice_1", [(SELF, 110, "same msg"), ("Alice", 200, "after switch")])
            msgs = MessengerAdapter().parse(d, self_name=SELF)
        self.assertEqual(sorted(m.text for m in msgs), ["after switch", "before switch", "same msg"])

    def test_requests_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            _dyi_thread(d, "inbox", "bob_1", [("Bob", 100, "keep")], participants=("Bob", SELF))
            _dyi_thread(d, "message_requests", "spam_1", [("Spam", 100, "drop")],
                        participants=("Spam", SELF))
            msgs = MessengerAdapter().parse(d, self_name=SELF)
        self.assertEqual([m.text for m in msgs], ["keep"])

    def test_dyi_non_text_types_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            path = _dyi_thread(d, "inbox", "sam_1", [], participants=("Sam", SELF))
            _write(path, {"participants": [{"name": "Sam"}, {"name": SELF}], "messages": [
                {"sender_name": SELF, "timestamp_ms": 600_000, "content": "real one", "type": "Generic"},
                {"sender_name": SELF, "timestamp_ms": 500_000, "type": "Generic",
                 "sticker": {"uri": "stickers_used/1.png"}},
                {"sender_name": "Sam", "timestamp_ms": 400_000, "type": "Call", "call_duration": 0,
                 "missed": True},
                {"sender_name": SELF, "timestamp_ms": 300_000, "type": "Share",
                 "share": {"link": "https://example.com"}},
                {"sender_name": "Sam", "timestamp_ms": 200_000, "content": "Sam left the chat",
                 "type": "Unsubscribe"},
            ]})
            msgs = MessengerAdapter().parse(d, self_name=SELF)
        self.assertEqual([m.text for m in msgs], ["real one"])


class ReviewRegressionTest(unittest.TestCase):
    def test_same_thread_in_inbox_and_cutover_combined(self):
        # Same folder name under two boxes must not overwrite each other.
        with tempfile.TemporaryDirectory() as d:
            _dyi_thread(d, "e2ee_cutover", "alice_123", [("Alice", 100, "before switch")])
            _dyi_thread(d, "inbox", "alice_123", [("Alice", 200, "in inbox"), (SELF, 210, "reply")])
            msgs = MessengerAdapter().parse(d, self_name=SELF)
        self.assertEqual([m.text for m in msgs], ["before switch", "in inbox", "reply"])

    def test_real_messages_resembling_notices_kept(self):
        with tempfile.TemporaryDirectory() as d:
            _e2ee_thread(d, "alice_1", [
                (SELF, 100, "You called it!"),
                ("Alice", 101, "I can't believe he called you"),
                (SELF, 102, "I left the chat."),
                ("Alice", 103, "she sent a photo."),
                ("Alice", 104, "Alice sent a photo."),       # real notice
                (SELF, 105, "You called Alice."),            # real notice
                ("Alice", 106, "Alice left the group."),     # real notice
            ])
            msgs = MessengerAdapter().parse(d, self_name=SELF)
        self.assertEqual([m.text for m in msgs], [
            "You called it!", "I can't believe he called you", "I left the chat.", "she sent a photo."])

    def test_flat_secure_storage_folder_as_input(self):
        with tempfile.TemporaryDirectory() as d:
            _write(os.path.join(d, "alice_1.json"), {"participants": [SELF, "Alice"], "messages": [
                {"senderName": "Alice", "text": "hi", "timestamp": 100_000}]})
            msgs = MessengerAdapter().parse(d, self_name=SELF)
        self.assertEqual([m.text for m in msgs], ["hi"])

    def test_json_outside_message_folders_not_probed(self):
        # A thread-shaped JSON in an unrelated folder (e.g. some other activity
        # data) isn't loaded just to check its shape.
        with tempfile.TemporaryDirectory() as d:
            _dyi_thread(d, "inbox", "bob_1", [("Bob", 100, "keep")], participants=("Bob", SELF))
            _write(os.path.join(d, "your_facebook_activity", "posts", "x.json"),
                   {"participants": ["A"], "messages": [{"senderName": "A", "text": "no", "timestamp": 1}]})
            msgs = MessengerAdapter().parse(d, self_name=SELF)
        self.assertEqual([m.text for m in msgs], ["keep"])


class RobustnessTest(unittest.TestCase):
    def test_corrupt_file_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as d:
            _dyi_thread(d, "inbox", "good_1", [("Bob", 100, "still here")], participants=("Bob", SELF))
            bad = os.path.join(d, "your_facebook_activity", "messages", "inbox", "bad_1", "message_1.json")
            os.makedirs(os.path.dirname(bad))
            with open(bad, "w", encoding="utf-8") as f:
                f.write("{this is not: valid json")
            msgs = MessengerAdapter().parse(d, self_name=SELF)
        self.assertEqual([m.text for m in msgs], ["still here"])

    def test_part_files_sorted_numerically(self):
        # message_10.json must not sort between message_1 and message_2.
        with tempfile.TemporaryDirectory() as d:
            base = os.path.join(d, "your_facebook_activity", "messages", "inbox", "dave_1")
            for part, (t, text) in {1: (300, "c"), 2: (200, "b"), 10: (100, "a")}.items():
                _write(os.path.join(base, f"message_{part}.json"), {
                    "participants": [{"name": "Dave"}, {"name": SELF}],
                    "messages": [{"sender_name": "Dave", "timestamp_ms": t * 1000, "content": text}]})
            msgs = MessengerAdapter().parse(d, self_name=SELF)
        self.assertEqual([m.text for m in msgs], ["a", "b", "c"])


class SelfDetectionTest(unittest.TestCase):
    def test_from_facebook_profile(self):
        with tempfile.TemporaryDirectory() as d:
            _write(os.path.join(d, "personal_information", "profile_information",
                                "profile_information.json"),
                   {"profile_v2": {"name": {"full_name": _mojibake(SELF), "first_name": "Yu"}}})
            _dyi_thread(d, "inbox", "a_1", [("Alice", 100, "hey"), (SELF, 110, "hi")])
            msgs = MessengerAdapter().parse(d)
        self.assertEqual([m.sender_is_self for m in msgs], [False, True])

    def test_common_participant_across_both_shapes(self):
        with tempfile.TemporaryDirectory() as d:
            _dyi_thread(d, "inbox", "bob_1", [("Bob", 100, "yo"), (SELF, 110, "sup")],
                        participants=("Bob", SELF))
            _e2ee_thread(d, "alice_1", [("Alice", 100, "hey"), (SELF, 110, "hi")])
            msgs = MessengerAdapter().parse(d)
        self.assertEqual({m.sender_id for m in msgs if m.sender_is_self}, {SELF})


class RegistryAndCliTest(unittest.TestCase):
    def test_registered(self):
        self.assertIn("messenger", available_sources())
        self.assertEqual(get_adapter("messenger").name, "messenger")

    def test_end_to_end(self):
        from ingest.cli import main
        os.environ["LLM_VALIDATE"] = "false"
        with tempfile.TemporaryDirectory() as d:
            _e2ee_thread(d, "alice_1", [("Alice", 100, "dinner tonight?"), (SELF, 110, "yes please")])
            out = os.path.join(d, "out.json")
            rc = main(["--source", "messenger", "--input", d, "--output", out,
                       "--self-name", SELF, "--skip-redact-scan"])
            self.assertEqual(rc, 0)
            with open(out, encoding="utf-8") as f:
                blob = json.dumps(json.load(f))
        self.assertIn("dinner tonight?", blob)
        self.assertIn("yes please", blob)


if __name__ == "__main__":
    unittest.main()
