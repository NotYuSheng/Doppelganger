"""Tests for the optional NER redaction tier (issue #13).

    python -m unittest tests.test_ner

A stub recogniser stands in for spaCy, so these run without the optional
dependency. One test uses real spaCy and is skipped when it isn't installed.
"""

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingest import ner, redactor


def _quiet(fn, *a, **kw):
    with redirect_stdout(io.StringIO()) as out:
        result = fn(*a, **kw)
    return result, out.getvalue()


def stub(entities):
    """A recogniser that labels each given word wherever it appears."""
    def recognise(text):
        spans = []
        for word, label in entities.items():
            start = text.find(word)
            while start >= 0:
                spans.append((start, start + len(word), label))
                start = text.find(word, start + len(word))
        return spans
    return recognise


SAMPLES = [[
    {"role": "user", "text": "Jamie Wong said Alex should visit Singapore with Acme Corp folks"},
    {"role": "assistant", "text": "lol ya, Alex Tan here. tell Jamie Wong I said hi"},
]]
ENTS = {"Jamie Wong": "PERSON", "Alex Tan": "PERSON", "Alex": "PERSON", "Singapore": "GPE",
        "Acme Corp": "ORG", "ya": "PERSON", "Friday": "DATE"}


class ScanTest(unittest.TestCase):
    def test_categories_allowlist_and_chat_words(self):
        findings = ner.ner_scan_samples(SAMPLES, stub(ENTS), allow=["Alex Tan"])
        got = sorted((f["turn"], f["category"], f["preview"]) for f in findings)
        cats = [c for _, c, _ in got]
        self.assertEqual(cats.count("PERSON"), 2)     # Jamie Wong twice; never Alex / Alex Tan / "ya"
        self.assertIn("LOCATION", cats)
        self.assertIn("ORG", cats)
        self.assertNotIn("DATE", cats)
        self.assertTrue(all(f["detector"] == "ner" and "start" in f for f in findings))

    def test_allowlist_words(self):
        allowed = ner.allowlist(["Alex Tan", "  ", "J"])
        self.assertTrue({"alex tan", "alex", "tan"} <= allowed)
        self.assertNotIn("j", allowed)

    def test_existing_placeholders_skipped(self):
        findings = ner.ner_scan_samples([[{"role": "user", "text": "mail [EMAIL] now"}]],
                                        stub({"[EMAIL]": "ORG"}))
        self.assertEqual(findings, [])


class ApplyTest(unittest.TestCase):
    def test_replace_and_regex_label_wins_on_overlap(self):
        samples = [[{"role": "user", "text": "Jamie Wong, my nric S0000001I"}]]
        findings = ner.ner_scan_samples(samples, stub({"Jamie Wong": "PERSON", "nric S0000001I": "PERSON"}))
        out = redactor.apply(samples, "replace", locales=["SG"], llm_findings=findings)
        self.assertEqual(out[0][0]["text"], "[PERSON], my [NRIC/FIN]")

    def test_merge_findings_keeps_detector(self):
        report = redactor.scan_samples([[{"role": "user", "text": "hi"}]])
        findings = ner.ner_scan_samples([[{"role": "user", "text": "Jamie Wong"}]],
                                        stub({"Jamie Wong": "PERSON"}))
        redactor.merge_findings(report, findings)
        self.assertEqual(report["findings"][0]["detector"], "ner")
        self.assertEqual(report["summary"]["PERSON"]["hits"], 1)
        self.assertNotIn("Jamie", json.dumps(report))   # previews are masked

    def test_unavailable_explains_and_skips(self):
        with mock.patch.object(ner, "load_spacy", side_effect=ner.NerUnavailable("install spaCy")):
            findings, out = _quiet(redactor.run_ner, SAMPLES, "en_core_web_sm", [])
        self.assertEqual(findings, [])
        self.assertIn("install spaCy", out)
        self.assertIn("Skipping NER", out)


class WiringTest(unittest.TestCase):
    def setUp(self):
        os.environ["LLM_VALIDATE"] = "false"

    def test_ingest_cli_redacts_names_but_not_yours(self):
        from ingest.cli import main
        export = {"personal_information": {"first_name": "Alex", "last_name": "Tan"},
                  "chats": {"list": [{"id": 1, "messages": [
                      {"id": 1, "type": "message", "date_unixtime": "100", "from": "Jamie",
                       "text": "Priya Nair is coming too"},
                      {"id": 2, "type": "message", "date_unixtime": "110", "from": "Alex Tan",
                       "text": "nice, Alex Tan approves"}]}]}}
        with tempfile.TemporaryDirectory() as d:
            inp, out = os.path.join(d, "result.json"), os.path.join(d, "out.json")
            with open(inp, "w") as f:
                json.dump(export, f)
            with mock.patch.object(ner, "load_spacy",
                                   return_value=stub({"Priya Nair": "PERSON", "Alex Tan": "PERSON"})):
                rc, _ = _quiet(main, ["--input", inp, "--output", out, "--ner-redact",
                                      "--redact", "replace", "--skip-validation"])
            with open(out) as f:
                texts = [t["value"] for t in json.load(f)[0]["conversations"]]
        self.assertEqual(rc, 0)
        self.assertEqual(texts, ["[PERSON] is coming too", "nice, Alex Tan approves"])

    def test_runner_audit_uses_names_saved_by_parse(self):
        from doppelganger import steps
        from ingest import sharegpt
        with tempfile.TemporaryDirectory() as d:
            paths = {"RAW_PATH": "raw.jsonl", "DATASET_PATH": "ds.json", "REPORT_PATH": "r.json",
                     "SELF_NAMES_PATH": "self.json"}
            patches = [mock.patch.object(steps, k, os.path.join(d, v)) for k, v in paths.items()]
            for p in patches:
                p.start()
            try:
                sharegpt.write_jsonl([[{"role": "user", "text": "Priya says hi to Sam Lee"},
                                       {"role": "assistant", "text": "Sam Lee says hi back"}]], steps.RAW_PATH)
                steps._save_self_names(["Sam Lee"])
                with mock.patch.object(ner, "load_spacy",
                                       return_value=stub({"Priya": "PERSON", "Sam Lee": "PERSON"})):
                    _quiet(steps.audit, validate=False, ner=True, holdout=0)
                with open(steps.DATASET_PATH) as f:
                    texts = [t["value"] for t in json.load(f)[0]["conversations"]]
            finally:
                for p in patches:
                    p.stop()
        self.assertEqual(texts, ["[PERSON] says hi to Sam Lee", "Sam Lee says hi back"])

    def test_bad_flag_combination_still_parses(self):
        from doppelganger.__main__ import build_parser
        args = build_parser().parse_args(["auto", "--ner-redact", "--ner-model", "xx_ent_wiki_sm",
                                          "--ner-allow", "Bobby"])
        self.assertEqual((args.ner_redact, args.ner_model, args.ner_allow), (True, "xx_ent_wiki_sm", ["Bobby"]))


@unittest.skipUnless(importlib.util.find_spec("spacy"), "spaCy not installed (optional)")
class RealSpacyTest(unittest.TestCase):
    def test_english_model(self):
        try:
            recognise = ner.load_spacy("en_core_web_sm")
        except ner.NerUnavailable:
            self.skipTest("en_core_web_sm not downloaded")
        findings = ner.ner_scan_samples(
            [[{"role": "user", "text": "Jamie Wong moved to Singapore, see ya"}]], recognise, allow=[])
        cats = {f["category"] for f in findings}
        self.assertIn("PERSON", cats)
        self.assertIn("LOCATION", cats)
        self.assertEqual(len([f for f in findings if f["category"] == "PERSON"]), 1)  # not "ya"


if __name__ == "__main__":
    unittest.main()
