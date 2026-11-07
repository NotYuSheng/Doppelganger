"""Tests for the evaluation harness (issue #17): stylometry, held-out split,
generation, scoring, blind test, LLM judge, history, and the audit/CLI wiring.

    python -m unittest tests.test_eval
"""

import io
import json
import os
import random
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingest import stylometry
from doppelganger import evaluate


def _quiet(fn, *a, **kw):
    with redirect_stdout(io.StringIO()) as out:
        result = fn(*a, **kw)
    return result, out.getvalue()


def _sample(i, reply="haha yeah see you there!"):
    return [{"role": "user", "text": f"party at {i}?"}, {"role": "assistant", "text": reply}]


class FakeClient:
    """OpenAI-shaped client returning scripted replies and recording prompts."""

    def __init__(self, reply):
        self.reply, self.calls = reply, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, model, messages, **kw):
        self.calls.append(messages)
        text = self.reply(messages) if callable(self.reply) else self.reply
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


class StylometryTest(unittest.TestCase):
    def test_features(self):
        f = stylometry.features("lol yes!! 😂\nsee you soon")
        self.assertEqual((f["messages"], f["emoji"], f["exclaim"], f["laugh"], f["lower_start"]),
                         (2, 1, 1, 1, 1))
        self.assertEqual(stylometry.features("Fine.")["end_period"], 1)
        self.assertEqual(stylometry.features("well...")["end_period"], 0)
        self.assertEqual(stylometry.features("that is HUGE")["caps_word"], 1)

    def test_style_match(self):
        casual = stylometry.profile(["lol yeah", "omg nooo 😭", "haha ok!!"])
        formal = stylometry.profile(["Certainly. I will attend the meeting at noon.",
                                     "Thank you for letting me know.", "That sounds reasonable."])
        self.assertEqual(stylometry.style_match(casual, casual), 100.0)
        self.assertLess(stylometry.style_match(formal, casual), 70)
        self.assertTrue(stylometry.biggest_gaps(formal, casual))

    def test_chrf(self):
        self.assertEqual(stylometry.chrf("see you at 7", "see you at 7"), 100.0)
        self.assertGreater(stylometry.chrf("see u at 7", "see you at 7"),
                           stylometry.chrf("I will be there", "see you at 7"))
        self.assertEqual(stylometry.chrf("", "hi"), 0.0)

    def test_binomial(self):
        self.assertEqual(stylometry.binomial_p(10, 20), 1.0)
        self.assertLess(stylometry.binomial_p(19, 20), 0.001)


class HoldoutTest(unittest.TestCase):
    def test_split_is_deterministic_and_sized(self):
        samples = [_sample(i) for i in range(1000)]
        train, held = evaluate.split_holdout(samples, 0.1)
        self.assertEqual(len(train) + len(held), 1000)
        self.assertTrue(70 < len(held) < 130)
        again_train, again_held = evaluate.split_holdout(list(reversed(samples)), 0.1)
        self.assertEqual(sorted(map(str, held)), sorted(map(str, again_held)))
        self.assertEqual(evaluate.split_holdout(samples, 0), (samples, []))

    def test_items_cut_before_last_reply(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "h.jsonl")
            conv = [{"role": "user", "text": "hey"}, {"role": "assistant", "text": "yo"},
                    {"role": "user", "text": "dinner?"}, {"role": "assistant", "text": "yes pls"},
                    {"role": "user", "text": "7?"}]
            evaluate.write_holdout([conv, [{"role": "assistant", "text": "only me"}]], path)
            with open(path, "a") as f:
                f.write("{bad\n")
            items = evaluate.load_items(path)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["reference"], "yes pls")
        self.assertEqual([t["text"] for t in items[0]["context"]], ["hey", "yo", "dinner?"])


class ReviewRegressionTest(unittest.TestCase):
    def test_items_merge_runs_and_start_with_the_other_person(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "h.jsonl")
            evaluate.write_holdout([[
                {"role": "assistant", "text": "you first"},
                {"role": "user", "text": "how was it?"}, {"role": "user", "text": "the trip"},
                {"role": "assistant", "text": "omg"}, {"role": "assistant", "text": "so good"},
            ]], path)
            items = evaluate.load_items(path)
        self.assertEqual(items[0]["reference"], "omg\nso good")           # whole reply, both messages
        self.assertEqual(items[0]["context"], [{"role": "user", "text": "how was it?\nthe trip"}])

    def test_judge_letter_parsing(self):
        self.assertEqual(evaluate._judge_letter("It's a close call, but B."), "B")
        self.assertEqual(evaluate._judge_letter("A"), "A")
        self.assertEqual(evaluate._judge_letter("b"), "B")
        self.assertIsNone(evaluate._judge_letter("not sure"))

    def test_generate_saves_progress_before_a_failure(self):
        items = [{"id": f"i{n}", "context": [{"role": "user", "text": "q"}], "reference": "r"} for n in range(3)]
        calls = {"n": 0}

        def flaky(messages):
            calls["n"] += 1
            if calls["n"] == 3:
                raise ConnectionError("dropped")
            return "fine"

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "g.json")
            with self.assertRaises(ConnectionError):
                _quiet(evaluate.generate, "ft", client=FakeClient(flaky), items=items, path=path)
            self.assertEqual(len(evaluate.load_generations(path)["ft"]), 2)

    def test_score_records_only_changes_and_only_the_label_asked(self):
        from doppelganger import eval_menu
        with tempfile.TemporaryDirectory() as d:
            hold, gens, hist = (os.path.join(d, n) for n in ("h.jsonl", "g.json", "hist.jsonl"))
            evaluate.write_holdout([_sample(i) for i in range(4)], hold)
            items = evaluate.load_items(hold)
            with open(gens, "w") as f:
                json.dump({"base": {i["id"]: "Hello." for i in items},
                           "ft": {i["id"]: "haha yeah" for i in items}}, f)
            with mock.patch.object(evaluate, "HOLDOUT_PATH", hold), \
                    mock.patch.object(evaluate, "GENERATIONS_PATH", gens), \
                    mock.patch.object(evaluate, "HISTORY_PATH", hist):
                _quiet(eval_menu.do_score, ["ft"])
                _quiet(eval_menu.do_score)          # all labels: base is new, ft unchanged
                _quiet(eval_menu.do_score)          # nothing changed
                labels = [e["label"] for e in evaluate.load_history()]
        self.assertEqual(sorted(labels), ["base", "ft"])

    def test_cancelling_model_picker_runs_nothing(self):
        from doppelganger import eval_menu

        class UI:
            def __init__(self):
                self.answers = [2, None]   # "Blind test", then cancel "Which model?"

            def select(self, *a, **kw):
                return self.answers.pop(0)

        with mock.patch.object(evaluate, "load_items", return_value=[{"id": "x"}]), \
                mock.patch.object(evaluate, "load_generations", return_value={"a": {}, "b": {}}), \
                mock.patch.object(eval_menu, "do_blind") as blind:
            rc, _ = _quiet(eval_menu.run, UI())
        self.assertEqual(rc, 0)
        blind.assert_not_called()


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.items = [{"id": f"i{n}", "context": [{"role": "user", "text": f"q{n}"}],
                       "reference": "lol yeah 😂"} for n in range(6)]

    def test_generate_resumes_and_sends_context(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "g.json")
            client = FakeClient("haha sure")
            made, _ = _quiet(evaluate.generate, "ft", client=client, items=self.items, path=path,
                             system_prompt="You are Alex.")
            self.assertEqual(made, 6)
            self.assertEqual(client.calls[0][0], {"role": "system", "content": "You are Alex."})
            self.assertEqual(client.calls[0][1], {"role": "user", "content": "q0"})
            again, _ = _quiet(evaluate.generate, "ft", client=FakeClient("x"), items=self.items, path=path)
            self.assertEqual(again, 0)  # already done
            self.assertEqual(evaluate.load_generations(path)["ft"]["i3"], "haha sure")

    def test_score_prefers_closer_model(self):
        gens = {"close": {i["id"]: "lol yea 😂" for i in self.items},
                "far": {i["id"]: "Certainly, I would be delighted to attend." for i in self.items}}
        close = evaluate.score("close", self.items, gens)
        far = evaluate.score("far", self.items, gens)
        self.assertGreater(close["chrf"], far["chrf"])
        self.assertGreater(close["style_match"], far["style_match"])
        self.assertIsNone(evaluate.score("missing", self.items, gens))

    def test_blind_test_counts_correct_picks(self):
        gens = {"ft": {i["id"]: "model reply" for i in self.items}}

        class AlwaysReal:
            def select(self, title, options, **kw):
                return 0 if "lol yeah" in options[0] else 1

        result, _ = _quiet(evaluate.blind_test, "ft", 6, ui=AlwaysReal(), rng=random.Random(1),
                           items=self.items, generations=gens)
        self.assertEqual((result["correct"], result["total"], result["rate"]), (6, 6, 100.0))
        self.assertIn("too few answers", evaluate.format_spotting(result))  # 6 < MIN_TRIALS
        self.assertIn("can tell them apart", evaluate.format_spotting(evaluate.distinguishability(19, 20)))
        self.assertIn("can't reliably tell", evaluate.format_spotting(evaluate.distinguishability(11, 20)))

    def test_llm_judge(self):
        gens = {"ft": {i["id"]: "model reply" for i in self.items}}

        def picks_real(messages):
            prompt = messages[0]["content"]
            return "A" if "Reply A: lol yeah" in prompt else "B"

        result = evaluate.llm_judge("ft", 6, client=FakeClient(picks_real), model="judge",
                                    rng=random.Random(2), items=self.items, generations=gens)
        self.assertEqual(result["rate"], 100.0)
        coin = evaluate.llm_judge("ft", 6, client=FakeClient("A"), model="judge",
                                  rng=random.Random(3), items=self.items, generations=gens)
        self.assertEqual(coin["total"], 6)

    def test_history_report(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "h.jsonl")
            evaluate.record("score", "base", {"n": 6, "chrf": 20.0, "style_match": 55.0}, path)
            evaluate.record("score", "finetuned", {"n": 6, "chrf": 41.5, "style_match": 88.0}, path)
            evaluate.record("blind", "finetuned", evaluate.distinguishability(11, 20), path)
            text = evaluate.report(evaluate.load_history(path))
        self.assertIn("finetuned", text)
        self.assertIn("41.5", text)
        self.assertIn("55%", text)
        self.assertIn("~50% = indistinguishable", text)
        self.assertEqual(evaluate.report([]), "No evaluation results yet.")


class WiringTest(unittest.TestCase):
    def _steps_in(self, d):
        from doppelganger import steps
        patches = [mock.patch.object(steps, "RAW_PATH", os.path.join(d, "raw.jsonl")),
                   mock.patch.object(steps, "DATASET_PATH", os.path.join(d, "ds.json")),
                   mock.patch.object(steps, "REPORT_PATH", os.path.join(d, "r.json")),
                   mock.patch.object(evaluate, "HOLDOUT_PATH", os.path.join(d, "hold.jsonl")),
                   mock.patch.object(evaluate, "HOLDOUT_META_PATH", os.path.join(d, "hold.meta.json"))]
        return steps, patches

    def _run_audit(self, d, samples, **kw):
        steps, patches = self._steps_in(d)
        for p in patches:
            p.start()
        try:
            from ingest import sharegpt
            sharegpt.write_jsonl(samples, steps.RAW_PATH)
            rc, out = _quiet(steps.audit, validate=False, **kw)
            with open(steps.DATASET_PATH) as f:
                trained = json.load(f)
            held = evaluate.load_items(evaluate.HOLDOUT_PATH)
            return rc, out, trained, held, os.path.exists(evaluate.HOLDOUT_PATH)
        finally:
            for p in patches:
                p.stop()

    def test_audit_holdout_is_sticky_and_explicit_zero_removes(self):
        samples = [_sample(i) for i in range(200)]
        with tempfile.TemporaryDirectory() as d:
            _, _, trained, held, _ = self._run_audit(d, samples, redact="off", holdout=0.1)
            self.assertEqual(len(trained) + len(held), 200)
            self.assertGreater(len(held), 5)
            # Default (menu audit / auto): the existing test set is kept, not leaked.
            _, out, trained2, held2, _ = self._run_audit(d, samples, redact="off")
            self.assertEqual(len(held2), len(held))
            self.assertIn("Kept the existing test set", out)
            # Explicit 0 removes it.
            _, out, trained3, _, exists = self._run_audit(d, samples, redact="off", holdout=0)
            self.assertFalse(exists)
            self.assertEqual(len(trained3), 200)
            self.assertIn("Removed the test set", out)

    def test_holdout_membership_survives_redaction_changes(self):
        samples = [[{"role": "user", "text": f"mail me at person{i}@example.com"},
                    {"role": "assistant", "text": "ok"}] for i in range(200)]
        with tempfile.TemporaryDirectory() as d:
            *_, held_off, _ = self._run_audit(d, samples, redact="off", holdout=0.1)
        with tempfile.TemporaryDirectory() as d:
            *_, held_rep, _ = self._run_audit(d, samples, redact="replace", holdout=0.1)
        self.assertEqual(len(held_off), len(held_rep))  # same conversations, just redacted
        self.assertTrue(all("[EMAIL]" in i["context"][0]["text"] for i in held_rep))

    def test_bad_holdout_values(self):
        from doppelganger.__main__ import main
        for bad in ("10", "-0.1", "abc"):
            with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()), \
                    mock.patch("sys.stderr", io.StringIO()):
                main(["audit", "--holdout", bad])
        with self.assertRaises(ValueError):
            evaluate.resolve_fraction(1.5)

    def test_cli_without_test_set_explains(self):
        from doppelganger.__main__ import main
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(evaluate, "HOLDOUT_PATH", os.path.join(d, "none.jsonl")):
            rc, out = _quiet(main, ["eval", "score"])
        self.assertEqual(rc, 1)
        self.assertIn("audit --holdout 0.1", out)

    def test_generate_reports_unreachable_endpoint(self):
        from doppelganger import eval_menu
        with tempfile.TemporaryDirectory() as d:
            hold = os.path.join(d, "hold.jsonl")
            evaluate.write_holdout([_sample(1)], hold)
            with mock.patch.object(evaluate, "HOLDOUT_PATH", hold), \
                    mock.patch.object(evaluate, "make_client", side_effect=ConnectionError("refused")):
                rc, out = _quiet(eval_menu.do_generate, "ft", "http://localhost:9/v1", "m")
        self.assertEqual(rc, 1)
        self.assertIn("doppelganger serve", out)


if __name__ == "__main__":
    unittest.main()
