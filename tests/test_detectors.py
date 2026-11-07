"""Contract tests for every registered sensitive-data detector.

    python -m unittest tests.test_detectors

Each detector declares ``examples`` it must catch and ``counter_examples`` it
must not. This file checks all of them, so a new country pack is tested the
moment it's added, with no test code of its own required. It also checks the
metadata every detector must carry, and the locale-selection rules.
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingest import redaction
from ingest.redaction import checksums


def _hits(detector, text):
    return [f for f in redaction.scan_text(text, None) if f.detector == detector.name]


class EveryDetectorTest(unittest.TestCase):
    def test_examples_and_counter_examples(self):
        for d in redaction._REGISTRY:
            with self.subTest(detector=d.name):
                self.assertTrue(d.examples, "every detector needs at least one example")
                self.assertTrue(d.counter_examples, "every detector needs at least one counter-example")
                for ex in d.examples:
                    self.assertTrue(_hits(d, ex), f"should detect: {ex!r}")
                for ex in d.counter_examples:
                    self.assertFalse(_hits(d, ex), f"should NOT detect: {ex!r}")

    def test_metadata(self):
        for d in redaction._REGISTRY:
            with self.subTest(detector=d.name):
                self.assertIn(d.precision, redaction.PRECISIONS)
                self.assertIn(d.severity, redaction.SEVERITIES)
                self.assertTrue(d.description, "describe what it detects")
                self.assertTrue(d.reference, "cite where the format/checksum is specified")
                self.assertTrue(d.locale == redaction.UNIVERSAL or re.fullmatch(r"[A-Z]{2}", d.locale),
                                "locale is 'universal' or an ISO 3166-1 alpha-2 code")
                if d.locale != redaction.UNIVERSAL:
                    self.assertTrue(d.name.startswith(d.locale.lower() + "_"),
                                    "country detectors are named <cc>_<what>")

    def test_high_precision_country_detectors_are_validated(self):
        # "high" outside universal means it runs for every user by default, so
        # it must be backed by a validator (checksum / date / code rules).
        for d in redaction._REGISTRY:
            if d.locale != redaction.UNIVERSAL and d.precision == "high":
                with self.subTest(detector=d.name):
                    self.assertIsNotNone(d.validator)

    def test_registry_rejects_bad_metadata(self):
        with self.assertRaises(ValueError):
            redaction.make("tmp_bad", "TMP", "XX", r"x", precision="maybe")
        with self.assertRaises(ValueError):
            redaction.make("tmp_bad2", "TMP", "XX", r"x", severity="extreme")


class LocaleSelectionTest(unittest.TestCase):
    def _names(self, locales):
        return {d.name for d in redaction.iter_detectors(locales)}

    def test_auto_runs_high_precision_everywhere(self):
        names = self._names(["auto"])
        self.assertIn("email", names)            # universal
        self.assertIn("sg_nric", names)          # high, SG
        self.assertIn("cn_resident_id", names)   # high, CN
        self.assertNotIn("sg_phone", names)      # standard, SG not selected
        self.assertNotIn("us_ssn", names)        # standard, US not selected

    def test_country_codes_add_all_of_that_country(self):
        names = self._names(["auto", "us"])      # case-insensitive
        self.assertIn("us_ssn", names)
        self.assertNotIn("sg_phone", names)

    def test_plain_country_list_keeps_old_behaviour(self):
        names = self._names(["SG"])
        self.assertIn("sg_phone", names)
        self.assertNotIn("cn_resident_id", names)

    def test_all_none_and_empty(self):
        self.assertEqual(self._names(["all"]), {d.name for d in redaction._REGISTRY})
        universal = {d.name for d in redaction._REGISTRY if d.locale == redaction.UNIVERSAL}
        self.assertEqual(self._names(["none"]), universal)
        self.assertEqual(self._names([]), universal)

    def test_parse_locales(self):
        self.assertEqual(redaction.parse_locales(None), ["auto"])
        self.assertEqual(redaction.parse_locales(" "), ["auto"])
        self.assertEqual(redaction.parse_locales("auto, SG ,US"), ["auto", "SG", "US"])

    def test_country_hint_only_without_countries(self):
        self.assertIn("--redact-locales auto,SG", redaction.country_hint(["auto"]))
        self.assertIsNotNone(redaction.country_hint(["none"]))
        self.assertIsNone(redaction.country_hint(["auto", "SG"]))
        self.assertIsNone(redaction.country_hint(["all"]))

    def test_summary_prints_hint(self):
        import io
        from contextlib import redirect_stdout
        from ingest import redactor
        buf = io.StringIO()
        with redirect_stdout(buf):
            redactor.print_summary(redactor.scan_samples([[{"role": "user", "text": "hi"}]]), "r.json")
        self.assertIn("only checked for the countries you choose", buf.getvalue())

    def test_redactor_default_is_auto(self):
        from ingest import redactor
        report = redactor.scan_samples([[{"role": "user", "text": "fin M1234567K, call 9123 4567"}]])
        cats = {f["category"] for f in report["findings"]}
        self.assertIn("NRIC/FIN", cats)      # high precision: on by default
        self.assertNotIn("PHONE", cats)      # SG phone needs SG selected
        self.assertEqual(report["locales"], ["auto"])


class ChecksumTest(unittest.TestCase):
    def test_luhn(self):
        self.assertTrue(checksums.luhn_valid("4111111111111111"))
        self.assertFalse(checksums.luhn_valid("4111111111111112"))
        self.assertTrue(checksums.luhn_valid("130421985", 9, 9))

    def test_iban(self):
        self.assertTrue(checksums.iban_valid("GB82 WEST 1234 5698 7654 32"))
        self.assertFalse(checksums.iban_valid("GB82 WEST 1234 5698 7654 33"))
        self.assertFalse(checksums.iban_valid("GB82"))

    def test_verhoeff(self):
        self.assertTrue(checksums.verhoeff_valid("2363"))   # textbook vector
        self.assertFalse(checksums.verhoeff_valid("2364"))

    def test_sg_m_series(self):
        from ingest.redaction.sg import nric_valid
        self.assertTrue(nric_valid("M1234567K"))   # GovTech FormSG test vector
        self.assertFalse(nric_valid("M1234567L"))
        self.assertTrue(nric_valid("S0000001I"))   # existing series unchanged
        self.assertFalse(nric_valid("S0000001A"))


if __name__ == "__main__":
    unittest.main()
