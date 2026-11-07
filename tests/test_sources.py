"""Tests for combining several exports into one dataset (ingest/sources.py).

    python -m unittest tests.test_sources
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingest import sources

SELF_TG = "Yu Sheng"     # name on Telegram (from personal_information)
SELF_WA = "YS"           # name in WhatsApp exports


def _telegram(d, name="result.json", chat_id=1):
    path = os.path.join(d, name)
    msgs = [
        {"id": 1, "type": "message", "date_unixtime": "1000", "from": "Alice", "text": "tg hi"},
        {"id": 2, "type": "message", "date_unixtime": "1010", "from": SELF_TG, "text": "tg hello"},
    ]
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"personal_information": {"first_name": "Yu", "last_name": "Sheng"},
                   "chats": {"list": [{"id": chat_id, "messages": msgs}]}}, f)
    return path


def _whatsapp(d, name="WhatsApp Chat with Bob.txt", partner="Bob"):
    path = os.path.join(d, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"13/03/2024, 10:00 - {partner}: wa hi\n13/03/2024, 10:01 - {SELF_WA}: wa hello\n")
    return path


class ResolveSpecsTest(unittest.TestCase):
    def test_plain_input_uses_default_source(self):
        specs = sources.resolve_specs(["data/result.json"], "telegram")
        self.assertEqual([(s.source, s.path, s.self_name) for s in specs],
                         [("telegram", "data/result.json", None)])

    def test_prefixed_inputs_and_per_source_names(self):
        specs = sources.resolve_specs(
            ["telegram:a.json", "whatsapp:b.txt", "c.txt"], "whatsapp",
            ["whatsapp:YS", "Fallback"])
        self.assertEqual([(s.source, s.path, s.self_name) for s in specs], [
            ("telegram", "a.json", "Fallback"),
            ("whatsapp", "b.txt", "YS"),
            ("whatsapp", "c.txt", "YS"),
        ])

    def test_colon_in_path_or_name_without_known_source(self):
        specs = sources.resolve_specs([r"C:\exports\result.json"], "telegram", ["Dr: Who"])
        self.assertEqual((specs[0].source, specs[0].path, specs[0].self_name),
                         ("telegram", r"C:\exports\result.json", "Dr: Who"))

    def test_source_prefix_is_case_insensitive(self):
        self.assertEqual(sources.resolve_specs(["WhatsApp:x.txt"], "telegram")[0].source, "whatsapp")

    def test_accepts_str_none_and_default_input(self):
        self.assertEqual(sources.resolve_specs(None, "telegram")[0].path, sources.DEFAULT_INPUT)
        spec = sources.resolve_specs("x.txt", "whatsapp", "Me")[0]
        self.assertEqual((spec.path, spec.self_names), ("x.txt", ["Me"]))

    def test_duplicate_inputs_skipped(self):
        specs = sources.resolve_specs(["whatsapp:x.txt", "whatsapp:./x.txt"], "telegram")
        self.assertEqual(len(specs), 1)

    def test_unknown_default_source_raises(self):
        with self.assertRaises(ValueError):
            sources.resolve_specs(["x"], "myspace")

    def test_self_name_for_unused_source_raises(self):
        with self.assertRaises(ValueError) as ctx:
            sources.resolve_specs(["telegram:a.json"], "telegram", ["signal:Me"])
        self.assertIn("signal", str(ctx.exception))


class LoadSamplesTest(unittest.TestCase):
    def test_combines_sources_with_summary(self):
        with tempfile.TemporaryDirectory() as d:
            specs = sources.resolve_specs(
                [f"telegram:{_telegram(d)}", f"whatsapp:{_whatsapp(d)}"], "telegram",
                [f"whatsapp:{SELF_WA}"])
            samples, results = sources.load_samples(specs)
        self.assertEqual(len(samples), 2)
        self.assertEqual([(r.spec.source, r.messages, r.samples) for r in results],
                         [("telegram", 2, 1), ("whatsapp", 2, 1)])
        blob = json.dumps(samples)
        self.assertIn("tg hello", blob)
        self.assertIn("wa hello", blob)

    def test_same_chat_id_in_two_exports_not_merged(self):
        # Two Telegram exports whose chats share id 1 and overlap in time must
        # stay separate conversations.
        with tempfile.TemporaryDirectory() as d:
            a, b = _telegram(d, "a.json"), _telegram(d, "b.json")
            specs = sources.resolve_specs([a, b], "telegram")
            samples, _ = sources.load_samples(specs)
        self.assertEqual(len(samples), 2)

    def test_single_export_chat_ids_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            specs = sources.resolve_specs([_telegram(d)], "telegram")
            from ingest.adapters import get_adapter
            before = get_adapter("telegram").parse(specs[0].path)
            samples, results = sources.load_samples(specs)
        self.assertEqual(before[0].chat_id, "1")
        self.assertEqual(len(samples), 1)

    def test_errors_name_the_failing_export(self):
        with tempfile.TemporaryDirectory() as d:
            specs = sources.resolve_specs([f"whatsapp:{_whatsapp(d)}"], "telegram")  # no self name
            with self.assertRaises(sources.SourceLoadError) as ctx:
                sources.load_samples(specs)
        self.assertIn("[whatsapp:", str(ctx.exception))
        self.assertIn("--self-name", str(ctx.exception))
        self.assertIsInstance(ctx.exception.__cause__, ValueError)

    def test_multi_arg_exceptions_are_wrapped_not_broken(self):
        # JSONDecodeError / UnicodeDecodeError can't be rebuilt from one string;
        # they must come through as SourceLoadError with the original chained.
        with tempfile.TemporaryDirectory() as d:
            bad_json = os.path.join(d, "result.json")
            with open(bad_json, "w") as f:
                f.write('{"chats": ')
            bad_txt = os.path.join(d, "WhatsApp Chat with X.txt")
            with open(bad_txt, "wb") as f:
                f.write(b"13/03/2024, 10:00 - X: \xff\xfe bad bytes\n")
            for spec_input, cause in ((bad_json, json.JSONDecodeError),
                                      (f"whatsapp:{bad_txt}", UnicodeDecodeError)):
                specs = sources.resolve_specs([spec_input], "telegram", ["whatsapp:YS"]
                                              if spec_input.startswith("whatsapp") else [])
                with self.assertRaises(sources.SourceLoadError) as ctx:
                    sources.load_samples(specs)
                self.assertIsInstance(ctx.exception.__cause__, cause)

    def test_two_exports_same_app_different_names(self):
        with tempfile.TemporaryDirectory() as d:
            a = os.path.join(d, "a.txt")
            with open(a, "w", encoding="utf-8") as f:
                f.write("13/03/2024, 10:00 - Bob: hi\n13/03/2024, 10:01 - Yu: hey\n")
            b = os.path.join(d, "b.txt")
            with open(b, "w", encoding="utf-8") as f:
                f.write("13/03/2024, 11:00 - Cat: yo\n13/03/2024, 11:01 - Yu Sheng: sup\n")
            specs = sources.resolve_specs([f"whatsapp:{a}", f"whatsapp:{b}"], "telegram",
                                          ["whatsapp:Yu", "whatsapp:Yu Sheng"])
            self.assertEqual(specs[0].self_names, ["Yu", "Yu Sheng"])
            samples, results = sources.load_samples(specs)
        self.assertEqual([r.samples for r in results], [1, 1])


class CliTest(unittest.TestCase):
    def setUp(self):
        os.environ["LLM_VALIDATE"] = "false"

    def test_ingest_cli_multi_source(self):
        from ingest.cli import main
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "out.json")
            rc = main(["--input", f"telegram:{_telegram(d)}", "--input", f"whatsapp:{_whatsapp(d)}",
                       "--self-name", f"whatsapp:{SELF_WA}", "--output", out, "--skip-redact-scan"])
            self.assertEqual(rc, 0)
            with open(out, encoding="utf-8") as f:
                data = json.load(f)
        self.assertEqual(len(data), 2)

    def test_ingest_cli_single_source_form_unchanged(self):
        from ingest.cli import main
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "out.json")
            rc = main(["--source", "whatsapp", "--input", _whatsapp(d), "--self-name", SELF_WA,
                       "--output", out, "--skip-redact-scan"])
            self.assertEqual(rc, 0)

    def test_ingest_cli_bad_self_name_source_exit_code(self):
        from ingest.cli import main
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(main(["--input", _telegram(d), "--self-name", "signal:Me"]), 2)

    def test_runner_parse_multi_source(self):
        from doppelganger import steps
        with tempfile.TemporaryDirectory() as d:
            raw = os.path.join(d, "raw.jsonl")
            orig = (steps.DATA_DIR, steps.RAW_PATH, steps.REPORT_PATH)
            steps.DATA_DIR, steps.RAW_PATH, steps.REPORT_PATH = d, raw, os.path.join(d, "r.json")
            try:
                rc = steps.parse(input_path=[f"telegram:{_telegram(d)}", f"whatsapp:{_whatsapp(d)}"],
                                 self_name=[f"whatsapp:{SELF_WA}"], scan=False)
            finally:
                steps.DATA_DIR, steps.RAW_PATH, steps.REPORT_PATH = orig
            self.assertEqual(rc, 0)
            with open(raw, encoding="utf-8") as f:
                self.assertEqual(sum(1 for _ in f), 2)

    def test_runner_parse_missing_input(self):
        from doppelganger import steps
        self.assertEqual(steps.parse(input_path=["whatsapp:/nonexistent.txt"], scan=False), 1)


if __name__ == "__main__":
    unittest.main()
