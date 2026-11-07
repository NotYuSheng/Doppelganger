"""Reply drills: deliberately created training data (see issue #14).

Chat exports only contain whatever conversations happened to take place. A
reply drill shows a realistic incoming message from a scenario library and the
user types how they'd actually reply. Each answer becomes a clean training pair:
a real prompt (written by us), the user's own words, and no third parties.

The library (``scenarios.json``) covers situations (advice, plans, apologies,
disagreements, good/bad news, …) × audiences (close friend, family, colleague,
someone you don't know well), so we choose what gets covered instead of
inheriting the narrow mix of a chat log. :func:`next_scenarios` serves the
least-covered situations first.

Answers are appended to ``data/elicited.jsonl`` one per line as soon as they're
given, so a session can stop at any point and resume later. The pipeline reads
that file through the ``elicited`` source adapter.
"""

import json
import os
import random
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

DEFAULT_PATH = os.path.join("data", "elicited.jsonl")
_LIBRARY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scenarios.json")


@dataclass(frozen=True)
class Scenario:
    id: str
    situation: str
    audience: str
    message: str


@dataclass
class Library:
    situations: Dict[str, str]   # id -> label, in display order
    audiences: Dict[str, str]    # id -> label (e.g. "A close friend")
    scenarios: List[Scenario]

    def by_id(self, scenario_id: str) -> Optional[Scenario]:
        return next((s for s in self.scenarios if s.id == scenario_id), None)


def load_library(path: str = _LIBRARY) -> Library:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return Library(
        situations={s["id"]: s["label"] for s in data["situations"]},
        audiences={a["id"]: a["label"] for a in data["audiences"]},
        scenarios=[Scenario(**s) for s in data["scenarios"]],
    )


# ── Answers on disk ───────────────────────────────────────────────────────────
def load_answers(path: str = DEFAULT_PATH) -> List[dict]:
    """Every drill answer saved so far (unreadable lines are skipped)."""
    answers: List[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict) and record.get("kind") == "drill" and record.get("reply"):
                    answers.append(record)
    except FileNotFoundError:
        pass
    return answers


def save_answer(scenario: Scenario, reply_lines: List[str], path: str = DEFAULT_PATH,
                now: Optional[datetime] = None) -> dict:
    """Append one answer. ``reply_lines`` are separate messages, as typed."""
    record = {
        "kind": "drill",
        "scenario": scenario.id,
        "situation": scenario.situation,
        "audience": scenario.audience,
        "message": scenario.message,
        "reply": [line for line in reply_lines if line.strip()],
        "answered_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


# ── Coverage ──────────────────────────────────────────────────────────────────
def coverage(answers: Iterable[dict], library: Library) -> Dict[str, Dict[str, int]]:
    """Answer counts per situation, then per audience within it."""
    table = {s: {a: 0 for a in library.audiences} for s in library.situations}
    for record in answers:
        row = table.get(record.get("situation"))
        if row is not None and record.get("audience") in row:
            row[record["audience"]] += 1
    return table


def next_scenarios(library: Library, answers: Iterable[dict], count: int,
                   situation: Optional[str] = None, rng: Optional[random.Random] = None) -> List[Scenario]:
    """Pick up to ``count`` unanswered scenarios, least-covered first.

    Scenarios are ranked by how many answers their situation already has, then
    their (situation, audience) cell, so each session fills the biggest gaps and
    spreads across audiences. Ties are shuffled. ``situation`` limits the pick
    to one situation. Already-answered scenarios are only reused once every
    scenario in scope has an answer.
    """
    rng = rng or random.Random()
    answers = list(answers)
    answered = {a.get("scenario") for a in answers}
    by_situation = Counter(a.get("situation") for a in answers)
    by_cell = Counter((a.get("situation"), a.get("audience")) for a in answers)

    pool = [s for s in library.scenarios if situation is None or s.situation == situation]
    fresh = [s for s in pool if s.id not in answered] or pool

    chosen: List[Scenario] = []
    sit_count, cell_count = Counter(by_situation), Counter(by_cell)
    candidates = list(fresh)
    rng.shuffle(candidates)
    while candidates and len(chosen) < count:
        candidates.sort(key=lambda s: (sit_count[s.situation], cell_count[(s.situation, s.audience)]))
        pick = candidates.pop(0)
        chosen.append(pick)
        sit_count[pick.situation] += 1          # so one session spreads across situations
        cell_count[(pick.situation, pick.audience)] += 1
    return chosen


# ── Session levels ────────────────────────────────────────────────────────────
SECONDS_PER_DRILL = 30  # rough time to read a message and type a reply

# (id, label, description). Each level is a coverage goal; what's left of it
# depends on what's already been answered.
LEVELS = [
    ("quick", "Quick", "every situation once"),
    ("standard", "Standard", "every situation twice, mixed audiences"),
    ("thorough", "Thorough", "every situation for every audience"),
    ("complete", "Complete", "every message in the library"),
]


def estimate(count: int) -> str:
    """Rough time for ``count`` drills, e.g. "~6 min"."""
    minutes = round(count * SECONDS_PER_DRILL / 60)
    return "<1 min" if count and minutes < 1 else f"~{minutes} min"


def plan_level(library: Library, answers: Iterable[dict], level: str,
               rng: Optional[random.Random] = None) -> List[Scenario]:
    """The drills still needed to reach a coverage ``level``.

    * quick: each situation answered at least once
    * standard: each situation at least twice, preferring audiences not yet used
    * thorough: each situation × audience answered at least once
    * complete: every scenario answered

    Returns them interleaved across situations, so stopping early still leaves
    the coverage balanced.
    """
    rng = rng or random.Random()
    answers = list(answers)
    answered = {a.get("scenario") for a in answers}
    sit_count = Counter(a.get("situation") for a in answers)
    cell_count = Counter((a.get("situation"), a.get("audience")) for a in answers)
    unanswered = [s for s in library.scenarios if s.id not in answered]
    rng.shuffle(unanswered)

    picked: List[Scenario] = []
    if level == "complete":
        picked = unanswered
    elif level == "thorough":
        for sit in library.situations:
            for aud in library.audiences:
                if cell_count[(sit, aud)] == 0:
                    match = next((s for s in unanswered if s.situation == sit and s.audience == aud), None)
                    if match:
                        picked.append(match)
    elif level in ("quick", "standard"):
        target = 1 if level == "quick" else 2
        for sit in library.situations:
            need = target - sit_count[sit]
            # Prefer audiences this situation hasn't been answered for yet.
            options = sorted((s for s in unanswered if s.situation == sit),
                             key=lambda s: cell_count[(sit, s.audience)])
            used = set()
            for s in options:
                if need <= 0:
                    break
                if s.audience in used and any(o.audience not in used for o in options):
                    continue
                picked.append(s)
                used.add(s.audience)
                need -= 1
    else:
        raise ValueError(f"unknown level {level!r}; choose from {[l[0] for l in LEVELS]}")

    # Interleave by situation: round-robin in library order.
    by_sit: Dict[str, List[Scenario]] = {}
    for s in picked:
        by_sit.setdefault(s.situation, []).append(s)
    ordered: List[Scenario] = []
    while any(by_sit.values()):
        for sit in library.situations:
            if by_sit.get(sit):
                ordered.append(by_sit[sit].pop(0))
    return ordered


def coverage_report(answers: Iterable[dict], library: Library) -> str:
    """A small text table of answers per situation × audience."""
    table = coverage(answers, library)
    auds = list(library.audiences)
    short = {"friend": "friend", "family": "family", "colleague": "colleague", "acquaintance": "other"}
    width = max(len(label) for label in library.situations.values()) + 2
    head = f"{'situation':<{width}}" + "".join(f"{short.get(a, a):>10}" for a in auds) + f"{'total':>8}"
    lines = [head, "-" * len(head)]
    gaps = []
    for sit, label in library.situations.items():
        row = table[sit]
        total = sum(row.values())
        if total == 0:
            gaps.append(label.lower())
        cells = "".join(f"{(row[a] or '·'):>10}" for a in auds)
        lines.append(f"{label:<{width}}{cells}{total:>8}")
    answered = sum(sum(r.values()) for r in table.values())
    lines.append("-" * len(head))
    lines.append(f"{answered} answer(s) across {sum(1 for r in table.values() if sum(r.values()))}"
                 f"/{len(library.situations)} situations.")
    if gaps:
        lines.append("Not covered yet: " + ", ".join(gaps) + ".")
    return "\n".join(lines)
