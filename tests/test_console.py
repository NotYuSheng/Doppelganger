"""Output must not crash on a non-UTF-8 stream (Windows redirected output).

    python -m unittest tests.test_console
"""

import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingest import banner, console


def _cp1252():
    raw = io.BytesIO()
    return raw, io.TextIOWrapper(raw, encoding="cp1252")


@mock.patch.dict(os.environ, {"DOPPELGANGER_NO_BANNER": ""})
class Cp1252OutputTest(unittest.TestCase):
    def test_banner_needs_the_fix(self):
        _, out = _cp1252()
        with mock.patch.object(sys, "stdout", out), mock.patch.object(console.os, "name", "posix"), \
                self.assertRaises(UnicodeEncodeError):
            banner.print_banner()   # fix inactive (not Windows): the art can't be encoded
            out.flush()

    @mock.patch.object(console.os, "name", "nt")
    def test_windows_switches_to_utf8(self):
        raw, out = _cp1252()
        _, err = _cp1252()
        with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
            banner.print_banner()   # calls use_utf8_output itself
            print("❯ ◉ ✓ → …")
            out.flush()
            self.assertEqual(err.errors, "backslashreplace")  # stderr keeps escapes
        self.assertIn("❯ ◉ ✓ → …".encode("utf-8"), raw.getvalue())

    @mock.patch.object(console.os, "name", "nt")
    def test_cli_entry_point_survives_cp1252(self):
        from ingest import cli
        _, out = _cp1252()
        _, err = _cp1252()
        with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err), \
                mock.patch.object(cli, "_load_dotenv"):   # don't load the developer's .env
            self.assertEqual(cli.main(["--source", "nope"]), 2)
            out.flush()

    def test_other_platforms_untouched(self):
        _, out = _cp1252()
        _, err = _cp1252()
        with mock.patch.object(console.os, "name", "posix"), \
                mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
            console.use_utf8_output()
        self.assertEqual((out.encoding, err.encoding), ("cp1252", "cp1252"))

    @mock.patch.object(console.os, "name", "nt")
    def test_utf8_and_plain_streams_left_alone(self):
        utf8 = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict")
        plain = io.StringIO()  # no reconfigure()
        with mock.patch.object(sys, "stdout", utf8), mock.patch.object(sys, "stderr", plain):
            console.use_utf8_output()
        self.assertEqual(utf8.errors, "strict")  # not reconfigured


if __name__ == "__main__":
    unittest.main()
