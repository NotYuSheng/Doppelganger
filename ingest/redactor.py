"""Non-destructive sensitive-data audit over conversation samples.

Runs the regex detectors in :mod:`ingest.redaction` across every turn, writes an
audit report, and prints a warning summary. By default **nothing is changed** —
the user reviews the report and decides whether to act. Acting is opt-in via
:func:`apply` (wired to the CLI's ``--redact`` flag):

  - "replace": swap each detected span for a ``[CATEGORY]`` placeholder, keeping
    conversational structure intact for training.
  - "drop": discard any conversation that contains a detected item.

Detection is regex-based and locale-aware (Singapore-first); see
``ingest/redaction`` to add coverage for more countries.
"""

import json
import re
from collections import defaultdict
from typing import Iterable, List, Optional

from ingest import redaction

# "auto": universal detectors + every country's high-precision detectors.
DEFAULT_LOCALES = redaction.DEFAULT_LOCALES
_MAX_CONSECUTIVE_LLM_FAILURES = 5  # abort the LLM pass if the endpoint keeps failing


def scan_samples(samples, locales: Optional[Iterable[str]] = None) -> dict:
    """Scan every turn and return an audit report (no mutation)."""
    if locales is None:
        locales = DEFAULT_LOCALES

    findings = []
    for ci, turns in enumerate(samples):
        for ti, turn in enumerate(turns):
            for f in redaction.scan_text(turn.get("text", ""), locales):
                findings.append({
                    "conversation": ci,
                    "turn": ti,
                    "role": turn.get("role"),
                    "category": f.category,
                    "detector": f.detector,
                    "severity": f.severity,
                    "preview": f.preview,
                })

    summary = {}
    convs_per_cat = defaultdict(set)
    for f in findings:
        s = summary.setdefault(
            f["category"], {"hits": 0, "conversations": 0, "severity": f["severity"]}
        )
        s["hits"] += 1
        convs_per_cat[f["category"]].add(f["conversation"])
    for cat, s in summary.items():
        s["conversations"] = len(convs_per_cat[cat])

    return {
        "conversations_scanned": len(samples),
        "total_findings": len(findings),
        "locales": list(locales),
        "summary": summary,
        "findings": findings,
    }


def write_report(report: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)


def print_summary(report: dict, report_path: str, mode: str = "off") -> None:
    hint = redaction.country_hint(report.get("locales", []))
    if hint:
        print(f"[redactor] Note: {hint}")
    n = report["total_findings"]
    if n == 0:
        print("[redactor] No sensitive data detected by regex scan.")
        return
    print(
        f"[redactor] WARNING: {n} potential sensitive item(s) detected across "
        f"{report['conversations_scanned']} conversations:"
    )
    for cat, s in sorted(report["summary"].items(), key=lambda kv: -kv[1]["hits"]):
        print(
            f"  {cat:22s} {s['hits']:4d} hit(s) in {s['conversations']:3d} "
            f"conversation(s)  [{s['severity']}]"
        )
    print(f"[redactor] Full report: {report_path}")
    if mode == "off":
        print(
            "[redactor] Nothing was removed. Review it, then re-run with "
            "--redact replace  (placeholder) or  --redact drop  (remove conversations)."
        )


def _replace_spans(text: str, spans) -> str:
    """Replace ``(start, end, category)`` spans with ``[CATEGORY]`` placeholders.

    Overlapping spans are **merged** so the full extent of every detected span is
    redacted — no partial leaks (e.g. a shorter span can't shadow part of a longer
    one). Each merged region is labelled by its longest contributing span.
    Applied right-to-left so earlier offsets stay valid.
    """
    merged = []  # [start, end, label_cat, label_len]
    for start, end, cat in sorted(set(spans)):
        if merged and start < merged[-1][1]:        # overlaps the previous region
            region = merged[-1]
            region[1] = max(region[1], end)
            if end - start > region[3]:             # longer span -> its label wins
                region[2], region[3] = cat, end - start
        else:
            merged.append([start, end, cat, end - start])
    for start, end, cat, _ in reversed(merged):
        text = text[:start] + f"[{cat}]" + text[end:]
    return text


def apply(samples, mode: str, locales: Optional[Iterable[str]] = None,
          llm_findings: Optional[List[dict]] = None) -> List:
    """Return samples with detected data handled per ``mode``.

    ``mode`` is "replace" (swap spans for ``[CATEGORY]``) or "drop" (remove any
    conversation containing a detection). Regex spans are re-derived per turn;
    optional ``llm_findings`` (which carry their own offsets) are applied too.
    """
    if locales is None:
        locales = DEFAULT_LOCALES

    llm_by_turn = defaultdict(list)
    for f in llm_findings or []:
        llm_by_turn[(f["conversation"], f["turn"])].append(
            (f["start"], f["end"], f["category"])
        )

    out = []
    for ci, turns in enumerate(samples):
        new_turns = []
        drop = False
        for ti, turn in enumerate(turns):
            text = turn.get("text", "")
            spans = [(f.start, f.end, f.category) for f in redaction.scan_text(text, locales)]
            spans += llm_by_turn.get((ci, ti), [])
            if not spans:
                new_turns.append(turn)
                continue
            if mode == "drop":
                drop = True
                break
            replaced = dict(turn)
            replaced["text"] = _replace_spans(text, spans)
            new_turns.append(replaced)
        if not drop:
            out.append(new_turns)
    return out


# --- Optional LLM detector (Tier 3) ------------------------------------------
#
# Regex can't catch names or context-dependent secrets. When enabled, the LLM
# reads each conversation and points at sensitive spans *verbatim* (it never
# rewrites the text — that stays the user's decision). Findings flow into the
# same report and the same apply() step as the regex tier. The client/endpoint
# plumbing (incl. LLM_API_BASE_URL for local servers) is shared with the quality
# validator via ingest.llm.

_LLM_PROMPT = """You are a privacy auditor. Identify spans of SENSITIVE or
PERSONALLY IDENTIFYING information in the conversation below: real people's
names, contact details, addresses, financial or government IDs, credentials,
or health/legal/financial specifics that could identify someone.

Each turn is numbered "[i] ROLE: text". Do NOT rewrite anything. For each
finding, copy the offending substring EXACTLY as it appears so it can be located.

Respond with ONLY this JSON:
{{"findings": [{{"turn": <int>, "text": "<verbatim span>", "category": "<short label>", "severity": "low|medium|high"}}]}}

Conversation:
{conversation}"""


def _format_conversation(turns) -> str:
    return "\n".join(
        f"[{i}] {t.get('role', '?').upper()}: {t.get('text', '').strip()}"
        for i, t in enumerate(turns)
    )


def _llm_audit_conversation(client, model, turns) -> List[dict]:
    from ingest import llm

    prompt = _LLM_PROMPT.format(conversation=_format_conversation(turns))
    raw = llm.chat(client, model, prompt, max_tokens=512)
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object in LLM response: {raw!r}")
    return json.loads(match.group()).get("findings", [])


def llm_scan_samples(samples, client, model) -> List[dict]:
    """LLM pass returning verbatim-located findings (with offsets, in memory).

    Each finding is verified by locating the model's span in the turn text; a
    paraphrased span that can't be found is reported as a soft-miss and skipped
    rather than trusting an offset we can't confirm.
    """
    findings = []
    consecutive_failures = 0
    for ci, turns in enumerate(samples):
        try:
            raw = _llm_audit_conversation(client, model, turns)
            consecutive_failures = 0
        except Exception as e:
            consecutive_failures += 1
            print(f"[redactor] LLM scan failed on conversation {ci}: {e}")
            if consecutive_failures >= _MAX_CONSECUTIVE_LLM_FAILURES:
                print("[redactor] Too many consecutive LLM failures — aborting LLM scan.")
                break
            continue
        for rf in raw:
            try:
                ti = int(rf["turn"])
                span = str(rf["text"])
            except (KeyError, ValueError, TypeError):
                continue
            if not (0 <= ti < len(turns)) or not span:
                continue
            text = turns[ti].get("text", "")
            # Record every non-overlapping occurrence — a repeated name/number
            # must not leak just because only the first was redacted.
            start, located = 0, False
            while True:
                idx = text.find(span, start)
                if idx < 0:
                    break
                located = True
                findings.append({
                    "conversation": ci,
                    "turn": ti,
                    "role": turns[ti].get("role"),
                    "category": str(rf.get("category", "PII")),
                    "detector": "llm",
                    "severity": str(rf.get("severity", "medium")),
                    "start": idx,
                    "end": idx + len(span),
                    "preview": redaction.mask(span),
                })
                start = idx + len(span)
            if not located:
                print(f"[redactor] LLM span not found verbatim (conv {ci}, turn {ti}): {span!r}")
    return findings


def merge_llm_findings(report: dict, llm_findings: List[dict]) -> dict:
    """Fold LLM findings into a regex report (masked previews only; no raw spans)."""
    for f in llm_findings:
        report["findings"].append({
            "conversation": f["conversation"],
            "turn": f["turn"],
            "role": f["role"],
            "category": f["category"],
            "detector": "llm",
            "severity": f["severity"],
            "preview": f["preview"],
        })
    convs_per_cat = defaultdict(set)
    for f in report["findings"]:
        convs_per_cat[f["category"]].add(f["conversation"])
    summary = {}
    for f in report["findings"]:
        s = summary.setdefault(
            f["category"], {"hits": 0, "conversations": 0, "severity": f["severity"]}
        )
        s["hits"] += 1
    for cat, s in summary.items():
        s["conversations"] = len(convs_per_cat[cat])
    report["summary"] = summary
    report["total_findings"] = len(report["findings"])
    return report
