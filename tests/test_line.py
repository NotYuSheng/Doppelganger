"""Unit tests for the LINE export adapter (stdlib unittest, no network).

Run from the repo root:

    python -m unittest tests.test_line

Fixtures are synthetic, modelled on the English and Japanese layouts seen in
public samples: header + "Saved on" line, date lines, tab-separated messages,
quoted multi-line messages, empty-sender system notices, placeholders.
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingest.adapters import available_sources, get_adapter
from ingest.adapters.line import LineAdapter

SELF = "Yu Sheng"


def _parse(text, name="[LINE] Chat history with Alice.txt", newline="\n", **kw):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, name)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text.replace("\n", newline))
        return LineAdapter().parse(path, **kw)


def _ts(y, mo, d, h, mi):
    return int(datetime(y, mo, d, h, mi).timestamp())


ENGLISH = """\
[LINE] Chat history with Alice
Saved on: 2026/03/08, 22:10

2026/01/25 Sun
07:53\tAlice\tmorning!
07:54\tAlice\t[Sticker]
08:10\tYu Sheng\t"good morning
how did you sleep?"
08:12\tAlice\tok
08:13\t\tAlice unsent a message.

2026/01/26 Mon
20:07\tYu Sheng\tdinner?
20:08\tAlice\t[Photo]
20:09\tAlice\t☎ Call time 0:42
"""

JAPANESE = """\
[LINE] Aliceとのトーク履歴
保存日時：2022/11/10 01:11

2018/07/10(火)
19:42\tAlice\tこんにちは
19:43\tYu Sheng\t元気？
19:44\tAlice\t[スタンプ]
19:45\tAlice\tメッセージの送信を取り消しました

2018/07/12(木)
23:55\tYu Sheng\tおやすみ
"""


class EnglishFormatTest(unittest.TestCase):
    def test_messages_and_timestamps(self):
        msgs = _parse(ENGLISH, self_name=SELF)
        self.assertEqual([m.text for m in msgs],
                         ["morning!", "good morning\nhow did you sleep?", "ok", "dinner?"])
        self.assertEqual(msgs[0].timestamp, _ts(2026, 1, 25, 7, 53))
        self.assertEqual(msgs[3].timestamp, _ts(2026, 1, 26, 20, 7))
        self.assertEqual([m.sender_is_self for m in msgs], [False, True, False, True])
        self.assertTrue(all(m.message_id is None and m.reply_to_id is None for m in msgs))
        self.assertEqual(msgs[0].chat_id, "[LINE] Chat history with Alice")

    def test_crlf_and_bom(self):
        msgs = _parse("﻿" + ENGLISH, newline="\r\n", self_name=SELF)
        self.assertEqual(len(msgs), 4)
        self.assertEqual(msgs[1].text, "good morning\nhow did you sleep?")

    def test_self_detected_from_header_in_1to1(self):
        msgs = _parse(ENGLISH)
        self.assertEqual({m.sender_id for m in msgs if m.sender_is_self}, {SELF})


class JapaneseFormatTest(unittest.TestCase):
    def test_messages(self):
        msgs = _parse(JAPANESE, name="[LINE] Aliceとのトーク.txt")
        self.assertEqual([m.text for m in msgs], ["こんにちは", "元気？", "おやすみ"])
        self.assertEqual(msgs[0].timestamp, _ts(2018, 7, 10, 19, 42))
        self.assertEqual([m.sender_is_self for m in msgs], [False, True, True])


class VariantTest(unittest.TestCase):
    def test_other_date_line_styles(self):
        text = (
            "[LINE] Chat history with Alice\n\n"
            "2025.12.30 火曜日\n10:00\tAlice\ta\n"
            "2026/01/25（日）\n10:00\tAlice\tb\n"
            "Sun, 01/25/2026\n11:00\tAlice\tc\n"
            "10:01\tYu Sheng\td\n"
        )
        msgs = _parse(text, self_name=SELF)
        self.assertEqual([m.timestamp for m in msgs], [
            _ts(2025, 12, 30, 10, 0), _ts(2026, 1, 25, 10, 0),
            _ts(2026, 1, 25, 11, 0), _ts(2026, 1, 25, 10, 1)])

    def test_twelve_hour_clock(self):
        text = ("[LINE] Chat history with Alice\n\n2026/01/25 Sun\n"
                "PM 08:07\tAlice\tevening\n"
                "12:30 AM\tYu Sheng\tlate\n"
                "午後3:05\tAlice\tafternoon\n")
        msgs = _parse(text, self_name=SELF)
        self.assertEqual([m.timestamp for m in msgs], [
            _ts(2026, 1, 25, 20, 7), _ts(2026, 1, 25, 0, 30), _ts(2026, 1, 25, 15, 5)])

    def test_unquoted_continuation_and_quoted_single_line(self):
        text = ("[LINE] Chat history with Alice\n\n2026/01/25 Sun\n"
                "10:00\tAlice\tline one\nline two\n"
                '10:01\tYu Sheng\t"just quotes"\n')
        msgs = _parse(text, self_name=SELF)
        self.assertEqual([m.text for m in msgs], ["line one\nline two", "just quotes"])

    def test_system_line_without_sender_column(self):
        text = ("[LINE] Chat history in Weekend crew\n\n2026/01/25 Sun\n"
                "10:00\tBob joined the group.\n"
                "10:01\tBob\thi all\n"
                "10:02\tYu Sheng\they bob\n")
        msgs = _parse(text, self_name=SELF)
        self.assertEqual([m.text for m in msgs], ["hi all", "hey bob"])


class SelfNameTest(unittest.TestCase):
    def test_group_needs_self_name(self):
        text = ("[LINE] Chat history in Weekend crew\n\n2026/01/25 Sun\n"
                "10:00\tBob\thi\n10:01\tCarol\tyo\n10:02\tYu Sheng\they\n")
        with self.assertRaises(ValueError) as ctx:
            _parse(text)
        self.assertIn("--self-name", str(ctx.exception))
        self.assertIn("'Carol'", str(ctx.exception))

    def test_explicit_self_name_wins(self):
        msgs = _parse(ENGLISH, self_name="Alice")
        self.assertTrue(msgs[0].sender_is_self)

    def test_not_a_line_export_raises(self):
        with self.assertRaises(ValueError):
            _parse("just some text\nnothing here\n", self_name=SELF)


class RegistryAndCliTest(unittest.TestCase):
    def test_registered(self):
        self.assertIn("line", available_sources())
        self.assertEqual(get_adapter("line").name, "line")

    def test_end_to_end(self):
        import json
        from ingest.cli import main
        os.environ["LLM_VALIDATE"] = "false"
        with tempfile.TemporaryDirectory() as d:
            inp = os.path.join(d, "[LINE] Chat history with Alice.txt")
            with open(inp, "w", encoding="utf-8") as f:
                f.write(ENGLISH)
            out = os.path.join(d, "out.json")
            rc = main(["--source", "line", "--input", inp, "--output", out, "--skip-redact-scan"])
            self.assertEqual(rc, 0)
            with open(out, encoding="utf-8") as f:
                blob = json.dumps(json.load(f), ensure_ascii=False)
        self.assertIn("how did you sleep?", blob)


if __name__ == "__main__":
    unittest.main()
