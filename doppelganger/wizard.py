"""Guided setup for the parse step: find exports, confirm who "you" are, run.

``python -m doppelganger`` → *parse* (or *auto*) walks through:

1. **Exports.** ``data/`` and the current folder are scanned for exports from
   every supported app, recognised by the file/folder names each app uses.
   The ones found are listed with all ticked (plus a "Select all" row); you can
   untick some or add one by path.
2. **Who you are.** Each export is test-parsed. If its app can't tell which
   sender is you (always for WhatsApp; LINE/KakaoTalk group chats), the names
   found are listed by message count and you pick yours.
3. **Group chats**: keep / focus / skip.
4. **Sensitive-data scan**: *Auto* (ID numbers with checksums for every
   country), or choose countries whose phone numbers and postcodes should also
   be checked.

The answers become an ordinary :func:`doppelganger.steps.parse` call, the same
as the flags, so the menu and the command line always behave the same.
"""

import contextlib
import io
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ingest import redaction, sources
from ingest.adapters import available_sources, get_adapter

from doppelganger import tui

# Folders never worth scanning.
_SKIP_DIRS = {".git", "venv", ".venv", "node_modules", "__pycache__", "saves",
              "LLaMA-Factory", "merged", "configs", "docs", "tests", "ingest",
              "doppelganger", "demo", "hooks"}

APP_NAMES = {
    "telegram": "Telegram", "whatsapp": "WhatsApp", "instagram": "Instagram",
    "messenger": "Messenger", "line": "LINE", "signal": "Signal", "kakaotalk": "KakaoTalk",
}

COUNTRY_NAMES = {
    "BR": "Brazil", "CA": "Canada", "CN": "China", "GB": "United Kingdom",
    "IN": "India", "MY": "Malaysia", "SG": "Singapore", "US": "United States",
}


@dataclass
class Found:
    """An export discovered on disk."""

    source: str
    path: str


@dataclass
class Plan:
    """Everything the wizard collected, ready for ``steps.parse``."""

    exports: List[Found] = field(default_factory=list)
    self_names: Dict[int, str] = field(default_factory=dict)  # export index -> name
    group_chats: str = "keep"
    countries: List[str] = field(default_factory=list)

    def parse_kwargs(self) -> dict:
        names = [f"{self.exports[i].source}:{n}" for i, n in sorted(self.self_names.items())]
        return dict(
            input_path=[f"{e.source}:{e.path}" for e in self.exports],
            self_name=names,
            group_chats=self.group_chats,
            locales=[redaction.AUTO, *self.countries],
            # The countries were just chosen in the menu; don't suggest flags.
            country_hint=False,
        )


# ── 1. Finding exports ────────────────────────────────────────────────────────
def _looks_like_telegram(path: str) -> bool:
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            head = f.read(65536)
    except OSError:
        return False
    return '"chats"' in head or '"personal_information"' in head


def _classify_file(name: str, path: str) -> Optional[str]:
    if name == "result.json" and _looks_like_telegram(path):
        return "telegram"
    if name.endswith(".txt") and (name.startswith("WhatsApp Chat") or name == "_chat.txt"):
        return "whatsapp"
    if name.startswith("[LINE]") and name.endswith(".txt"):
        return "line"
    if re.match(r"^(KakaoTalkChats.*\.txt|KakaoTalk_.*\.(txt|csv)|Kakaotalk_Chat_.*)$", name):
        return "kakaotalk"
    return None


def _classify_dir(name: str, path: str) -> Optional[str]:
    if name.startswith("signal-export-") and os.path.isfile(os.path.join(path, "main.jsonl")):
        return "signal"
    if name.startswith("instagram-") or os.path.isdir(os.path.join(path, "your_instagram_activity")):
        return "instagram"
    if (name.startswith("facebook-")
            or os.path.isdir(os.path.join(path, "your_facebook_activity"))
            or os.path.isdir(os.path.join(path, "your_activity_across_facebook"))):
        return "messenger"
    return None


def discover(roots: Optional[List[str]] = None, max_depth: int = 2) -> List[Found]:
    """Find exports under ``roots`` (default: ``data/`` and the current folder).

    A recognised export folder isn't searched further (its insides belong to
    it). Results are de-duplicated and sorted by app, then path.
    """
    roots = roots if roots is not None else [os.path.dirname(sources.DEFAULT_INPUT), "."]
    found: Dict[str, Found] = {}

    def walk(directory: str, depth: int) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda e: e.name)
        except OSError:
            return
        for entry in entries:
            path = os.path.normpath(entry.path)
            if entry.is_dir(follow_symlinks=False):
                if entry.name in _SKIP_DIRS or entry.name.startswith("."):
                    continue
                source = _classify_dir(entry.name, path)
                if source:
                    found.setdefault(os.path.abspath(path), Found(source, path))
                elif depth < max_depth:
                    walk(path, depth + 1)
            elif entry.is_file():
                source = _classify_file(entry.name, path)
                if source:
                    found.setdefault(os.path.abspath(path), Found(source, path))

    for root in roots:
        if os.path.isdir(root):
            walk(root, 0)
    order = list(APP_NAMES)
    return sorted(found.values(), key=lambda f: (order.index(f.source), f.path))


# ── 2. Who you are ────────────────────────────────────────────────────────────
@contextlib.contextmanager
def _quiet():
    with contextlib.redirect_stdout(io.StringIO()):
        yield


def needs_self_name(found: Found) -> Tuple[bool, List[Tuple[str, int]]]:
    """Whether this export needs you to say which sender you are.

    Returns ``(needed, senders)`` where ``senders`` are ``(name, message count)``
    pairs, most active first (empty when not needed or unreadable).
    """
    adapter = get_adapter(found.source)
    try:
        with _quiet():
            adapter.parse(found.path, self_name=None)
        return False, []
    except ValueError as e:
        if "--self-name" not in str(e):
            raise
    # Parse again with a name nobody has, just to list who's in the chat.
    with _quiet():
        messages = adapter.parse(found.path, self_name="\x00")
    counts = Counter(m.sender_id for m in messages)
    return True, counts.most_common()


# ── The flow ──────────────────────────────────────────────────────────────────
def _label(f: Found) -> str:
    return f"{APP_NAMES.get(f.source, f.source):<9}  {f.path}"


def _add_by_hand(ui) -> Optional[Found]:
    apps = available_sources()
    i = ui.select("Which app is it from?", [APP_NAMES.get(a, a) for a in apps])
    if i is None:
        return None
    path = ui.text("Path to the export (file or folder)")
    if not path:
        return None
    if not os.path.exists(path):
        print(f"  Not found: {path}")
        return None
    return Found(apps[i], path)


def choose_exports(ui=tui, roots: Optional[List[str]] = None) -> Optional[List[Found]]:
    found = discover(roots)
    while True:
        if found:
            picked = ui.multiselect(
                f"Found {len(found)} export(s). Which should be used?",
                [_label(f) for f in found], checked=set(range(len(found))),
                select_all=True, action="Add another export by path…",
            )
            if picked is None:
                return None
            if picked == tui.ACTION:
                extra = _add_by_hand(ui)
                if extra:
                    found.append(extra)
                continue
            chosen = [found[i] for i in picked]
            if chosen:
                return chosen
            print("  Pick at least one export.")
        else:
            print("No chat exports found in data/ or the current folder.\n"
                  "  See docs/sources/README.md for how to export from each app, then put\n"
                  "  the export in data/ (or enter its path below).")
            extra = _add_by_hand(ui)
            if extra is None:
                return None
            found.append(extra)


def choose_self_names(exports: List[Found], ui=tui) -> Optional[Dict[int, str]]:
    names: Dict[int, str] = {}
    for i, f in enumerate(exports):
        try:
            needed, senders = needs_self_name(f)
        except Exception as e:  # unreadable export: let parse report it properly
            print(f"  Couldn't read {f.path}: {e}")
            continue
        if not needed:
            continue
        if not senders:
            print(f"  No messages found in {f.path}.")
            continue
        options = [s for s, _ in senders]
        hints = [f"{n} message{'s' if n != 1 else ''}" for _, n in senders]
        pick = ui.select(f"Which of these is you in {APP_NAMES.get(f.source, f.source)} "
                         f"({os.path.basename(f.path.rstrip(os.sep))})?", options, hints=hints)
        if pick is None:
            return None
        names[i] = options[pick]
    return names


def choose_group_chats(ui=tui) -> Optional[str]:
    modes = ["keep", "focus", "skip"]
    i = ui.select("Group chats (3+ people):", [
        "Keep", "Focus", "Skip",
    ], default=0, hints=[
        "use them like 1:1 chats",
        "only direct back-and-forth with one person",
        "leave them out",
    ])
    return None if i is None else modes[i]


def choose_countries(ui=tui) -> Optional[List[str]]:
    """Which countries' phone/postcode/format-only detectors to add.

    "Auto" (the default) checks ID numbers for every country and adds nothing
    else; only if you ask to choose is the country list shown.
    """
    mode = ui.select("Sensitive-data scan:", ["Auto", "Choose countries…"], default=0, hints=[
        "recommended: ID numbers from every country, plus emails, cards, keys",
        "also check phone numbers & postcodes for the countries you pick",
    ])
    if mode is None:
        return None
    if mode == 0:
        return []
    codes = [c for c in redaction.available_locales() if c != redaction.UNIVERSAL]
    picked = ui.multiselect(
        "Also check phone numbers & postcodes for:",
        [f"{COUNTRY_NAMES.get(c, c)} ({c})" for c in codes], checked=set(), select_all=True,
    )
    return None if picked is None else [codes[i] for i in picked]


def run(ui=tui, roots: Optional[List[str]] = None) -> Optional[Plan]:
    """Ask everything; returns a :class:`Plan`, or None if the user cancelled."""
    exports = choose_exports(ui, roots)
    if exports is None:
        return None
    names = choose_self_names(exports, ui)
    if names is None:
        return None
    group_chats = choose_group_chats(ui)
    if group_chats is None:
        return None
    countries = choose_countries(ui)
    if countries is None:
        return None
    return Plan(exports, names, group_chats, countries)
