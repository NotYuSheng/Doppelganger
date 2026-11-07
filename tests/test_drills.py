"""Tests for reply drills (issue #14, phase 1): scenario library, coverage-
driven selection, storage, the elicited adapter, and the session runner.

    python -m unittest tests.test_drills
"""

import io
import json
import os
import random
import sys
import tempfile
import unittest
from collections import Counter
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingest import core, drills
from ingest.adapters import available_sources, get_adapter


def _quiet(fn, *a, **kw):
    with redirect_stdout(io.StringIO()) as out:
        result = fn(*a, **kw)
    return result, out.getvalue()


class LibraryTest(unittest.TestCase):
    def setUp(self):
        self.lib = drills.load_library()

    def test_every_situation_and_audience_is_covered(self):
        ids = [s.id for s in self.lib.scenarios]
        self.assertEqual(len(ids), len(set(ids)), "scenario ids must be unique")
        cells = Counter((s.situation, s.audience) for s in self.lib.scenarios)
        for sit in self.lib.situations:
            for aud in self.lib.audiences:
                self.assertGreaterEqual(cells[(sit, aud)], 1, f"no scenario for {sit} × {aud}")

    def test_scenarios_reference_known_ids_and_have_text(self):
        for s in self.lib.scenarios:
            self.assertIn(s.situation, self.lib.situations)
            self.assertIn(s.audience, self.lib.audiences)
            self.assertTrue(s.message.strip())


class SelectionTest(unittest.TestCase):
    def setUp(self):
        self.lib = drills.load_library()

    def _answer(self, scenario):
        return {"kind": "drill", "scenario": scenario.id, "situation": scenario.situation,
                "audience": scenario.audience, "reply": ["x"]}

    def test_first_session_spreads_across_situations(self):
        batch = drills.next_scenarios(self.lib, [], 12, rng=random.Random(1))
        self.assertEqual(len({s.situation for s in batch}), 12)

    def test_least_covered_situations_first(self):
        covered = [self._answer(s) for s in self.lib.scenarios if s.situation != "apology"]
        batch = drills.next_scenarios(self.lib, covered, 3, rng=random.Random(2))
        self.assertTrue(all(s.situation == "apology" for s in batch))

    def test_unanswered_first_then_reuse(self):
        answered = [self._answer(s) for s in self.lib.scenarios]
        batch = drills.next_scenarios(self.lib, answered[:-1], 1, rng=random.Random(3))
        self.assertEqual(batch[0].id, self.lib.scenarios[-1].id)
        self.assertEqual(len(drills.next_scenarios(self.lib, answered, 5)), 5)  # all done: reuse

    def test_situation_filter(self):
        batch = drills.next_scenarios(self.lib, [], 50, situation="conflict")
        self.assertEqual({s.situation for s in batch}, {"conflict"})
        self.assertEqual(len(batch), 8)


class StorageTest(unittest.TestCase):
    def test_round_trip_and_bad_lines(self):
        lib = drills.load_library()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "data", "elicited.jsonl")
            drills.save_answer(lib.scenarios[0], ["first", "  ", "second"], path)
            with open(path, "a") as f:
                f.write("{broken\n" + json.dumps({"kind": "other"}) + "\n")
            answers = drills.load_answers(path)
        self.assertEqual(len(answers), 1)
        self.assertEqual(answers[0]["reply"], ["first", "second"])
        self.assertEqual(answers[0]["situation"], lib.scenarios[0].situation)

    def test_missing_file_is_empty(self):
        self.assertEqual(drills.load_answers("/nonexistent/elicited.jsonl"), [])

    def test_coverage_report(self):
        lib = drills.load_library()
        s = lib.by_id("apology-friend-1")
        report = drills.coverage_report([{"situation": s.situation, "audience": s.audience}], lib)
        self.assertIn("1 answer(s) across 1/12 situations", report)
        self.assertIn("Not covered yet:", report)
        self.assertNotIn("you let them down", report.split("Not covered yet:")[1])


class AdapterTest(unittest.TestCase):
    def test_registered_and_builds_samples(self):
        self.assertIn("elicited", available_sources())
        lib = drills.load_library()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "elicited.jsonl")
            drills.save_answer(lib.by_id("good_news-friend-1"), ["no way!!", "congrats"], path)
            drills.save_answer(lib.by_id("plans-colleague-1"), ["sure, thursday works"], path)
            msgs, _ = _quiet(get_adapter("elicited").parse, path)
        self.assertEqual(sum(m.sender_is_self for m in msgs), 3)
        samples = core.build_samples(msgs)
        self.assertEqual([[t["text"] for t in s] for s in samples], [
            ["I GOT THE JOB!!!", "no way!!\ncongrats"],
            ["can we move our 1:1 to thursday? something came up", "sure, thursday works"],
        ])

    def test_missing_file_explains(self):
        with self.assertRaises(FileNotFoundError) as ctx:
            get_adapter("elicited").parse("/nonexistent/elicited.jsonl")
        self.assertIn("doppelganger drills", str(ctx.exception))


class FakeUI:
    def __init__(self, *answers):
        self.answers = list(answers)

    def select(self, title, options, default=0, hints=None):
        return self.answers.pop(0)


class SessionTest(unittest.TestCase):
    def _run(self, inputs, **kw):
        from doppelganger import drills as session
        feed = iter(inputs)

        def fake_input(prompt):
            value = next(feed)
            if isinstance(value, BaseException):
                raise value
            return value

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "elicited.jsonl")
            rc, out = _quiet(session.run_session, path=path, input_fn=fake_input,
                             rng=random.Random(0), **kw)
            return rc, out, drills.load_answers(path)

    def test_answer_multiline_skip_and_stop(self):
        rc, out, answers = self._run(["hey!", "sounds good", "", "", "sorry about that", "", "/stop"],
                                     count=5)
        self.assertEqual(rc, 0)
        self.assertEqual([a["reply"] for a in answers], [["hey!", "sounds good"], ["sorry about that"]])
        self.assertIn("Saved 2 answer(s), skipped 1", out)

    def test_asks_session_size(self):
        from doppelganger import drills as session
        feed = iter(["ok", ""] * 10)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "e.jsonl")
            _quiet(session.run_session, path=path, ui=FakeUI(0), input_fn=lambda p: next(feed))
            self.assertEqual(len(drills.load_answers(path)), 5)

    def test_end_of_input_saves_what_was_answered(self):
        _, _, answers = self._run(["yes", "", EOFError()], count=5)
        self.assertEqual(len(answers), 1)

    def test_unknown_situation(self):
        rc, out, _ = self._run([], count=1, situation="nope")
        self.assertEqual(rc, 2)


class WizardTest(unittest.TestCase):
    def test_drill_answers_are_found_as_a_source(self):
        from doppelganger import wizard
        lib = drills.load_library()
        with tempfile.TemporaryDirectory() as d:
            drills.save_answer(lib.scenarios[0], ["hi"], os.path.join(d, "elicited.jsonl"))
            found = wizard.discover([d])
            self.assertEqual([f.source for f in found], ["elicited"])
            self.assertEqual(wizard.needs_self_name(found[0]), (False, []))


if __name__ == "__main__":
    unittest.main()
