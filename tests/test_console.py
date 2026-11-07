"""Output must not crash on a non-UTF-8 stream (Windows redirected output).

    python -m unittest tests.test_console
"""

import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingest import banner
from ingest.console import use_utf8_output


@mock.patch.dict(os.environ, {"DOPPELGANGER_NO_BANNER": ""})
class Cp1252OutputTest(unittest.TestCase):
    def _stream(self):
        raw = io.BytesIO()
        return raw, io.TextIOWrapper(raw, encoding="cp1252")

    def test_banner_crashes_without_the_fix(self):
        _, out = self._stream()
        with mock.patch.object(sys, "stdout", out), self.assertRaises(UnicodeEncodeError):
            banner.print_banner()
            out.flush()

    def test_banner_prints_after_switching_to_utf8(self):
        raw, out = self._stream()
        with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", out):
            use_utf8_output()
            banner.print_banner()
            print("❯ ◉ ✓ → …")
            out.flush()
        self.assertIn("❯ ◉ ✓ → …".encode("utf-8"), raw.getvalue())

    def test_cli_entry_point_survives_cp1252(self):
        from ingest.cli import main
        raw, out = self._stream()
        with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", out):
            self.assertEqual(main(["--source", "nope"]), 2)  # prints the banner first
            out.flush()

    def test_leaves_utf8_and_plain_streams_alone(self):
        s = io.StringIO()  # no reconfigure(): must not fail
        with mock.patch.object(sys, "stdout", s):
            use_utf8_output()


if __name__ == "__main__":
    unittest.main()
