"""Unit tests for the Signal export adapter (stdlib unittest, no network).

Run from the repo root:

    python -m unittest tests.test_signal

Fixtures are synthetic main.jsonl files following Signal's Backup.proto in the
Protobuf JSON mapping (as written by libsignal's BackupJsonExporter).
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingest.adapters import available_sources, get_adapter
from ingest.adapters.signal import SignalAdapter

ACI_CHEWIE = "CujGyCcHTqeyJHQXIn04kA=="


def _frames():
    return [
        {"version": "1", "backupTimeMs": "1715636551000"},
        {"account": {"givenName": "Yu", "familyName": "Sheng", "username": "ys.01"}},
        {"recipient": {"id": "1", "self": {}}},
        {"recipient": {"id": "2", "releaseNotes": {}}},
        {"recipient": {"id": "4", "contact": {"profileGivenName": "Han", "profileFamilyName": "Solo"}}},
        {"recipient": {"id": "5", "contact": {"aci": ACI_CHEWIE, "profileGivenName": "Chew",
                                              "systemGivenName": "Chewie", "registered": {}}}},
        {"recipient": {"id": 6, "group": {"snapshot": {"title": {"title": "Falcon Crew"}}}}},
        {"chat": {"id": "1", "recipientId": "4"}},
        {"chat": {"id": "2", "recipientId": 6}},
    ]


def _item(chat, author, sent_ms, body=None, outgoing=False, **extra):
    item = {"chatId": str(chat), "authorId": str(author), "dateSent": str(sent_ms)}
    item["outgoing" if outgoing else "incoming"] = {}
    if body is not None:
        item["standardMessage"] = {"text": {"body": body}}
    item.update(extra)
    return {"chatItem": item}


def _write(d, frames):
    folder = os.path.join(d, "signal-export-2026-11-07-12-00-00")
    os.makedirs(folder)
    with open(os.path.join(folder, "metadata.json"), "w") as f:
        json.dump({"version": 1}, f)
    with open(os.path.join(folder, "main.jsonl"), "w", encoding="utf-8") as f:
        for fr in frames:
            f.write(json.dumps(fr, ensure_ascii=False) + "\n")
    return folder


def _parse(frames, **kw):
    with tempfile.TemporaryDirectory() as d:
        return SignalAdapter().parse(_write(d, frames), **kw)


class ParseTest(unittest.TestCase):
    def test_one_to_one_chat(self):
        msgs = _parse(_frames() + [
            _item(1, 4, 100_000, "hey"),
            _item(1, 1, 110_000, "hi han", outgoing=True),
        ])
        self.assertEqual([(m.sender_id, m.sender_is_self, m.text) for m in msgs],
                         [("Han Solo", False, "hey"), ("Yu Sheng", True, "hi han")])
        self.assertEqual(msgs[0].timestamp, 100)
        self.assertEqual(msgs[0].chat_id, "Han Solo (1)")
        self.assertEqual(msgs[0].message_id, "4:100000")

    def test_group_chat_and_contact_name_priority(self):
        msgs = _parse(_frames() + [_item(2, 5, 100_000, "rrwwgg")])
        self.assertEqual(msgs[0].chat_id, "Falcon Crew (2)")
        self.assertEqual(msgs[0].sender_id, "Chewie")  # system name beats profile name

    def test_quote_sets_reply_to(self):
        quoted = _item(1, 1, 200_000, "it's a trap", outgoing=True)
        quoted["chatItem"]["standardMessage"]["quote"] = {
            "targetSentTimestamp": "100000", "authorId": "4", "text": {"body": "hey"}}
        msgs = _parse(_frames() + [_item(1, 4, 100_000, "hey"), quoted])
        self.assertEqual(msgs[1].reply_to_id, msgs[0].message_id)
        self.assertIsNone(msgs[0].reply_to_id)

    def test_mentions_resolved(self):
        item = _item(2, 4, 100_000, "ask ￼ about it")
        item["chatItem"]["standardMessage"]["text"]["bodyRanges"] = [
            {"start": 4, "length": 1, "mentionAci": ACI_CHEWIE},
            {"start": 0, "length": 3, "style": "BOLD"}]
        msgs = _parse(_frames() + [item])
        self.assertEqual(msgs[0].text, "ask @Chewie about it")

    def test_mention_offsets_are_utf16(self):
        # "😀" is 2 UTF-16 units, so the mention after "😀 " starts at 3, not 2.
        item = _item(2, 4, 100_000, "😀 \ufffc hi")
        item["chatItem"]["standardMessage"]["text"]["bodyRanges"] = [
            {"start": 3, "length": 1, "mentionAci": ACI_CHEWIE}]
        msgs = _parse(_frames() + [item])
        self.assertEqual(msgs[0].text, "😀 @Chewie hi")

    def test_non_text_items_skipped(self):
        msgs = _parse(_frames() + [
            _item(1, 4, 100_000, "real"),
            {"chatItem": {"chatId": "1", "authorId": "4", "dateSent": "101000", "directionless": {},
                          "updateMessage": {"simpleUpdate": {"type": "JOINED_SIGNAL"}}}},
            {"chatItem": {"chatId": "1", "authorId": "4", "dateSent": "102000", "incoming": {},
                          "stickerMessage": {"sticker": {"emoji": "👍"}}}},
            {"chatItem": {"chatId": "1", "authorId": "4", "dateSent": "103000", "incoming": {},
                          "remoteDeletedMessage": {}}},
            {"chatItem": {"chatId": "1", "authorId": "4", "dateSent": "104000", "incoming": {},
                          "standardMessage": {"attachments": [{"pointer": {}}]}}},
            _item(1, 1, 105_000, "   ", outgoing=True),
        ])
        self.assertEqual([m.text for m in msgs], ["real"])

    def test_edited_message_uses_latest_text(self):
        item = _item(1, 4, 100_000, "final version")
        item["chatItem"]["revisions"] = [
            {"chatId": "1", "authorId": "4", "dateSent": "90000", "incoming": {},
             "standardMessage": {"text": {"body": "first draft"}}}]
        msgs = _parse(_frames() + [item])
        self.assertEqual([m.text for m in msgs], ["final version"])

    def test_numbers_as_ints_or_strings(self):
        frames = _frames() + [{"chatItem": {"chatId": 1, "authorId": 4, "dateSent": 100000,
                                            "incoming": {}, "standardMessage": {"text": {"body": "x"}}}}]
        self.assertEqual(len(_parse(frames)), 1)

    def test_unicode_and_bad_lines(self):
        with tempfile.TemporaryDirectory() as d:
            folder = _write(d, _frames() + [_item(1, 4, 100_000, "你好 👋")])
            with open(os.path.join(folder, "main.jsonl"), "a", encoding="utf-8") as f:
                f.write("{not json\n")
            msgs = SignalAdapter().parse(folder)
        self.assertEqual([m.text for m in msgs], ["你好 👋"])


class InputTest(unittest.TestCase):
    def test_accepts_parent_folder_and_file(self):
        with tempfile.TemporaryDirectory() as d:
            folder = _write(d, _frames() + [_item(1, 4, 100_000, "hey")])
            self.assertEqual(len(SignalAdapter().parse(d)), 1)
            self.assertEqual(len(SignalAdapter().parse(os.path.join(folder, "main.jsonl"))), 1)

    def test_missing_export_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileNotFoundError):
                SignalAdapter().parse(d)

    def test_not_a_signal_file_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                SignalAdapter().parse(_write(d, [{"foo": 1}]))

    def test_self_name_overrides_display_name_only(self):
        msgs = _parse(_frames() + [_item(1, 1, 100_000, "hi", outgoing=True)], self_name="YS")
        self.assertEqual((msgs[0].sender_id, msgs[0].sender_is_self), ("YS", True))


class RegistryAndCliTest(unittest.TestCase):
    def test_registered(self):
        self.assertIn("signal", available_sources())
        self.assertEqual(get_adapter("signal").name, "signal")

    def test_end_to_end(self):
        from ingest.cli import main
        os.environ["LLM_VALIDATE"] = "false"
        with tempfile.TemporaryDirectory() as d:
            folder = _write(d, _frames() + [
                _item(1, 4, 100_000, "dinner at 8?"),
                _item(1, 1, 110_000, "see you there", outgoing=True)])
            out = os.path.join(d, "out.json")
            rc = main(["--source", "signal", "--input", folder, "--output", out, "--skip-redact-scan"])
            self.assertEqual(rc, 0)
            with open(out, encoding="utf-8") as f:
                blob = json.dumps(json.load(f))
        self.assertIn("dinner at 8?", blob)
        self.assertIn("see you there", blob)


if __name__ == "__main__":
    unittest.main()
