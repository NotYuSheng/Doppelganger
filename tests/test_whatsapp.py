"""Unit tests for the WhatsApp export adapter (stdlib unittest, no network).

Run from the repo root:

    python -m unittest tests.test_whatsapp

These lock in the messy bits of WhatsApp's plain-text export: the iOS vs Android
prefix shapes, locale timestamp variants, multi-line message reconstruction,
media/omitted placeholders, system-notice skipping, and the fact that the owner
must be given explicitly (WhatsApp never marks "you").
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingest.adapters.whatsapp import WhatsAppAdapter, _parse_timestamp

SELF = "Yu Sheng"


def _parse(text, **kw):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "WhatsApp Chat with Alice.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return WhatsAppAdapter().parse(path, **kw)


ANDROID = """\
12/03/2024, 14:32 - Alice: hey there
12/03/2024, 14:33 - Alice: you around?
12/03/2024, 14:35 - Yu Sheng: yeah whats up
"""

IOS = """\
[12/03/2024, 14:32:11] Alice: hey there
[12/03/2024, 14:33:02] Yu Sheng: yeah whats up
"""


class FormatTest(unittest.TestCase):
    def test_android_format(self):
        msgs = _parse(ANDROID, self_name=SELF)
        self.assertEqual(len(msgs), 3)
        self.assertEqual(msgs[0].sender_id, "Alice")
        self.assertEqual(msgs[0].text, "hey there")
        self.assertFalse(msgs[0].sender_is_self)
        self.assertTrue(msgs[2].sender_is_self)

    def test_ios_bracketed_format(self):
        msgs = _parse(IOS, self_name=SELF)
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0].text, "hey there")
        self.assertTrue(msgs[1].sender_is_self)

    def test_no_ids_or_reply_metadata(self):
        # WhatsApp has neither; the pipeline relies on the documented fallback.
        for m in _parse(ANDROID, self_name=SELF):
            self.assertIsNone(m.message_id)
            self.assertIsNone(m.reply_to_id)

    def test_chat_id_from_filename(self):
        msgs = _parse(ANDROID, self_name=SELF)
        self.assertEqual(msgs[0].chat_id, "WhatsApp Chat with Alice")


class MultiLineTest(unittest.TestCase):
    def test_continuation_lines_are_joined(self):
        text = (
            "12/03/2024, 14:32 - Alice: line one\n"
            "line two\n"
            "line three\n"
            "12/03/2024, 14:33 - Yu Sheng: ok\n"
        )
        msgs = _parse(text, self_name=SELF)
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0].text, "line one\nline two\nline three")

    def test_colon_in_body_is_not_a_sender_split(self):
        # A body containing "word: more" must stay in the text, not be re-parsed.
        text = "12/03/2024, 14:32 - Alice: note: buy milk\n"
        msgs = _parse(text, self_name=SELF)
        self.assertEqual(msgs[0].text, "note: buy milk")


class PlaceholderAndSystemTest(unittest.TestCase):
    def test_media_omitted_is_dropped(self):
        text = (
            "12/03/2024, 14:32 - Alice: <Media omitted>\n"
            "12/03/2024, 14:33 - Alice: real text\n"
        )
        msgs = _parse(text, self_name=SELF)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].text, "real text")

    def test_system_notice_without_sender_is_skipped(self):
        text = (
            "12/03/2024, 14:30 - Messages and calls are end-to-end encrypted.\n"
            "12/03/2024, 14:32 - Alice: hi\n"
        )
        msgs = _parse(text, self_name=SELF)
        # The encryption notice has a timestamp but no "Sender:" -> not a message.
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].sender_id, "Alice")

    def test_deleted_message_is_dropped(self):
        text = "12/03/2024, 14:32 - Alice: This message was deleted\n"
        self.assertEqual(_parse(text, self_name=SELF), [])


class TimestampTest(unittest.TestCase):
    def _ts(self, raw):
        return _parse_timestamp(raw)

    def test_dmy_24h(self):
        got = self._ts("12/03/2024, 14:32:11")
        self.assertEqual(got, int(datetime(2024, 3, 12, 14, 32, 11).timestamp()))

    def test_dmy_no_seconds(self):
        got = self._ts("12/03/2024, 14:32")
        self.assertEqual(got, int(datetime(2024, 3, 12, 14, 32).timestamp()))

    def test_12h_am_pm(self):
        got = self._ts("12/03/2024, 2:32:11 PM")
        self.assertEqual(got, int(datetime(2024, 3, 12, 14, 32, 11).timestamp()))

    def test_dotted_am_pm(self):
        got = self._ts("12/03/2024, 2:32 p.m.")
        self.assertEqual(got, int(datetime(2024, 3, 12, 14, 32).timestamp()))

    def test_dash_date_separator(self):
        got = self._ts("12-03-2024, 14:32")
        self.assertEqual(got, int(datetime(2024, 3, 12, 14, 32).timestamp()))

    def test_dot_time_separator(self):
        got = self._ts("12/03/2024, 14.32")
        self.assertEqual(got, int(datetime(2024, 3, 12, 14, 32).timestamp()))

    def test_two_digit_year(self):
        got = self._ts("12/03/24, 14:32")
        self.assertEqual(got, int(datetime(2024, 3, 12, 14, 32).timestamp()))

    def test_unparseable_returns_none(self):
        self.assertIsNone(self._ts("not a date"))


class SelfNameTest(unittest.TestCase):
    def test_missing_self_name_raises(self):
        with self.assertRaises(ValueError):
            _parse(ANDROID)  # no self_name

    def test_self_name_selects_owner(self):
        msgs = _parse(ANDROID, self_name="Alice")
        alice = [m for m in msgs if m.sender_id == "Alice"]
        self.assertTrue(all(m.sender_is_self for m in alice))

    def test_unknown_self_name_warns_but_parses(self):
        # A typo'd name is a warning, not a crash — every turn is "not you".
        msgs = _parse(ANDROID, self_name="Nobody")
        self.assertTrue(all(not m.sender_is_self for m in msgs))


if __name__ == "__main__":
    unittest.main()
