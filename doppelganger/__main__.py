"""Doppelganger runner entry point.

    python -m doppelganger              # interactive numbered menu
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
                   help=f"Chat source. Supported: {', '.join(available_sources())}.")
    p.add_argument("--input", dest="input_path", default=steps.DEFAULT_INPUT,
                   help="Path to the raw export.")
    p.add_argument("--self-name", default=None, help="Override auto-detection of which sender is you.")
    p.add_argument("--conversation-gap", type=int, default=3600,
                   help="Seconds of silence that start a new conversation.")
    p.add_argument("--message-chain", type=int, default=30,
                   help="Max seconds between same-sender messages to merge into one turn.")
    p.add_argument("--multi-speaker", action="store_true",
                   help="Keep and label individual senders in group chats.")
    p.add_argument("--redact-locales", default=None,
                   help="Comma-separated locales for sensitive-data detection (e.g. SG,US).")
    if with_redact:
        p.add_argument("--redact", choices=["off", "replace", "drop"], default="replace",
                       help="What to do with detected sensitive data (default: replace).")


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
            scan=not args.skip_redact_scan,
        )
    if args.command == "audit":
        return steps.audit(redact=args.redact, locales=_locales(args.redact_locales),
                           validate=not args.skip_validation)
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
        )
    return 2


# ── Interactive menu ──────────────────────────────────────────────────────────
def _menu() -> int:
    banner.print_banner()
    while True:
        print("\nDoppelganger — pick a step (q to quit):")
        for i, (name, desc, done) in enumerate(steps.STEPS, 1):
            mark = "✓" if done() else " "
            print(f"  [{mark}] {i}. {name:6} — {desc}")
        print("      a. auto   — run parse → audit end-to-end")
        try:
            choice = input("> ").strip().lower()
        except EOFError:       # non-interactive stdin
            print()
            return 0
        except KeyboardInterrupt:  # Ctrl-C
            print()
            return 130

        if choice in ("q", "quit", "exit", ""):
            return 0
        if choice == "a":
            steps.auto()
            continue
        if choice.isdigit() and 1 <= int(choice) <= len(steps.STEPS):
            name = steps.STEPS[int(choice) - 1][0]
            rc = {
                "parse": steps.parse, "audit": steps.audit, "train": steps.train,
                "merge": steps.merge, "chat": steps.chat,
            }[name]()
            if rc:
                print(f"[{name}] exited with code {rc}.")
        else:
            print("Unrecognized choice.")


def main(argv: Optional[List[str]] = None) -> int:
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
