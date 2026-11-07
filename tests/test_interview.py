"""Tests for the persona interview (issue #14, phase 2).

    python -m unittest tests.test_interview
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingest import core, interview as iv
from ingest.adapters import get_adapter


def _quiet(fn, *a, **kw):
    with redirect_stdout(io.StringIO()) as out:
        result = fn(*a, **kw)
    return result, out.getvalue()


class FakeClient:
    def __init__(self, reply):
        self.reply, self.prompts = reply, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, model, messages, **kw):
        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        text = self.reply(prompt) if callable(self.reply) else self.reply
        if isinstance(text, Exception):
            raise text
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


class FakeUI:
    def __init__(self, *answers):
        self.answers, self.hints = list(answers), []

    def select(self, title, options, default=0, hints=None):
        self.hints = list(hints or [])
        return self.answers.pop(0)


def _feed(*lines):
    it = iter(lines)

    def fake_input(prompt):
        value = next(it)
        if isinstance(value, BaseException):
            raise value
        return value
    return fake_input


class TopicsTest(unittest.TestCase):
    def test_topics_complete(self):
        ids = [t.id for t in iv.TOPICS]
        self.assertEqual(len(ids), len(set(ids)))
        for t in iv.TOPICS:
            self.assertTrue(t.question.endswith(("?", ".")))
            self.assertGreaterEqual(len(t.follow_ups), 2)

    def test_depth_estimates(self):
        self.assertEqual(iv.estimate_minutes(9, "short"), 9)
        self.assertEqual(iv.estimate_minutes(9, "standard"), 27)
        self.assertEqual(iv.estimate_minutes(9, "deep"), 45)
        with self.assertRaises(ValueError):
            iv.depth_followups("epic")


class SessionTest(unittest.TestCase):
    def _run(self, inputs, d, **kw):
        from doppelganger import interview
        path, card = os.path.join(d, "e.jsonl"), os.path.join(d, "persona.md")
        kw.setdefault("access", (None, None, "offline"))
        rc, out = _quiet(interview.run_session, path=path, persona_path=card,
                         input_fn=_feed(*inputs), **kw)
        return rc, out, iv.load_turns(path), iv.load_card(card)

    def test_offline_standard_uses_fixed_follow_ups(self):
        with tempfile.TemporaryDirectory() as d:
            rc, out, turns, card = self._run(
                ["i'm alex lol", "", "chill", "", "/skip", "/stop"], d, depth="standard")
        self.assertEqual(rc, 0)
        self.assertEqual([t["question"] for t in turns],
                         [iv.TOPICS[0].question, iv.TOPICS[0].follow_ups[0]])
        self.assertIn("i'm alex lol", card)
        self.assertIn("Saved 2 answer(s)", out)

    def test_next_skips_topic_and_resume_continues(self):
        with tempfile.TemporaryDirectory() as d:
            self._run(["/next", "backend dev", "", "/stop"], d, depth="short")
            _, out, turns, _ = self._run(["family + 2 close friends", "", "/stop"], d, depth="short")
        self.assertEqual([t["topic"] for t in turns], ["work", "intro"])  # intro was skipped first
        self.assertIn("1 of 9 topics done", out)

    def test_llm_follow_ups_and_next(self):
        client = FakeClient(lambda p: "NEXT" if p.count("\nQ: ") >= 2 else "what's your coffee order?")
        with tempfile.TemporaryDirectory() as d:
            _, _, turns, _ = self._run(["alex", "", "flat white", "", "/stop"], d, depth="deep",
                                       access=(client, "m", "llm"))
        self.assertEqual([t["question"] for t in turns][1], "what's your coffee order?")

    def test_llm_failure_falls_back_to_fixed_question(self):
        def flaky(prompt):
            if "Ask ONE short" in prompt:
                return ConnectionError("down")
            return "You are Alex."
        with tempfile.TemporaryDirectory() as d:
            _, out, turns, card = self._run(["alex", "", "nice", "", "/stop"], d, depth="standard",
                                            access=(FakeClient(flaky), "m", "llm"))
        self.assertEqual(turns[1]["question"], iv.TOPICS[0].follow_ups[0])
        self.assertIn("LLM follow-up failed", out)
        self.assertEqual(card, "You are Alex.")

    def test_asks_depth_with_estimates(self):
        ui = FakeUI(0)
        with tempfile.TemporaryDirectory() as d:
            self._run(["/stop"], d, ui=ui)
        self.assertEqual(ui.hints[0], "one question per topic · ~9 min")
        self.assertIn("~27 min", ui.hints[1])

    def test_all_topics_done_offers_rebuild(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "e.jsonl")
            for t in iv.TOPICS:
                iv.save_turn(t, t.question, ["answer"], path)
            _, out, _, card = self._run([], d, ui=FakeUI(0))
        self.assertIn("**How you text**", card)
        self.assertNotIn("**A recent story**", card)  # stories are training data, not card


class ReviewRegressionTest(unittest.TestCase):
    def _run(self, inputs, d, **kw):
        from doppelganger import interview
        path, card = os.path.join(d, "e.jsonl"), os.path.join(d, "persona.md")
        kw.setdefault("access", (None, None, "offline"))
        rc, out = _quiet(interview.run_session, path=path, persona_path=card,
                         input_fn=_feed(*inputs), **kw)
        return rc, out, iv.load_turns(path), iv.load_card(card)

    def test_half_typed_answer_saved_before_a_command(self):
        with tempfile.TemporaryDirectory() as d:
            _, _, turns, _ = self._run(["line one", "line two", "/stop"], d, depth="short")
            _, _, turns2, _ = self._run(["partial", KeyboardInterrupt()], d, depth="short")
        self.assertEqual(turns[0]["reply"], ["line one", "line two"])
        self.assertEqual(turns2[-1]["reply"], ["partial"])

    def test_deep_offline_really_asks_more(self):
        with tempfile.TemporaryDirectory() as d:
            _, _, turns, _ = self._run(["a", ""] * 5 + ["/stop"], d, depth="deep")
        self.assertEqual(len(turns), 5)
        self.assertTrue(all(t["topic"] == "intro" for t in turns))

    def test_ctrl_c_during_llm_follow_up_finishes_cleanly(self):
        def interrupt(prompt):
            if "Ask ONE short" in prompt:
                raise KeyboardInterrupt
            return "You are Alex."
        with tempfile.TemporaryDirectory() as d:
            rc, out, turns, card = self._run(["alex", ""], d, depth="standard",
                                             access=(FakeClient(interrupt), "m", "llm"))
        self.assertEqual(rc, 0)
        self.assertEqual(len(turns), 1)
        self.assertEqual(card, "You are Alex.")

    def test_empty_llm_card_falls_back(self):
        from doppelganger import interview
        with tempfile.TemporaryDirectory() as d:
            path, card_path = os.path.join(d, "e.jsonl"), os.path.join(d, "p.md")
            iv.save_turn(iv.TOPICS[0], iv.TOPICS[0].question, ["i'm alex"], path)
            card, out = _quiet(interview.make_card, path, card_path, FakeClient("```\n```"), "m")
        self.assertIn("i'm alex", card)
        self.assertIn("empty card", out)

    def test_story_only_makes_no_card_and_card_flag_explains(self):
        from doppelganger import interview
        story = next(t for t in iv.TOPICS if t.id == "story")
        with tempfile.TemporaryDirectory() as d:
            path, card_path = os.path.join(d, "e.jsonl"), os.path.join(d, "p.md")
            iv.save_turn(story, story.question, ["went hiking"], path)
            card, _ = _quiet(interview.make_card, path, card_path)
            self.assertIsNone(card)
            self.assertFalse(os.path.exists(card_path))
            with mock.patch.dict(os.environ, {"LLM_MODEL": ""}):
                rc, out = _quiet(interview.build_card, False, os.path.join(d, "none.jsonl"), card_path)
        self.assertEqual(rc, 1)
        self.assertIn("No interview answers", out)

    def test_follow_ups_keep_their_context_in_training_data(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "e.jsonl")
            t = iv.TOPICS[4]  # interests
            iv.save_turn(t, t.question, ["bouldering"], path)
            iv.save_turn(t, "How did you get into that?", ["a friend dragged me along"], path)
            iv.save_turn(iv.TOPICS[5], iv.TOPICS[5].question, ["tabs > spaces"], path)
            msgs, _ = _quiet(get_adapter("elicited").parse, path)
        samples = [[x["text"] for x in s] for s in core.build_samples(msgs)]
        self.assertIn([t.question, "bouldering", "How did you get into that?", "a friend dragged me along"],
                      samples)
        self.assertEqual(len(samples), 2)


class CardTest(unittest.TestCase):
    def _turns(self, d):
        path = os.path.join(d, "e.jsonl")
        for i, t in enumerate(iv.TOPICS[:4]):
            iv.save_turn(t, t.question, [f"lol answer {i}", "second line"], path)
        return path, iv.load_turns(path)

    def test_fallback_card_has_answers_and_measured_style(self):
        with tempfile.TemporaryDirectory() as d:
            path, turns = self._turns(d)
            card = iv.fallback_card(turns, drills_path=path)
        self.assertIn("lol answer 0 / second line", card)
        self.assertIn("## How they text", card)
        self.assertIn("lowercase", card)
        self.assertIn("lol / haha", card)
        self.assertIn("without inventing specifics", card)

    def test_llm_card_prompt_guards_against_invention_and_strips_fences(self):
        client = FakeClient("```markdown\nYou are Alex.\n```")
        with tempfile.TemporaryDirectory() as d:
            path, turns = self._turns(d)
            card = iv.llm_card(turns, client, "m", drills_path=path)
        self.assertEqual(card, "You are Alex.\n")
        self.assertIn("Never invent", client.prompts[0])
        self.assertIn("lol answer 3", client.prompts[0])
        self.assertIn("Measured texting habits", client.prompts[0])

    def test_card_is_a_profile_not_a_record_of_events(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "e.jsonl")
            story = next(t for t in iv.TOPICS if t.id == "story")
            iv.save_turn(iv.TOPICS[0], iv.TOPICS[0].question, ["i'm alex"], path)
            iv.save_turn(story, story.question, ["went hiking and got lost lol"], path)
            turns = iv.load_turns(path)
            card = iv.fallback_card(turns, drills_path=path)
            client = FakeClient("You are Alex.")
            iv.llm_card(turns, client, "m", drills_path=path)
        self.assertIn("i'm alex", card)
        self.assertNotIn("hiking", card)
        self.assertNotIn("hiking", client.prompts[0])
        self.assertIn("Don't list specific past events", client.prompts[0])

    def test_style_needs_enough_text(self):
        self.assertEqual(iv.style_section(["hi", "yo"]), [])


class AdapterTest(unittest.TestCase):
    def test_interview_answers_become_training_pairs(self):
        from ingest import drills
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "e.jsonl")
            iv.save_turn(iv.TOPICS[1], iv.TOPICS[1].question, ["backend dev", "lots of bugs"], path)
            drills.save_answer(drills.load_library().scenarios[0], ["sure"], path)
            msgs, out = _quiet(get_adapter("elicited").parse, path)
        samples = core.build_samples(msgs)
        self.assertEqual(len(samples), 2)
        self.assertIn([iv.TOPICS[1].question, "backend dev\nlots of bugs"], [[t["text"] for t in s] for s in samples])
        self.assertIn("1 drill answer(s) and 1 interview answer(s)", out)


class WiringTest(unittest.TestCase):
    def test_chat_passes_persona_card(self):
        from doppelganger import steps
        with tempfile.TemporaryDirectory() as d:
            out_dir = os.path.join(d, "saves")
            os.makedirs(out_dir)
            open(os.path.join(out_dir, "adapter_model.safetensors"), "w").close()
            card = os.path.join(d, "persona.md")
            with open(card, "w") as f:
                f.write("You are Alex.")
            calls = []
            with mock.patch.object(steps, "_read_yaml", return_value={"output_dir": out_dir,
                                                                      "model_name_or_path": "m"}), \
                    mock.patch.object(steps.os.path, "exists", side_effect=lambda p: True), \
                    mock.patch.object(steps, "_llamafactory", side_effect=lambda a, gpus=None: calls.append(a) or 0), \
                    mock.patch.object(iv, "PERSONA_PATH", card):
                _quiet(steps.chat)
                _quiet(steps.chat, persona=False)
        self.assertIn("--default_system=You are Alex.", calls[0])  # one token: safe if it starts with "-"
        self.assertFalse(any(a.startswith("--default_system") for a in calls[1]))

    def test_eval_generate_persona_flag(self):
        from doppelganger import eval_menu
        args = SimpleNamespace(eval_command="generate", label="persona", base_url="u", model="m",
                               limit=None, system_prompt=None, persona=True)
        with mock.patch.object(iv, "PERSONA_PATH", "/nonexistent/persona.md"):
            rc, out = _quiet(eval_menu.dispatch, args)
        self.assertEqual(rc, 1)
        self.assertIn("doppelganger interview", out)
        with tempfile.TemporaryDirectory() as d:
            card = os.path.join(d, "persona.md")
            with open(card, "w") as f:
                f.write("You are Alex.")
            with mock.patch.object(iv, "PERSONA_PATH", card), \
                    mock.patch.object(eval_menu, "do_generate", return_value=0) as gen:
                _quiet(eval_menu.dispatch, args)
        self.assertEqual(gen.call_args[0][4], "You are Alex.")

    def test_cloud_llm_refused_by_default(self):
        from doppelganger import interview
        env = {"LLM_MODEL": "gpt-x", "LLM_API_BASE_URL": "", "LLM_API_KEY": "k"}
        with mock.patch.dict(os.environ, env):
            client, model, note = interview.llm_access(allow_cloud=False)
        self.assertIsNone(client)
        self.assertIn("--allow-cloud", note)


if __name__ == "__main__":
    unittest.main()
