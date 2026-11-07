"""Interactive persona interview (``python -m doppelganger interview``).

See :mod:`ingest.interview` for the topics, storage and persona card.
"""

from typing import Callable, List, Optional, Tuple

from ingest import interview as iv

from doppelganger import tui

BOLD, DIM, AMBER, RESET = tui.BOLD, tui.DIM, tui.AMBER, tui.RESET

_HOWTO = (
    "Answer however you like: short or long, in your own words. Press Enter on an\n"
    "empty line to send. /skip skips a question, /next moves to the next topic,\n"
    "/stop ends the interview (everything answered is saved and you can resume)."
)

_STOP, _NEXT, _SKIP = "stop", "next", "skip"


def _read_answer(input_fn: Callable[[str], str]):
    """Lines of an answer until an empty line, or one of the commands."""
    lines: List[str] = []
    while True:
        try:
            line = input_fn(f"{AMBER}you ›{RESET} " if not lines else f"{DIM}    ›{RESET} ")
        except (EOFError, KeyboardInterrupt):
            print()
            return _STOP
        cmd = line.strip().lower()
        if cmd in ("/stop", "/next", "/skip"):
            return cmd[1:]
        if not line.strip():
            return lines or _SKIP
        lines.append(line.rstrip())


def llm_access(allow_cloud: bool = False) -> Tuple[Optional[object], Optional[str], str]:
    """``(client, model, note)``. The client is None if there's no usable LLM,
    or only a hosted one and ``allow_cloud`` is off (interview answers are
    personal, so they stay local unless you say otherwise)."""
    from ingest import llm
    if not llm.model():
        return None, None, ("No LLM configured: using fixed follow-up questions. "
                            f"Set {llm.BASE_URL_ENV} + {llm.MODEL_ENV} for questions that react to your answers.")
    if not llm.is_local() and not allow_cloud:
        return None, None, ("Only a hosted LLM is configured, and interview answers are personal, so "
                            "using fixed follow-up questions instead. Point "
                            f"{llm.BASE_URL_ENV} at a local server, or pass --allow-cloud.")
    try:
        return llm.get_client(), llm.model(), f"Follow-up questions by {llm.model()} ({llm.endpoint_label()})."
    except (ImportError, EnvironmentError) as e:
        return None, None, f"LLM unavailable ({e}): using fixed follow-up questions."


def choose_depth(topics_left: int, ui=tui) -> Optional[str]:
    options, hints = [], []
    for depth_id, label, desc, _ in iv.DEPTHS:
        options.append(f"{label:<9}")
        hints.append(f"{desc} · ~{iv.estimate_minutes(topics_left, depth_id)} min")
    pick = ui.select("How in-depth?  (you can stop any time; answers are saved)", options, hints=hints,
                     default=1)
    return None if pick is None else iv.DEPTHS[pick][0]


def _ask(question: str, input_fn) -> object:
    print(f"\n{BOLD}{question}{RESET}")
    return _read_answer(input_fn)


def make_card(path: str = iv.ELICITED_PATH, persona_path: str = iv.PERSONA_PATH,
              client=None, model: Optional[str] = None) -> Optional[str]:
    """Write the persona card from the transcript; returns it (or None)."""
    turns = iv.load_turns(path)
    if not turns:
        return None
    card = None
    if client is not None:
        try:
            card = iv.llm_card(turns, client, model, drills_path=path)
        except Exception as e:  # endpoint down, bad model, …: fall back, never lose the card
            print(f"{DIM}Couldn't write the card with the LLM ({e}); using the plain version.{RESET}")
    card = card or iv.fallback_card(turns, drills_path=path)
    iv.write_card(card, persona_path)
    return card


def run_session(
    depth: Optional[str] = None,
    path: str = iv.ELICITED_PATH,
    persona_path: str = iv.PERSONA_PATH,
    ui=tui,
    input_fn: Callable[[str], str] = input,
    allow_cloud: bool = False,
    access: Optional[Tuple[object, Optional[str], str]] = None,
) -> int:
    turns = iv.load_turns(path)
    done = set(iv.topics_done(turns))
    remaining = [t for t in iv.TOPICS if t.id not in done]
    print(f"\n{BOLD}Persona interview{RESET}  {DIM}so the model knows who you are, not just how you type{RESET}")
    print(f"{DIM}{len(done)} of {len(iv.TOPICS)} topics done · answers saved to {path} · "
          f"persona card: {persona_path}{RESET}")

    if not remaining:
        pick = ui.select("You've covered every topic. What now?",
                         ["Rebuild the persona card", "Answer every topic again (adds more)", "Done"])
        if pick is None or pick == 2:
            return 0
        if pick == 0:
            client, model, note = access or llm_access(allow_cloud)
            return _finish(path, persona_path, client, model, answered=0)
        remaining = list(iv.TOPICS)

    if depth is None:
        depth = choose_depth(len(remaining), ui)
        if depth is None:
            return 0
    try:
        follow_ups = iv.depth_followups(depth)
    except ValueError as e:
        print(f"error: {e}")
        return 2

    client, model, note = access or llm_access(allow_cloud)
    print(f"\n{DIM}{note}{RESET}\n{_HOWTO}")

    answered = 0
    for n, topic in enumerate(remaining, 1):
        left = iv.estimate_minutes(len(remaining) - n + 1, depth)
        print(f"\n{DIM}── [{n}/{len(remaining)}] {topic.label} · ~{left} min left ──{RESET}")
        so_far: List[dict] = []
        question: Optional[str] = topic.question
        asked = 0
        while question:
            answer = _ask(question, input_fn)
            if answer == _STOP:
                return _finish(path, persona_path, client, model, answered)
            if answer == _NEXT:
                break
            if answer != _SKIP:
                so_far.append(iv.save_turn(topic, question, answer, path))
                answered += 1
            if asked >= follow_ups:
                break
            question, use_fixed = None, client is None or not so_far
            if not use_fixed:
                try:
                    question = iv.llm_follow_up(topic, so_far, client, model)  # None = topic covered
                except Exception:
                    print(f"{DIM}LLM follow-up failed; using a fixed question.{RESET}")
                    use_fixed = True
            if use_fixed and asked < len(topic.follow_ups):
                question = topic.follow_ups[asked]
            asked += 1
    return _finish(path, persona_path, client, model, answered)


def _finish(path, persona_path, client, model, answered: int) -> int:
    print(f"\nSaved {answered} answer(s).")
    card = make_card(path, persona_path, client, model)
    if card:
        preview = "\n".join(card.strip().splitlines()[:8])
        print(f"\n{BOLD}Persona card{RESET} written to {persona_path}:\n{DIM}{preview}\n…{RESET}")
        print(f"\nIt's used as the system prompt by {BOLD}chat{RESET}, and your answers are training "
              "data next time you run parse/auto.\nMeasure its effect: "
              "python -m doppelganger eval generate --label persona --persona")
    return 0
