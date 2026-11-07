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


def choose_level(library, answers, ui=tui) -> Optional[str]:
    """Ask how comprehensive this session should be, with time estimates."""
    options, hints, ids = [], [], []
    for level_id, label, desc in drills.LEVELS:
        left = len(drills.plan_level(library, answers, level_id, rng=random.Random(0)))
        options.append(f"{label:<9}")
        hints.append(f"{desc} · " + ("done ✓" if not left else f"{left} to go, {drills.estimate(left)}"))
        ids.append(level_id)
    pick = ui.select("How comprehensive?  (you can stop any time; answers are saved)",
                     options, hints=hints)
    return None if pick is None else ids[pick]


def run_session(
    count: Optional[int] = None,
    situation: Optional[str] = None,
    level: Optional[str] = None,
    path: str = drills.DEFAULT_PATH,
    ui=tui,
    input_fn: Callable[[str], str] = input,
    rng: Optional[random.Random] = None,
) -> int:
    """Run one drill session. Returns 0, or 2 for an unknown situation/level.

    ``count`` / ``situation`` pick a fixed number of least-covered drills;
    otherwise the session works towards a coverage ``level`` (asked if not given).
    """
    library = drills.load_library()
    if situation and situation not in library.situations:
        print(f"error: unknown situation {situation!r}. Choose from: {', '.join(library.situations)}")
        return 2
    if level and level not in {l[0] for l in drills.LEVELS}:
        print(f"error: unknown level {level!r}. Choose from: {', '.join(l[0] for l in drills.LEVELS)}")
        return 2
    answers = drills.load_answers(path)
    total = len(library.scenarios)
    print(f"\n{BOLD}Reply drills{RESET}  {DIM}create training data by replying to realistic messages{RESET}")
    print(f"{DIM}{len(answers)} of {total} answered so far · the full library takes "
          f"{drills.estimate(total)} · saved to {path}{RESET}")

    if count is not None or situation:
        batch = drills.next_scenarios(library, answers, count or 10, situation=situation, rng=rng)
    else:
        if level is None:
            level = choose_level(library, answers, ui)
            if level is None:
                return 0
        batch = drills.plan_level(library, answers, level, rng=rng)
        if not batch:
            print(f"\nNothing left for this level, you've covered it. Pick a bigger level for more.")
            print(drills.coverage_report(answers, library))
            return 0

    print(f"\n{len(batch)} drill(s), about {drills.estimate(len(batch))}.\n{_HOWTO}")
    saved = skipped = 0
    for i, scenario in enumerate(batch, 1):
        who = library.audiences[scenario.audience]
        what = library.situations[scenario.situation]
        left = len(batch) - i + 1
        print(f"\n{DIM}[{i}/{len(batch)}] {what} · {drills.estimate(left)} left{RESET}")
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

    all_answers = drills.load_answers(path)
    print(f"\nSaved {saved} answer(s)" + (f", skipped {skipped}" if skipped else "") + ".")
    print(drills.coverage_report(all_answers, library))
    if saved:
        print(f"\nNext: run {BOLD}parse{RESET} (or auto). Your answers in {path} are picked up "
              "automatically, alongside any chat exports.")
    return 0


def report(path: str = drills.DEFAULT_PATH) -> int:
    print(drills.coverage_report(drills.load_answers(path), drills.load_library()))
    return 0
