"""``python -m doppelganger eval`` and the menu's *eval* step.

Thin layer over :mod:`doppelganger.evaluate`: argument handling, friendly
messages when a prerequisite is missing, and the interactive chooser.
"""

import os
from typing import Optional

from doppelganger import evaluate, steps, tui

_NO_TEST_SET = (
    "No test set yet. Evaluation needs conversations the model never trained on:\n"
    "  python -m doppelganger audit --holdout 0.1   # keep ~10% back, then re-train\n"
    "(or choose 'Create a test set' in the eval menu)."
)


def _need_items() -> bool:
    if evaluate.load_items():
        return True
    print(_NO_TEST_SET)
    return False


def do_generate(label: str, base_url: str, model: str, limit: Optional[int] = None,
                system_prompt: Optional[str] = None) -> int:
    if not _need_items():
        return 1
    print(f"Asking {base_url} ({model}) to answer the test conversations as '{label}'…")
    try:
        made = evaluate.generate(label, base_url=base_url, model=model, limit=limit,
                                 system_prompt=system_prompt)
    except ImportError as e:
        print(f"error: {e}")
        return 1
    except Exception as e:  # connection refused, bad model name, …
        print(f"error: couldn't get replies from {base_url}: {e}\n"
              "  Is the model running? Start it with: python -m doppelganger serve")
        return 1
    print(f"Generated {made} new repl{'y' if made == 1 else 'ies'} for '{label}'.")
    return do_score([label])


def _same_as_last(label: str, result: dict) -> bool:
    last = [e for e in evaluate.load_history() if e.get("kind") == "score" and e.get("label") == label]
    return bool(last) and all(last[-1].get(k) == result.get(k) for k in ("n", "chrf", "style_match"))


def do_score(labels=None) -> int:
    """Score the given labels (default: all). A result identical to the last
    recorded one isn't recorded again, so history only grows when something
    changed."""
    if not _need_items():
        return 1
    labels = labels or sorted(evaluate.load_generations())
    if not labels:
        print("No model replies yet. Run: python -m doppelganger eval generate")
        return 1
    for label in labels:
        result = evaluate.score(label)
        if result:
            print(evaluate.format_score(result))
            if not _same_as_last(label, result):
                evaluate.record("score", label, result)
    return 0


def do_blind(label: str, count: int) -> int:
    if not _need_items():
        return 1
    result = evaluate.blind_test(label, count)
    if not result:
        print(f"No replies for '{label}' yet. Run: python -m doppelganger eval generate --label {label}")
        return 1
    print("\n" + evaluate.format_spotting(result, "You"))
    evaluate.record("blind", label, result)
    return 0


def do_judge(label: str, count: int) -> int:
    if not _need_items():
        return 1
    try:
        result = evaluate.llm_judge(label, count)
    except (ImportError, EnvironmentError) as e:
        print(f"error: the LLM judge needs an LLM endpoint: {e}")
        return 1
    if not result:
        print(f"No replies for '{label}' yet. Run: python -m doppelganger eval generate --label {label}")
        return 1
    print(evaluate.format_spotting(result, "The judge"))
    evaluate.record("judge", label, result)
    return 0


def do_report() -> int:
    print(evaluate.report())
    return 0


def dispatch(args) -> int:
    cmd = getattr(args, "eval_command", None)
    if cmd == "generate":
        system_prompt = args.system_prompt
        if getattr(args, "persona", False):
            from ingest.interview import PERSONA_PATH, load_card
            system_prompt = load_card(PERSONA_PATH)
            if not system_prompt:
                print(f"No persona card at {PERSONA_PATH}. Run: python -m doppelganger interview")
                return 1
        return do_generate(args.label, args.base_url, args.model, args.limit, system_prompt)
    if cmd == "score":
        return do_score()
    if cmd == "blind":
        return do_blind(args.label, args.count)
    if cmd == "judge":
        return do_judge(args.label, args.count)
    if cmd == "report" or cmd is None:
        return do_report()
    return 2


def run(ui=tui) -> int:
    """The menu's eval step."""
    items = evaluate.load_items()
    labels = sorted(evaluate.load_generations())
    print(f"\n{tui.BOLD}Evaluate{tui.RESET}  {tui.DIM}does the model sound like you?{tui.RESET}")
    print(f"{tui.DIM}{len(items)} test conversation(s) · model replies: "
          f"{', '.join(labels) if labels else 'none yet'}{tui.RESET}")
    if not items:
        pick = ui.select("There's no test set yet. Create one now?", [
            "Create a test set", "Not now",
        ], hints=["re-run audit keeping ~10% of conversations back, then re-train", ""])
        if pick != 0:
            return 0
        rc = steps.audit(holdout=0.1, country_hint=False)
        if rc == 0:
            print("\nNext: train again (so the model doesn't see the test set), then run "
                  "`serve` and come back to eval.")
        return rc

    actions = [
        ("generate", "Get model replies", "ask the running model (start it with `serve`) to answer the test set"),
        ("score", "Score", "how close the replies are to yours: wording (chrF) and style"),
        ("blind", "Blind test", "you pick which of two replies is really yours"),
        ("judge", "LLM judge", "an LLM does the blind test (needs LLM_* in .env)"),
        ("report", "History", "compare models and runs"),
    ]
    pick = ui.select("What would you like to do?", [a[1] for a in actions], hints=[a[2] for a in actions])
    if pick is None:
        return 0
    action = actions[pick][0]
    if action == "generate":
        label = ui.text("Name for this model's replies", "finetuned")
        url = ui.text("Endpoint", evaluate.DEFAULT_ENDPOINT)
        if not label or not url:
            return 0
        return do_generate(label, url, os.environ.get("EVAL_MODEL", "doppelganger"))
    if action == "score":
        return do_score()
    if action in ("blind", "judge"):
        if not labels:
            print("No model replies yet. Choose 'Get model replies' first.")
            return 1
        if len(labels) == 1:
            label = labels[0]
        else:
            choice = ui.select("Which model?", labels)
            if choice is None:
                return 0
            label = labels[choice]
        return do_blind(label, 20) if action == "blind" else do_judge(label, 50)
    return do_report()
