"""Unit tests for the KakaoTalk export adapter (stdlib unittest, no network).

Run from the repo root:

    python -m unittest tests.test_kakaotalk

Fixtures are synthetic, modelled on the layouts KakaoTalk's Export Messages
produces: Android/iOS in English and Korean, the older time-first layout,
Windows PC, and the macOS CSV.
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingest.adapters import available_sources, get_adapter
from ingest.adapters.kakaotalk import KakaoTalkAdapter

SELF = "Yu Sheng"


def _parse(text, name="KakaoTalkChats.txt", newline="\n", **kw):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, name)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text.replace("\n", newline))
        return KakaoTalkAdapter().parse(path, **kw)


def _ts(y, mo, d, h, mi):
    return int(datetime(y, mo, d, h, mi).timestamp())


ANDROID_EN = """\
KakaoTalk Chats with Alice
Date Saved : Nov 7, 2026 at 10:00 AM

October 12, 2020 at 6:49 AM
October 12, 2020 at 6:49 AM, Alice : morning
October 12, 2020 at 6:50 AM, Alice : Photo
October 12, 2020 at 6:51 AM, Yu Sheng : hey! long message
that continues here
October 12, 2020 at 12:05 PM, Alice : lunch?
October 12, 2020 at 12:06 PM, Alice left this chatroom.
"""

IOS_KO = """\
Alice 님과 카카오톡 대화
저장한 날짜 : 2024. 3. 17. 오후 11:30

2024년 3월 14일 목요일
2024. 3. 14. 오후 7:12, Alice : 내일 뭐 해?
2024. 3. 14. 오후 7:13, Yu Sheng : 아직 몰라
2024. 3. 14. 오후 7:14, Alice : 이모티콘
2024. 3. 14. 오후 7:15, Alice : 삭제된 메시지입니다.
2024. 3. 14. 오후 7:16, Alice : 사진.jpg
"""


class AndroidIosTest(unittest.TestCase):
    def test_android_english(self):
        msgs = _parse(ANDROID_EN)
        self.assertEqual([m.text for m in msgs],
                         ["morning", "hey! long message\nthat continues here", "lunch?"])
        self.assertEqual(msgs[0].timestamp, _ts(2020, 10, 12, 6, 49))
        self.assertEqual(msgs[2].timestamp, _ts(2020, 10, 12, 12, 5))
        self.assertEqual([m.sender_is_self for m in msgs], [False, True, False])  # from header
        self.assertTrue(all(m.message_id is None and m.reply_to_id is None for m in msgs))

    def test_english_comma_variant(self):
        msgs = _parse("October 12, 2020, 6:49 AM, Alice : hi\nOctober 12, 2020, 6:50 PM, Yu Sheng : yo\n",
                      self_name=SELF)
        self.assertEqual([m.timestamp for m in msgs], [_ts(2020, 10, 12, 6, 49), _ts(2020, 10, 12, 18, 50)])

    def test_ios_korean(self):
        msgs = _parse(IOS_KO)
        self.assertEqual([m.text for m in msgs], ["내일 뭐 해?", "아직 몰라"])
        self.assertEqual(msgs[0].timestamp, _ts(2024, 3, 14, 19, 12))
        self.assertTrue(msgs[1].sender_is_self)

    def test_android_korean_and_time_first(self):
        text = ("2020년 10월 12일 오전 12:30, Alice : 자니?\n"
                "오후 10:50, 2011년 11월 6일, Yu Sheng : 옛날 형식\n"
                "오후 10:51, 2011년 11월 6일, Alice님이 들어왔습니다.\n")
        msgs = _parse(text, self_name=SELF)
        self.assertEqual([(m.text, m.timestamp) for m in msgs],
                         [("자니?", _ts(2020, 10, 12, 0, 30)), ("옛날 형식", _ts(2011, 11, 6, 22, 50))])

    def test_crlf_bom_and_odd_spaces(self):
        msgs = _parse("﻿" + ANDROID_EN.replace(" AM", " AM"), newline="\r\n")
        self.assertEqual(len(msgs), 3)

    def test_text_containing_colon_separator(self):
        msgs = _parse("October 12, 2020 at 6:49 AM, Alice : ratio is 3 : 1\n", self_name=SELF)
        self.assertEqual(msgs[0].text, "ratio is 3 : 1")


class ReviewRegressionTest(unittest.TestCase):
    def test_wrapped_line_starting_with_a_date_is_continuation(self):
        text = ("2020년 10월 12일 오전 6:40, Alice : meeting moved\n"
                "2020년 10월 12일 6:49에 만나자\n"
                "see you then\n"
                "2020년 10월 12일 오전 6:50, Yu Sheng : ok\n")
        msgs = _parse(text, self_name=SELF)
        self.assertEqual([m.text for m in msgs],
                         ["meeting moved\n2020년 10월 12일 6:49에 만나자\nsee you then", "ok"])

    def test_english_date_dividers_not_glued_to_messages(self):
        text = ("Monday, October 12, 2020\n"
                "October 12, 2020 at 11:59 PM, Alice : night\n"
                "Tuesday, October 13, 2020\n"
                "October 13, 2020 at 8:00 AM, Yu Sheng : morning\n")
        msgs = _parse(text, self_name=SELF)
        self.assertEqual([m.text for m in msgs], ["night", "morning"])

    def test_pc_english_layout(self):
        text = ("--------------- Monday, October 12, 2020 ---------------\n"
                "[Alice] [11:59 PM] night\n"
                "--------------- Tuesday, October 13, 2020 ---------------\n"
                "[Yu Sheng] [8:00 AM] morning\n")
        msgs = _parse(text, name="KakaoTalk_2020.txt", self_name=SELF)
        self.assertEqual([(m.text, m.timestamp) for m in msgs],
                         [("night", _ts(2020, 10, 12, 23, 59)), ("morning", _ts(2020, 10, 13, 8, 0))])

    def test_blank_lines_inside_message_kept(self):
        text = "October 12, 2020 at 6:49 AM, Alice : para1\n\npara2\n"
        msgs = _parse(text, self_name=SELF)
        self.assertEqual(msgs[0].text, "para1\n\npara2")

    def test_malformed_pc_time_does_not_crash(self):
        text = ("--------------- 2024년 3월 14일 목요일 ---------------\n"
                "[Alice] [오후 7:12] hi\n"
                "[Alice] [오후 25:10] broken\n"
                "[Yu Sheng] [오후 7:13] yo\n")
        msgs = _parse(text, name="KakaoTalk_x.txt", self_name=SELF)
        self.assertEqual([m.text for m in msgs], ["hi\n[Alice] [오후 25:10] broken", "yo"])

    def test_invisible_marks_before_placeholder(self):
        text = ("2024. 3. 14. 오후 7:12, Alice : \u200e사진\n"
                "2024. 3. 14. 오후 7:13, Yu Sheng : real\n")
        msgs = _parse(text, self_name=SELF)
        self.assertEqual([m.text for m in msgs], ["real"])


class PcAndMacTest(unittest.TestCase):
    def test_windows_pc(self):
        text = ("Alice 님과 카카오톡 대화\n저장한 날짜 : 2024-03-17 23:30:00\n\n"
                "--------------- 2024년 3월 14일 목요일 ---------------\n"
                "[Alice] [오후 7:12] 안녕\n"
                "[Yu Sheng] [오후 7:13] 반가워\n두 줄째\n"
                "[Alice] [오전 12:01] 사진\n")
        msgs = _parse(text, name="KakaoTalk_20240317.txt")
        self.assertEqual([m.text for m in msgs], ["안녕", "반가워\n두 줄째"])
        self.assertEqual(msgs[0].timestamp, _ts(2024, 3, 14, 19, 12))
        self.assertTrue(msgs[1].sender_is_self)

    def test_macos_csv(self):
        text = ('Date,User,Message\n'
                '2024-03-14 19:12:00,Alice,"hello, there"\n'
                '2024-03-14 19:13:00,Yu Sheng,"multi\nline"\n'
                '2024-03-14 19:14:00,Alice,Photo\n'
                ',,\n')
        msgs = _parse(text, name="KakaoTalk_Chat_Alice.csv", self_name=SELF)
        self.assertEqual([m.text for m in msgs], ["hello, there", "multi\nline"])
        self.assertEqual(msgs[0].timestamp, _ts(2024, 3, 14, 19, 12))


class SelfNameTest(unittest.TestCase):
    def test_group_needs_self_name(self):
        text = ("Alice, Bob 님과 카카오톡 대화\n"
                "2024. 3. 14. 오후 7:12, Alice : hi\n2024. 3. 14. 오후 7:13, Bob : yo\n"
                "2024. 3. 14. 오후 7:14, Yu Sheng : hey\n")
        with self.assertRaises(ValueError) as ctx:
            _parse(text)
        self.assertIn("--self-name", str(ctx.exception))
        msgs = _parse(text, self_name=SELF)
        self.assertEqual(sum(m.sender_is_self for m in msgs), 1)

    def test_not_a_kakaotalk_export_raises(self):
        with self.assertRaises(ValueError):
            _parse("hello\nworld\n", self_name=SELF)

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            KakaoTalkAdapter().parse("/nonexistent/KakaoTalkChats.txt", self_name=SELF)


class RegistryAndCliTest(unittest.TestCase):
    def test_registered(self):
        self.assertIn("kakaotalk", available_sources())
        self.assertEqual(get_adapter("kakaotalk").name, "kakaotalk")

    def test_end_to_end(self):
        from ingest.cli import main
        os.environ["LLM_VALIDATE"] = "false"
        with tempfile.TemporaryDirectory() as d:
            inp = os.path.join(d, "KakaoTalkChats.txt")
            with open(inp, "w", encoding="utf-8") as f:
                f.write(ANDROID_EN)
            out = os.path.join(d, "out.json")
            rc = main(["--source", "kakaotalk", "--input", inp, "--output", out, "--skip-redact-scan"])
            self.assertEqual(rc, 0)
            with open(out, encoding="utf-8") as f:
                blob = json.dumps(json.load(f))
        self.assertIn("that continues here", blob)


if __name__ == "__main__":
    unittest.main()
