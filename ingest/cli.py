"""Command-line entrypoint: raw chat export -> fine-tuning dataset.

    python -m ingest --source telegram --input data/result.json

    # several exports in one run -> one combined dataset
    python -m ingest --input telegram:data/result.json \
                     --input "whatsapp:data/WhatsApp Chat with Alice.txt" \
                     --self-name "whatsapp:Your Name"

Pipeline: adapter (parse) -> core (build samples) -> validator (optional LLM
filter) -> writer (ShareGPT or raw JSONL).
"""

import argparse
import os
import sys

import os.path

from ingest import core, redactor, sharegpt, sources
from ingest.adapters import available_sources
from ingest.validator import validate_samples

DEFAULT_INPUT = sources.DEFAULT_INPUT
SHAREGPT_OUTPUT = "./data/chat_sharegpt.json"
JSONL_OUTPUT = "./data/chat_dataset.jsonl"


def _load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader (no dependency).

    Sets ``KEY=VALUE`` pairs from ``path`` into the environment without
    overriding variables already set in the real environment.
    """
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _run_llm_redaction(samples, allow_cloud: bool):
    """Run the optional LLM redaction pass, guarding against accidental cloud use.

    Returns a (possibly empty) list of LLM findings. Prefers a local endpoint;
    if none is configured and cloud use wasn't explicitly allowed, it warns and
    skips rather than silently shipping chat data to a third party.
    """
    from ingest import llm

    if not llm.is_local() and not allow_cloud:
        print(
            "[redactor] --llm-redact set but no local endpoint configured. "
            "Refusing to send chat data to a hosted API by default. Set "
            f"{llm.BASE_URL_ENV} to a local OpenAI-compatible server (Ollama, "
            "vLLM, LM Studio, ...), or pass --allow-cloud-redaction to override. "
            "Skipping LLM pass."
        )
        return []

    try:
        client = llm.get_client()
    except (ImportError, EnvironmentError) as e:
        print(f"[redactor] LLM redaction unavailable: {e}. Skipping LLM pass.")
        return []

    model = llm.model()
    print(f"[redactor] LLM redaction scan via {model} ({llm.endpoint_label()})...")
    return redactor.llm_scan_samples(samples, client, model)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ingest",
        description="Convert a chat export into a fine-tuning dataset.",
    )
    parser.add_argument(
        "--source",
        default="telegram",
        help=f"Chat source for inputs without a 'source:' prefix. Supported: "
        f"{', '.join(available_sources())}.",
    )
    parser.add_argument(
        "--input",
        action="append",
        default=None,
        help=f"Path to a raw export (default: {DEFAULT_INPUT}). Repeat to combine "
        "several exports into one dataset; prefix with the source to mix apps, "
        "e.g. --input telegram:result.json --input 'whatsapp:Chat.txt'.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output path. Defaults to data/chat_sharegpt.json (sharegpt) "
        "or data/chat_dataset.jsonl (jsonl).",
    )
    parser.add_argument(
        "--format",
        choices=["sharegpt", "jsonl"],
        default="sharegpt",
        help="Output format (default: sharegpt — what training consumes).",
    )
    parser.add_argument(
        "--self-name",
        action="append",
        default=None,
        help="Your display name, when a source can't detect it. Use "
        "'source:Name' to set it for one source (repeatable); a plain name "
        "applies to every source without its own.",
    )
    parser.add_argument(
        "--conversation-gap",
        type=int,
        default=core.DEFAULT_CONVERSATION_GAP,
        help=f"Seconds of silence that start a new conversation "
        f"(default: {core.DEFAULT_CONVERSATION_GAP}).",
    )
    parser.add_argument(
        "--message-chain",
        type=int,
        default=core.DEFAULT_MESSAGE_CHAIN,
        help=f"Max seconds between same-sender messages to merge into one turn "
        f"(default: {core.DEFAULT_MESSAGE_CHAIN}).",
    )
    parser.add_argument(
        "--multi-speaker",
        action="store_true",
        help="In group chats, keep individual senders and label each user turn "
        "with their name (e.g. 'Bob: ...'). Your own turns are never labelled. "
        "Default collapses the other side into one speaker.",
    )
    parser.add_argument(
        "--group-chats",
        choices=list(core.GROUP_CHAT_MODES),
        default=core.DEFAULT_GROUP_CHATS,
        help="How to use chats with more than two people. 'keep' (default) uses "
        "them like 1:1 chats; 'focus' keeps only stretches where you and one other "
        "person talk directly (plus your explicit replies, where the source has "
        "reply links); 'skip' leaves them out.",
    )
    parser.add_argument(
        "--redact",
        choices=["off", "replace", "drop"],
        default="off",
        help="What to do with detected sensitive data. 'off' (default) only "
        "scans and writes a report. 'replace' swaps spans for [CATEGORY] "
        "placeholders; 'drop' removes conversations containing detections.",
    )
    parser.add_argument(
        "--redact-locales",
        default="SG",
        help="Comma-separated locales for sensitive-data detection (universal "
        "patterns always run). Default: SG.",
    )
    parser.add_argument(
        "--skip-redact-scan",
        action="store_true",
        help="Skip the sensitive-data scan/report entirely.",
    )
    parser.add_argument(
        "--llm-redact",
        action="store_true",
        help="Additionally use an LLM to flag context-dependent sensitive data "
        "(names, secrets regex misses). Prefers a local endpoint: set "
        "LLM_API_BASE_URL, or pass --allow-cloud-redaction to use a hosted API "
        "(which sends chat text to a third party).",
    )
    parser.add_argument(
        "--allow-cloud-redaction",
        action="store_true",
        help="Permit LLM redaction against a hosted API when no local "
        "LLM_API_BASE_URL is configured.",
    )
    parser.add_argument(
        "--no-audit",
        action="store_true",
        help="Master off-switch: skip ALL auditing — the regex sensitive-data "
        "scan and the LLM quality validation. Just build the dataset.",
    )
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Skip only the LLM quality validation (the regex scan still runs "
        "unless --skip-redact-scan / --no-audit is also given).",
    )
    return parser


def main(argv=None) -> int:
    _load_dotenv()
    args = build_parser().parse_args(argv)

    from ingest import banner
    banner.print_banner()

    try:
        specs = sources.resolve_specs(args.input, args.source, args.self_name)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    output = args.output or (
        SHAREGPT_OUTPUT if args.format == "sharegpt" else JSONL_OUTPUT
    )

    try:
        samples, results = sources.load_samples(
            specs,
            conversation_gap=args.conversation_gap,
            message_chain=args.message_chain,
            multi_speaker=args.multi_speaker,
            group_chats=args.group_chats,
        )
    except sources.SourceLoadError as e:  # missing file, bad export, unknown self, ...
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"Extracted {len(samples)} conversation samples.")
    sources.print_summary(results)
    sources.print_group_advice(results, args.group_chats, args.multi_speaker)

    # --no-audit is the master off-switch; the granular flags disable one half.
    skip_scan = args.no_audit or args.skip_redact_scan
    skip_validation = args.no_audit or args.skip_validation
    if args.no_audit:
        print("[audit] All auditing disabled (--no-audit) — building dataset as-is.")

    locales = [s.strip() for s in args.redact_locales.split(",") if s.strip()]
    llm_findings = []
    if not skip_scan:
        report = redactor.scan_samples(samples, locales=locales)
        if args.llm_redact:
            llm_findings = _run_llm_redaction(samples, args.allow_cloud_redaction)
            redactor.merge_llm_findings(report, llm_findings)
        report_path = os.path.join(os.path.dirname(output) or ".", "redaction_report.json")
        redactor.write_report(report, report_path)
        redactor.print_summary(report, report_path, mode=args.redact)

    # --redact is an explicit request, so honour it even when the scan/report was
    # skipped — otherwise the dataset would silently keep sensitive data.
    if args.redact != "off":
        if skip_scan:
            print(
                f"[redactor] Scan skipped, but --redact {args.redact} was requested — "
                "applying regex redaction (note: --llm-redact needs the scan)."
            )
        before = len(samples)
        samples = redactor.apply(
            samples, args.redact, locales=locales, llm_findings=llm_findings
        )
        print(
            f"[redactor] Applied --redact {args.redact}: "
            f"{before} -> {len(samples)} samples."
        )

    if not skip_validation:
        samples = validate_samples(samples)

    if args.format == "sharegpt":
        written = sharegpt.write_sharegpt(samples, output)
        print(f"Wrote {written} ShareGPT samples to {output}.")
    else:
        written = sharegpt.write_jsonl(samples, output)
        print(f"Wrote {written} samples to {output}.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
