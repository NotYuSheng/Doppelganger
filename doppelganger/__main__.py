"""Doppelganger runner entry point.

    python -m doppelganger              # interactive menu with a guided setup
    python -m doppelganger parse ...    # named subcommand
    python -m doppelganger auto         # run the whole pipeline unattended
"""

import argparse
import sys
from typing import List, Optional

from ingest import banner
from ingest.adapters import available_sources

from doppelganger import steps


def _add_parse_flags(p: argparse.ArgumentParser, with_redact: bool = False) -> None:
    p.add_argument("--source", default="telegram",
                   help=f"Chat source for inputs without a 'source:' prefix. "
                        f"Supported: {', '.join(available_sources())}.")
    p.add_argument("--input", dest="input_path", action="append", default=None,
                   help=f"Path to a raw export (default: {steps.DEFAULT_INPUT}). Repeat to "
                        "combine exports; prefix with the source to mix apps "
                        "(--input telegram:result.json --input 'whatsapp:Chat.txt').")
    p.add_argument("--self-name", action="append", default=None,
                   help="Your display name if a source can't detect it. 'source:Name' "
                        "sets it for one source (repeatable).")
    p.add_argument("--conversation-gap", type=int, default=3600,
                   help="Seconds of silence that start a new conversation.")
    p.add_argument("--message-chain", type=int, default=30,
                   help="Max seconds between same-sender messages to merge into one turn.")
    p.add_argument("--multi-speaker", action="store_true",
                   help="Keep and label individual senders in group chats.")
    p.add_argument("--group-chats", choices=["keep", "focus", "skip"], default="keep",
                   help="Group chats: keep (default), focus (only direct exchanges "
                        "with one person), or skip.")
    p.add_argument("--redact-locales", default=None,
                   help="Sensitive-data detectors to run: auto (default), country codes "
                        "(e.g. auto,SG,US), all, or none.")
    if with_redact:
        p.add_argument("--redact", choices=["off", "replace", "drop"], default="replace",
                       help="What to do with detected sensitive data (default: replace).")


def _fraction(value: str) -> float:
    try:
        f = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {value!r}")
    if not 0 <= f < 1:
        raise argparse.ArgumentTypeError(f"must be between 0 and 1 (e.g. 0.1 for 10%), got {value}")
    return f


def _locales(value: Optional[str]) -> Optional[List[str]]:
    if not value:
        return None
    return [s.strip() for s in value.split(",") if s.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="doppelganger",
        description="Step-based runner for the Doppelganger pipeline. "
                    "Run with no command for an interactive menu.",
    )
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("parse", help="Parse export → conversations + scan.")
    _add_parse_flags(p)
    p.add_argument("--skip-redact-scan", action="store_true", help="Skip the sensitive-data scan.")

    a = sub.add_parser("audit", help="Review scan → redact → dataset.")
    a.add_argument("--redact", choices=["off", "replace", "drop"], default="replace",
                   help="What to do with detected sensitive data (default: replace).")
    a.add_argument("--redact-locales", default=None, help="Comma-separated detection locales.")
    a.add_argument("--skip-validation", action="store_true", help="Skip the optional LLM quality audit.")
    a.add_argument("--holdout", type=_fraction, default=None,
                   help="Keep this fraction of conversations out of training as a test set "
                        "for `eval` (e.g. 0.1). Without it, an existing test set is kept; "
                        "--holdout 0 removes it.")

    t = sub.add_parser("train", help="LoRA fine-tune on the dataset.")
    t.add_argument("--config", default=None, help="Training config (default: configs/train_lora[.local].yaml).")
    t.add_argument("--gpus", default=None, help="CUDA_VISIBLE_DEVICES, e.g. '0' or '0,1,2,3'.")
    t.add_argument("--epochs", default=None,
                   help="Override num_train_epochs: an integer, or 'auto' for the "
                        "size-based recommendation. Omit to use the config value.")
    t.add_argument("--tensorboard", action="store_true",
                   help="Wire a live TensorBoard dashboard (requires `pip install tensorboard`).")

    m = sub.add_parser("merge", help="Merge the LoRA adapter into the base model.")
    m.add_argument("--config", default=None, help="Export config (default: configs/export_lora[.local].yaml).")
    m.add_argument("--gpus", default=None, help="CUDA_VISIBLE_DEVICES.")

    c = sub.add_parser("chat", help="Chat with the fine-tuned model.")
    c.add_argument("--config", default=None, help="Training config (default: configs/train_lora[.local].yaml).")
    c.add_argument("--gpus", default=None, help="CUDA_VISIBLE_DEVICES, e.g. '0'.")

    d = sub.add_parser("drills", help="Answer reply drills to create training data on purpose.")
    d.add_argument("--level", choices=["quick", "standard", "thorough", "complete"], default=None,
                   help="Coverage goal: quick (each situation once, ~6 min), standard (twice), "
                        "thorough (every situation × audience), complete (all). Default: ask.")
    d.add_argument("--count", type=int, default=None, help="Instead of a level: this many drills.")
    d.add_argument("--situation", default=None,
                   help="Only this situation (e.g. advice, apology, conflict, bad_news).")
    d.add_argument("--report", action="store_true", help="Just show what's covered so far.")

    sv = sub.add_parser("serve", help="Serve the model as an OpenAI-compatible API (for eval).")
    sv.add_argument("--config", default=None, help="Training config (default: configs/train_lora[.local].yaml).")
    sv.add_argument("--gpus", default=None, help="CUDA_VISIBLE_DEVICES.")
    sv.add_argument("--base", action="store_true", help="Serve the base model without the adapter.")
    sv.add_argument("--port", type=int, default=8000, help="Port (default 8000).")

    ev = sub.add_parser("eval", help="Measure how much the model sounds like you.")
    evs = ev.add_subparsers(dest="eval_command")
    g = evs.add_parser("generate", help="Have a model answer the held-out test conversations.")
    g.add_argument("--label", default="finetuned", help="Name for this model's replies (e.g. finetuned, base).")
    g.add_argument("--base-url", default="http://localhost:8000/v1", help="OpenAI-compatible endpoint.")
    g.add_argument("--model", default="doppelganger", help="Model name to request from the endpoint.")
    g.add_argument("--limit", type=int, default=None, help="Only the first N test conversations.")
    g.add_argument("--system-prompt", default=None, help="Optional system prompt (e.g. a persona card).")
    evs.add_parser("score", help="chrF + style match for every model's replies.")
    b = evs.add_parser("blind", help="Blind test: pick which reply is really yours.")
    b.add_argument("--label", default="finetuned")
    b.add_argument("--count", type=int, default=20)
    j = evs.add_parser("judge", help="An LLM does the blind test (uses LLM_* settings in .env).")
    j.add_argument("--label", default="finetuned")
    j.add_argument("--count", type=int, default=50)
    evs.add_parser("report", help="Compare results across models and runs.")

    au = sub.add_parser("auto", help="Run parse → audit end-to-end (optionally → train).")
    _add_parse_flags(au, with_redact=True)
    au.add_argument("--train", dest="do_train", action="store_true", help="Also run training after the dataset is built.")
    au.add_argument("--gpus", default=None, help="CUDA_VISIBLE_DEVICES for training.")
    return parser


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "parse":
        return steps.parse(
            source=args.source, input_path=args.input_path, self_name=args.self_name,
            conversation_gap=args.conversation_gap, message_chain=args.message_chain,
            multi_speaker=args.multi_speaker, locales=_locales(args.redact_locales),
            scan=not args.skip_redact_scan, group_chats=args.group_chats,
        )
    if args.command == "drills":
        from doppelganger import drills as drill_session
        if args.report:
            return drill_session.report()
        return drill_session.run_session(count=args.count, situation=args.situation, level=args.level)
    if args.command == "audit":
        return steps.audit(redact=args.redact, locales=_locales(args.redact_locales),
                           validate=not args.skip_validation, holdout=args.holdout)
    if args.command == "serve":
        return steps.serve(config=args.config, gpus=args.gpus, base=args.base, port=args.port)
    if args.command == "eval":
        from doppelganger import eval_menu
        return eval_menu.dispatch(args)
    if args.command == "train":
        return steps.train(config=args.config, gpus=args.gpus, epochs=args.epochs,
                           tensorboard=args.tensorboard)
    if args.command == "merge":
        return steps.merge(config=args.config, gpus=args.gpus)
    if args.command == "chat":
        return steps.chat(config=args.config, gpus=args.gpus)
    if args.command == "auto":
        return steps.auto(
            do_train=args.do_train, redact=args.redact, gpus=args.gpus,
            source=args.source, input_path=args.input_path, self_name=args.self_name,
            conversation_gap=args.conversation_gap, message_chain=args.message_chain,
            multi_speaker=args.multi_speaker, locales=_locales(args.redact_locales),
            group_chats=args.group_chats,
        )
    return 2


# ── Interactive menu ──────────────────────────────────────────────────────────
def _guided(step_fn) -> int:
    """Run parse/auto after the guided setup (pick exports, who you are, …)."""
    from doppelganger import wizard
    plan = wizard.run()
    if plan is None:
        print("Cancelled.")
        return 0
    extra = {"show_banner": False} if step_fn is steps.auto else {}
    return step_fn(**plan.parse_kwargs(), **extra)


def _menu() -> int:
    from doppelganger import tui
    banner.print_banner()
    from doppelganger import drills as drill_session
    from doppelganger import eval_menu
    runners = {
        "drills": drill_session.run_session,
        "eval": eval_menu.run,
        "parse": lambda: _guided(steps.parse), "audit": lambda: steps.audit(country_hint=False),
        "train": steps.train,
        "merge": steps.merge, "chat": steps.chat,
    }
    while True:
        options, hints, keys = [], [], []
        for name, desc, done in steps.STEPS:
            options.append(f"{'✓' if done() else ' '} {name:<6}")
            hints.append(desc)
            keys.append(name)
        options += [f"  {'auto':<6}", f"  {'quit':<6}"]
        hints += ["guided setup, then parse → audit in one go", ""]
        keys += ["auto", "quit"]
        choice = tui.select("What would you like to do?", options, hints=hints,
                            default=keys.index("parse"))
        if choice is None or keys[choice] == "quit":
            return 0
        name = keys[choice]
        try:
            rc = _guided(steps.auto) if name == "auto" else runners[name]()
        except KeyboardInterrupt:
            print("\nInterrupted.")
            continue
        if rc:
            print(f"[{name}] exited with code {rc}.")


def main(argv: Optional[List[str]] = None) -> int:
    from ingest.console import use_utf8_output
    use_utf8_output()
    steps.load_env()
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        return _menu()
    args = build_parser().parse_args(argv)
    if args.command is None:
        return _menu()
    return _dispatch(args)


if __name__ == "__main__":
    raise SystemExit(main())
