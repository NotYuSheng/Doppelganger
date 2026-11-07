"""Arrow-key menu tests against each platform's real key handling.

    python -m unittest tests.test_tui_terminal

* POSIX (Linux, macOS): the pickers run in a real pseudo-terminal and receive
  the exact bytes a terminal sends for arrow keys, Space and Enter.
* Windows: CI machines have no interactive console, so ``msvcrt.getwch`` is fed
  the exact codes Windows produces (``\\xe0`` + ``H``/``P`` for ↑/↓) and
  the real Windows code path in ``tui._read_key`` decodes them.

The plain numbered fallback is covered in ``tests/test_wizard.py``.
"""

import io
import os
import sys
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from doppelganger import tui  # noqa: E402

UP, DOWN, ENTER, SPACE = "\x1b[A", "\x1b[B", "\r", " "


@unittest.skipIf(os.name == "nt", "POSIX pseudo-terminal test")
class PtyTest(unittest.TestCase):
    """Run a picker in a child process attached to a pseudo-terminal."""

    def _drive(self, code, keys, wait_for, timeout=20):
        import pty
        import select as _select

        env = dict(os.environ, PYTHONPATH=ROOT, TERM="xterm-256color", DOPPELGANGER_NO_BANNER="1")
        env.pop("DOPPELGANGER_PLAIN", None)
        pid, fd = pty.fork()
        if pid == 0:
            os.execvpe(sys.executable, [sys.executable, "-c", code], env)

        output = ""

        def pump(until=None, seconds=timeout):
            nonlocal output
            end = time.time() + seconds
            while time.time() < end:
                if until and until in output:
                    return
                r, _, _ = _select.select([fd], [], [], 0.05)
                if r:
                    try:
                        chunk = os.read(fd, 65536).decode(errors="replace")
                    except OSError:
                        return
                    if not chunk:
                        return
                    output += chunk

        try:
            pump(wait_for)
            self.assertIn(wait_for, output, "picker never appeared")
            for key in keys:
                os.write(fd, key.encode())
                pump(seconds=0.15)
            pump("RESULT=")
        finally:
            status = None
            for _ in range(50):  # give the child up to ~5s to exit, then kill it
                done, status = os.waitpid(pid, os.WNOHANG)
                if done:
                    break
                time.sleep(0.1)
            else:
                os.kill(pid, 9)
                os.waitpid(pid, 0)
            os.close(fd)
        return output, status

    def test_select_with_arrow_keys(self):
        code = ("from doppelganger import tui\n"
                "r = tui.select('Pick one', ['a', 'b', 'c'])\n"
                "print('RESULT=%r' % r)\n")
        out, _ = self._drive(code, [DOWN, DOWN, UP, ENTER], "Pick one")
        self.assertIn("RESULT=1", out)
        self.assertIn("\x1b[?25h", out)  # cursor shown again afterwards

    def test_select_wraps_and_cancels(self):
        code = ("from doppelganger import tui\n"
                "print('RESULT=%r' % tui.select('Pick', ['a', 'b']))\n")
        out, _ = self._drive(code, [UP, ENTER], "Pick")
        self.assertIn("RESULT=1", out)   # up from the first option wraps to the last
        out, _ = self._drive(code, ["q"], "Pick")
        self.assertIn("RESULT=None", out)

    def test_multiselect_select_all_and_action(self):
        code = ("from doppelganger import tui\n"
                "r = tui.multiselect('Tick', ['x', 'y', 'z'], select_all=True, action='Add')\n"
                "print('RESULT=%r' % (r,))\n")
        # Select all ticks everything; move to "y" and untick it.
        out, _ = self._drive(code, [SPACE, DOWN, DOWN, SPACE, ENTER], "Tick")
        self.assertIn("RESULT=[0, 2]", out)
        # The action row is last: up from the top wraps to it.
        out, _ = self._drive(code, [UP, ENTER], "Tick")
        self.assertIn("RESULT='action'", out)

    def test_signal_keys_cancel_instead_of_killing(self):
        # Ctrl-C / Ctrl-\ / Ctrl-Z reach the picker as keys and cancel it; the
        # process exits normally (so the terminal and cursor are restored).
        code = ("from doppelganger import tui\n"
                "print('RESULT=%r' % tui.select('Pick', ['a', 'b']))\n")
        for key in ("\x03", "\x1c", "\x1a"):
            out, status = self._drive(code, [key], "Pick")
            self.assertIn("RESULT=None", out)
            self.assertTrue(os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0)

    def test_full_menu_starts_and_quits(self):
        code = ("import sys\nfrom doppelganger.__main__ import main\n"
                "rc = main([])\nprint('RESULT=%r' % rc)\n")
        out, status = self._drive(code, ["q"], "What would you like to do?")
        self.assertIn("RESULT=0", out)
        self.assertIn("auto", out)


@unittest.skipUnless(os.name == "nt", "Windows console key codes")
class WindowsKeysTest(unittest.TestCase):
    """Feed msvcrt the codes a Windows console produces."""

    def _run(self, fn, keys):
        import msvcrt
        with mock.patch.object(tui, "interactive", return_value=True), \
             mock.patch.object(msvcrt, "getwch", side_effect=list(keys)), \
             redirect_stdout(io.StringIO()):
            return fn()

    def test_arrow_keys(self):
        r = self._run(lambda: tui.select("Pick", ["a", "b", "c"]),
                      ["\xe0", "P", "\xe0", "P", "\xe0", "H", "\r"])  # down, down, up, enter
        self.assertEqual(r, 1)

    def test_numpad_arrow_prefix_and_cancel(self):
        self.assertEqual(self._run(lambda: tui.select("Pick", ["a", "b"]), ["\x00", "P", "\r"]), 1)
        self.assertIsNone(self._run(lambda: tui.select("Pick", ["a", "b"]), ["q"]))

    def test_multiselect(self):
        r = self._run(lambda: tui.multiselect("Tick", ["x", "y", "z"], select_all=True),
                      [" ", "\xe0", "P", "\xe0", "P", " ", "\r"])
        self.assertEqual(r, [0, 2])


class KeyNamesTest(unittest.TestCase):
    """Platform-independent: plain keys map to the same actions everywhere."""

    def test_vim_keys_and_digits(self):
        keys = iter(["j", "j", "k", "\r"])
        with mock.patch.object(tui, "interactive", return_value=True), \
             mock.patch.object(tui, "_read_key", side_effect=lambda: {
                 "j": "down", "k": "up", "\r": "enter"}[next(keys)]), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(tui.select("Pick", ["a", "b", "c"]), 1)


if __name__ == "__main__":
    unittest.main()
