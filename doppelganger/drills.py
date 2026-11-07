"""Interactive reply-drill sessions (``python -m doppelganger drills``).

Shows realistic incoming messages and records how you'd actually reply. See
:mod:`ingest.drills` for the scenario library, storage and coverage logic.
"""

import random
from typing import Callable, List, Optional

from ingest import drills

from doppelganger import tui

BOLD, DIM, AMBER, RESET = tui.BOLD, tui.DIM, tui.AMBER, tui.RESET

_HOWTO = (
    "Type how you'd actually reply. Press Enter on an empty line to send.\n"
    "Several lines become several messages, like texting. An empty first line\n"
    "skips the drill; /stop ends the session (everything answered is saved)."
)


def _read_reply(input_fn: Callable[[str], str]) -> Optional[List[str]]:
    """Lines of a reply until an empty line. [] = skip, None = stop."""
    lines: List[str] = []
    while True:
        try:
            line = input_fn(f"{AMBER}you ›{RESET} " if not lines else f"{DIM}    ›{RESET} ")
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if line.strip() == "/stop":
            return None
        if not line.strip():
            return lines
        lines.append(line.rstrip())


def run_session(
    count: Optional[int] = None,
    situation: Optional[str] = None,
    path: str = drills.DEFAULT_PATH,
    ui=tui,
    input_fn: Callable[[str], str] = input,
    rng: Optional[random.Random] = None,
) -> int:
    """Run one drill session. Returns 0, or 2 for an unknown situation."""
    library = drills.load_library()
    if situation and situation not in library.situations:
        print(f"error: unknown situation {situation!r}. Choose from: {', '.join(library.situations)}")
        return 2
    answers = drills.load_answers(path)
    print(f"\n{BOLD}Reply drills{RESET}  {DIM}create training data by replying to realistic messages{RESET}")
    print(f"{DIM}{len(answers)} answered so far · saved to {path}{RESET}")

    if count is None:
        sizes = [5, 10, 20]
        pick = ui.select("How many drills this session?", [f"{n} drills" for n in sizes],
                         hints=["about 2 minutes", "about 5 minutes", "about 10 minutes"])
        if pick is None:
            return 0
        count = sizes[pick]

    batch = drills.next_scenarios(library, answers, count, situation=situation, rng=rng)
    print(f"\n{_HOWTO}")
    saved = skipped = 0
    for i, scenario in enumerate(batch, 1):
        who = library.audiences[scenario.audience]
        what = library.situations[scenario.situation]
        print(f"\n{DIM}[{i}/{len(batch)}] {what}{RESET}")
        print(f"{BOLD}{who} messages you:{RESET}")
        print(f"  {AMBER}│{RESET} {scenario.message}")
        reply = _read_reply(input_fn)
        if reply is None:
            break
        if not reply:
            skipped += 1
            continue
        drills.save_answer(scenario, reply, path)
        saved += 1

    total = drills.load_answers(path)
    print(f"\nSaved {saved} answer(s)" + (f", skipped {skipped}" if skipped else "") + ".")
    print(drills.coverage_report(total, library))
    if saved:
        print(f"\nNext: run {BOLD}parse{RESET} (or auto). Your answers in {path} are picked up "
              "automatically, alongside any chat exports.")
    return 0


def report(path: str = drills.DEFAULT_PATH) -> int:
    print(drills.coverage_report(drills.load_answers(path), drills.load_library()))
    return 0
