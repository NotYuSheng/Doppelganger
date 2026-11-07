"""Tests for the interactive menu pieces: pickers (plain mode) and the guided
setup (export discovery, picking who you are, building the parse call).

    python -m unittest tests.test_wizard
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from doppelganger import tui, wizard

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PlainPickerTest(unittest.TestCase):
    """Without a terminal the pickers fall back to numbered prompts."""

    def setUp(self):
        os.environ["DOPPELGANGER_PLAIN"] = "1"

    def tearDown(self):
        os.environ.pop("DOPPELGANGER_PLAIN", None)

    def _run(self, fn, answers):
        with mock.patch("builtins.input", side_effect=answers), redirect_stdout(io.StringIO()):
            return fn()

    def test_select(self):
        self.assertEqual(self._run(lambda: tui.select("t", ["a", "b", "c"]), ["2"]), 1)
        self.assertEqual(self._run(lambda: tui.select("t", ["a", "b"], default=1), [""]), 1)
        self.assertEqual(self._run(lambda: tui.select("t", ["a", "b"]), ["9", "1"]), 0)  # retry
        self.assertIsNone(self._run(lambda: tui.select("t", ["a"]), ["q"]))
        self.assertIsNone(self._run(lambda: tui.select("t", ["a"]), EOFError()))

    def test_multiselect(self):
        self.assertEqual(self._run(lambda: tui.multiselect("t", ["a", "b", "c"]), ["3,1"]), [0, 2])
        self.assertEqual(self._run(lambda: tui.multiselect("t", ["a", "b"], checked={1}), [""]), [1])
        self.assertEqual(self._run(lambda: tui.multiselect("t", ["a", "b"]), ["none"]), [])
        self.assertIsNone(self._run(lambda: tui.multiselect("t", ["a"]), ["q"]))

    def test_multiselect_all_and_action(self):
        self.assertEqual(self._run(lambda: tui.multiselect("t", ["a", "b", "c"], select_all=True), ["all"]),
                         [0, 1, 2])
        self.assertEqual(self._run(lambda: tui.multiselect("t", ["a"], action="Add…"), ["add"]), tui.ACTION)

    def test_not_interactive_without_tty(self):
        self.assertFalse(tui.interactive())


class DiscoverTest(unittest.TestCase):
    def test_finds_every_app_by_name(self):
        with tempfile.TemporaryDirectory() as d:
            data = os.path.join(d, "data")
            os.makedirs(data)
            shutil.copy(os.path.join(ROOT, "demo", "sample_export.json"), os.path.join(data, "result.json"))
            for name in ("WhatsApp Chat with Sam.txt", "[LINE] Chat history with Bo.txt",
                         "KakaoTalkChats.txt", "notes.txt"):
                open(os.path.join(data, name), "w").close()
            os.makedirs(os.path.join(data, "signal-export-2026-11-07"))
            open(os.path.join(data, "signal-export-2026-11-07", "main.jsonl"), "w").close()
            os.makedirs(os.path.join(data, "exports", "instagram-me-2026", "your_instagram_activity"))
            os.makedirs(os.path.join(data, "fb", "your_activity_across_facebook"))
            os.makedirs(os.path.join(d, "venv", "WhatsApp Chat with X.txt"))  # skipped folder
            found = wizard.discover([data, d])
        self.assertEqual([f.source for f in found],
                         ["telegram", "whatsapp", "instagram", "messenger", "line", "signal", "kakaotalk"])

    def test_result_json_must_look_like_telegram(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "result.json"), "w") as f:
                json.dump({"something": "else"}, f)
            self.assertEqual(wizard.discover([d]), [])

    def test_export_folder_not_searched_inside(self):
        with tempfile.TemporaryDirectory() as d:
            inner = os.path.join(d, "facebook-me", "your_facebook_activity", "messages")
            os.makedirs(inner)
            open(os.path.join(d, "facebook-me", "WhatsApp Chat with Z.txt"), "w").close()
            found = wizard.discover([d])
        self.assertEqual([f.source for f in found], ["messenger"])


class FakeUI:
    """Scripted answers for the pickers, in order."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked = []

    def _next(self, title):
        self.asked.append(title)
        return self.answers.pop(0)

    def select(self, title, options, default=0, hints=None):
        return self._next(title)

    def multiselect(self, title, options, checked=None, hints=None, **kw):
        return self._next(title)

    def text(self, prompt, default=""):
        return self._next(prompt)


def _workspace(d):
    data = os.path.join(d, "data")
    os.makedirs(data)
    shutil.copy(os.path.join(ROOT, "demo", "sample_export.json"), os.path.join(data, "result.json"))
    shutil.copy(os.path.join(ROOT, "demo", "sample_whatsapp.txt"),
                os.path.join(data, "WhatsApp Chat with Sam.txt"))
    return data


class WizardFlowTest(unittest.TestCase):
    def test_full_flow_builds_parse_call(self):
        with tempfile.TemporaryDirectory() as d:
            data = _workspace(d)
            # exports: both ticked; WhatsApp self: 2nd most active ("Alex Tan");
            # group chats: focus; scan: choose countries -> SG
            ui = FakeUI([0, 1], 1, 1, 1, None)
            from ingest import redaction
            codes = [c for c in redaction.available_locales() if c != "universal"]
            ui.answers[-1] = [codes.index("SG")]
            with redirect_stdout(io.StringIO()):
                plan = wizard.run(ui, roots=[data])
        kw = plan.parse_kwargs()
        self.assertEqual([i.split(":", 1)[0] for i in kw["input_path"]], ["telegram", "whatsapp"])
        self.assertEqual(kw["self_name"], ["whatsapp:Alex Tan"])  # Telegram detects itself
        self.assertEqual(kw["group_chats"], "focus")
        self.assertEqual(kw["locales"], ["auto", "SG"])
        self.assertEqual(len(ui.asked), 5)

    def test_plan_runs_through_parse(self):
        from doppelganger import steps
        with tempfile.TemporaryDirectory() as d:
            data = _workspace(d)
            with redirect_stdout(io.StringIO()):
                plan = wizard.run(FakeUI([0, 1], 1, 0, 0), roots=[data])
            orig = (steps.DATA_DIR, steps.RAW_PATH, steps.REPORT_PATH)
            steps.DATA_DIR, steps.RAW_PATH, steps.REPORT_PATH = d, os.path.join(d, "raw.jsonl"), os.path.join(d, "r.json")
            try:
                with redirect_stdout(io.StringIO()):
                    rc = steps.parse(**plan.parse_kwargs())
            finally:
                steps.DATA_DIR, steps.RAW_PATH, steps.REPORT_PATH = orig
        self.assertEqual(rc, 0)

    def test_cancel_anywhere_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            data = _workspace(d)
            for answers in ([None], [[0, 1], None], [[0, 1], 1, None], [[0, 1], 1, 0, None],
                            [[0, 1], 1, 0, 1, None]):
                with redirect_stdout(io.StringIO()):
                    self.assertIsNone(wizard.run(FakeUI(*answers), roots=[data]))

    def test_no_exports_offers_manual_path(self):
        with tempfile.TemporaryDirectory() as d:
            empty = os.path.join(d, "data")
            os.makedirs(empty)
            chat = os.path.join(d, "chat.txt")
            shutil.copy(os.path.join(ROOT, "demo", "sample_whatsapp.txt"), chat)
            from ingest.adapters import available_sources
            ui = FakeUI(available_sources().index("whatsapp"), chat, [0], 0, 0, 0)
            with redirect_stdout(io.StringIO()) as out:
                plan = wizard.run(ui, roots=[empty])
        self.assertIn("docs/sources/README.md", out.getvalue())
        self.assertEqual([(e.source, e.path) for e in plan.exports], [("whatsapp", chat)])

    def test_auto_scan_needs_no_country_list(self):
        ui = FakeUI(0)
        self.assertEqual(wizard.choose_countries(ui), [])
        self.assertEqual(len(ui.asked), 1)  # the country list wasn't shown

    def test_add_another_action(self):
        with tempfile.TemporaryDirectory() as d:
            data = _workspace(d)
            extra = os.path.join(d, "more.txt")
            shutil.copy(os.path.join(ROOT, "demo", "sample_whatsapp.txt"), extra)
            from ingest.adapters import available_sources
            ui = FakeUI(tui.ACTION, available_sources().index("whatsapp"), extra, [0, 1, 2])
            with redirect_stdout(io.StringIO()):
                chosen = wizard.choose_exports(ui, roots=[data])
        self.assertEqual([c.path for c in chosen][-1], extra)

    def test_needs_self_name(self):
        with tempfile.TemporaryDirectory() as d:
            data = _workspace(d)
            tg, wa = wizard.discover([data])
            self.assertEqual(wizard.needs_self_name(tg), (False, []))
            needed, senders = wizard.needs_self_name(wa)
        self.assertTrue(needed)
        self.assertEqual(senders, [("Sam", 3), ("Alex Tan", 2)])


class AuditReusesLocalesTest(unittest.TestCase):
    def test_previous_locales_from_report(self):
        from doppelganger import steps
        with tempfile.TemporaryDirectory() as d:
            report = os.path.join(d, "r.json")
            with open(report, "w") as f:
                json.dump({"locales": ["auto", "SG"]}, f)
            orig = steps.REPORT_PATH
            steps.REPORT_PATH = report
            try:
                self.assertEqual(steps._previous_locales(), ["auto", "SG"])
                os.remove(report)
                self.assertIsNone(steps._previous_locales())
            finally:
                steps.REPORT_PATH = orig


if __name__ == "__main__":
    unittest.main()
