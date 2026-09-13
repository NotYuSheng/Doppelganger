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


class RegressionTest(unittest.TestCase):
    """Findings from the code review on the WhatsApp adapter branch."""

    def test_continuation_line_starting_with_date_is_kept(self):
        # A wrapped body line whose text starts with a date/time must NOT be
        # dropped: it lacks the " - "/"] " prefix separator, so it's a
        # continuation, not a system notice. (Same silent-drop class as #42.)
        text = (
            "12/03/2024, 14:32 - Alice: my notes\n"
            "13/04/2025, 09:00 standup recap\n"
            "end line\n"
            "12/03/2024, 14:33 - Yu Sheng: ok\n"
        )
        msgs = _parse(text, self_name=SELF)
        self.assertEqual(len(msgs), 2)
        self.assertEqual(
            msgs[0].text, "my notes\n13/04/2025, 09:00 standup recap\nend line"
        )

    def test_no_space_after_comma(self):
        # Some locales emit "DD/MM/YYYY,HH:MM" with no space after the comma.
        text = (
            "12/03/2024,14:32 - Alice: hi\n"
            "12/03/2024,14:33 - Yu Sheng: yo\n"
        )
        msgs = _parse(text, self_name=SELF)
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0].text, "hi")

    def test_literal_null_message_is_kept(self):
        # A real one-word message "null" must not be treated as a placeholder.
        text = "12/03/2024, 14:32 - Alice: null\n"
        msgs = _parse(text, self_name=SELF)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].text, "null")

    def test_invisible_marks_around_placeholder_are_stripped(self):
        # WhatsApp wraps placeholders in LRM/RLM marks; they must still be
        # recognized and dropped. (LRM=U+200E, RLM=U+200F built explicitly so
        # the marks survive editing.)
        lrm, rlm = "‎", "‏"
        text = f"12/03/2024, 14:32 - Alice: {lrm}<Media omitted>{rlm}\n"
        self.assertEqual(_parse(text, self_name=SELF), [])


class DateOrderTest(unittest.TestCase):
    def test_us_format_detected_month_first(self):
        # A date with second field > 12 forces month/day for the whole file, so
        # "03/12/2024" is Dec 3, not March 12.
        text = (
            "03/12/2024, 09:00 - Alice: hi\n"       # ambiguous alone
            "03/25/2024, 09:00 - Alice: later\n"    # 25 > 12 -> month-first
            "03/12/2024, 09:01 - Yu Sheng: yo\n"
        )
        msgs = _parse(text, self_name=SELF)
        first = [m for m in msgs if m.sender_id == "Alice"][0]
        self.assertEqual(
            first.timestamp, int(datetime(2024, 3, 12, 9, 0).timestamp())
        )

    def test_dmy_detected_day_first(self):
        # First field > 12 forces day/month.
        text = (
            "25/03/2024, 09:00 - Alice: hi\n"       # 25 > 12 -> day-first
            "05/03/2024, 09:01 - Yu Sheng: yo\n"
        )
        msgs = _parse(text, self_name=SELF)
        yo = [m for m in msgs if m.sender_id == "Yu Sheng"][0]
        # 05/03 under day-first is 5 March.
        self.assertEqual(
            yo.timestamp, int(datetime(2024, 3, 5, 9, 1).timestamp())
        )


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
