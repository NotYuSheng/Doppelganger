"""Evaluation: "does this actually sound like me?" (issue #17).

The harness compares a model's replies with what the person really wrote, on
conversations the model never trained on:

1. **Held-out test set.** ``audit --holdout 0.1`` keeps ~10% of conversations
   out of the training data (``data/eval_holdout.jsonl``). The split is
   deterministic, so re-running audit keeps the same conversations out.
2. **Generate.** Each test conversation is cut just before one of your replies
   and sent to a model behind an OpenAI-compatible endpoint (``doppelganger
   serve`` runs the fine-tuned model that way; ``serve --base`` runs the base
   model for comparison). Replies are stored per label.
3. **Score.** chrF (how close the wording is) and style match (length, emoji,
   punctuation, lowercase, laughter, multi-message habits) against your real
   replies. See :mod:`ingest.stylometry`.
4. **Blind test.** You see a message and two replies, yours and the model's,
   and pick yours. ~50% means you can't tell them apart.
5. **LLM judge** (optional). The same blind test, done by an LLM given a few of
   your real replies as reference.

Every score and test is appended to ``data/eval_history.jsonl``, so techniques
(drills vs none, group-chat modes, base vs fine-tuned, …) can be compared over
time with ``eval report``.
"""

import hashlib
import json
import os
import random
import re
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ingest import stylometry

DATA_DIR = "data"
HOLDOUT_PATH = os.path.join(DATA_DIR, "eval_holdout.jsonl")
HOLDOUT_META_PATH = os.path.join(DATA_DIR, "eval_holdout.meta.json")
GENERATIONS_PATH = os.path.join(DATA_DIR, "eval_generations.json")
HISTORY_PATH = os.path.join(DATA_DIR, "eval_history.jsonl")

DEFAULT_ENDPOINT = "http://localhost:8000/v1"  # where `doppelganger serve` listens


# ── 1. Held-out test set ──────────────────────────────────────────────────────
def _key(sample) -> str:
    return hashlib.sha1(json.dumps(sample, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def split_holdout(samples: Sequence, fraction: float) -> Tuple[List, List]:
    """Split samples into ``(train, holdout)``.

    Membership depends only on each sample's content, so the same conversations
    are held out every time audit runs with the same fraction, and they never
    leak into training on a re-run.
    """
    if fraction <= 0:
        return list(samples), []
    cut = int(fraction * 10000)
    train, holdout = [], []
    for s in samples:
        (holdout if int(_key(s)[:8], 16) % 10000 < cut else train).append(s)
    return train, holdout


def resolve_fraction(holdout: Optional[float]) -> float:
    """The test-set fraction to use for an audit run.

    ``None`` means "keep the existing test set": the fraction it was made with,
    or 0 if there isn't one. Explicit values must be in [0, 1).
    """
    if holdout is None:
        try:
            with open(HOLDOUT_META_PATH, encoding="utf-8") as f:
                saved = float(json.load(f).get("fraction", 0))
        except (OSError, ValueError, TypeError, AttributeError):
            return 0.0
        return saved if 0 < saved < 1 and os.path.exists(HOLDOUT_PATH) else 0.0
    if not 0 <= holdout < 1:
        raise ValueError(f"--holdout must be a fraction between 0 and 1 (e.g. 0.1 for 10%), got {holdout}")
    return float(holdout)


def write_holdout(samples: Sequence, path: Optional[str] = None,
                  fraction: Optional[float] = None) -> None:
    path = path or HOLDOUT_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    if fraction is not None:
        with open(HOLDOUT_META_PATH, "w", encoding="utf-8") as f:
            json.dump({"fraction": fraction}, f)


def remove_holdout() -> bool:
    """Delete the test set; returns whether there was one."""
    existed = False
    for p in (HOLDOUT_PATH, HOLDOUT_META_PATH):
        if os.path.exists(p):
            os.remove(p)
            existed = True
    return existed


def _merge_runs(turns: List[dict]) -> List[dict]:
    """Merge consecutive turns from the same side (several texts in a row are
    one turn), the same shape training uses."""
    merged: List[dict] = []
    for t in turns:
        if not isinstance(t, dict) or t.get("role") not in ("user", "assistant"):
            continue
        text = str(t.get("text", "")).strip()
        if not text:
            continue
        if merged and merged[-1]["role"] == t["role"]:
            merged[-1] = {"role": t["role"], "text": merged[-1]["text"] + "\n" + text}
        else:
            merged.append({"role": t["role"], "text": text})
    return merged


def load_items(path: Optional[str] = None) -> List[dict]:
    """Test items: ``{"id", "context": [turns], "reference": str}``.

    Each held-out conversation is cut just before your last reply (all the
    messages you sent in a row, together). The turns before it, merged by side
    and starting with the other person, are the context the model answers.
    """
    items = []
    try:
        with open(path or HOLDOUT_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    turns = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(turns, list):
                    continue
                merged = _merge_runs(turns)
                if not merged or merged[-1]["role"] != "assistant":
                    merged = merged[:max((i for i, t in enumerate(merged) if t["role"] == "assistant"),
                                         default=-1) + 1]
                if len(merged) < 2:
                    continue
                context, reference = merged[:-1], merged[-1]["text"]
                while context and context[0]["role"] == "assistant":
                    context = context[1:]  # the model's turn history starts with the other person
                if not context:
                    continue
                items.append({"id": _key(turns)[:12], "context": context, "reference": reference})
    except FileNotFoundError:
        pass
    return items


# ── 2. Generate ───────────────────────────────────────────────────────────────
def load_generations(path: Optional[str] = None) -> Dict[str, Dict[str, str]]:
    try:
        with open(path or GENERATIONS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def _save_generations(data: Dict[str, Dict[str, str]], path: Optional[str] = None) -> None:
    path = path or GENERATIONS_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def make_client(base_url: str):
    try:
        from openai import OpenAI
    except ImportError:
        raise ImportError("The 'openai' package is needed for eval: pip install openai")
    return OpenAI(base_url=base_url, api_key=os.environ.get("EVAL_API_KEY", "not-needed"))


def generate(
    label: str,
    base_url: str = DEFAULT_ENDPOINT,
    model: str = "doppelganger",
    limit: Optional[int] = None,
    system_prompt: Optional[str] = None,
    client=None,
    items: Optional[List[dict]] = None,
    path: Optional[str] = None,
) -> int:
    """Generate a reply for each test item under ``label``. Returns how many
    were generated. Already-generated items are skipped, so it can resume."""
    items = items if items is not None else load_items()
    if limit:
        items = items[:limit]
    client = client or make_client(base_url)
    data = load_generations(path)
    done = data.setdefault(label, {})
    made = 0
    for n, item in enumerate(items, 1):
        if item["id"] in done:
            continue
        messages = [{"role": "system", "content": system_prompt}] if system_prompt else []
        messages += [{"role": t["role"], "content": t["text"]} for t in item["context"]]
        resp = client.chat.completions.create(model=model, messages=messages,
                                              temperature=0.7, max_tokens=256)
        done[item["id"]] = (resp.choices[0].message.content or "").strip()
        made += 1
        _save_generations(data, path)  # after every reply, so a failure loses nothing
        if made % 10 == 0:
            print(f"  {n}/{len(items)} generated…")
    _save_generations(data, path)
    return made


# ── 3. Score ──────────────────────────────────────────────────────────────────
def score(label: str, items: Optional[List[dict]] = None,
          generations: Optional[Dict[str, Dict[str, str]]] = None) -> Optional[dict]:
    """chrF and style match for one label's replies, or None if it has none."""
    items = items if items is not None else load_items()
    gens = (generations if generations is not None else load_generations()).get(label, {})
    pairs = [(gens[i["id"]], i["reference"]) for i in items if i["id"] in gens]
    if not pairs:
        return None
    gen_profile = stylometry.profile(g for g, _ in pairs)
    ref_profile = stylometry.profile(r for _, r in pairs)
    return {
        "label": label,
        "n": len(pairs),
        "chrf": stylometry.mean(stylometry.chrf(g, r) for g, r in pairs),
        "style_match": stylometry.style_match(gen_profile, ref_profile),
        "gaps": stylometry.biggest_gaps(gen_profile, ref_profile),
    }


# ── 4/5. Blind test and LLM judge ─────────────────────────────────────────────
def _pairs_for(label: str, count: int, rng: random.Random,
               items: Optional[List[dict]] = None,
               generations: Optional[Dict[str, Dict[str, str]]] = None) -> List[Tuple[dict, str]]:
    items = items if items is not None else load_items()
    gens = (generations if generations is not None else load_generations()).get(label, {})
    usable = [(i, gens[i["id"]]) for i in items
              if i["id"] in gens and gens[i["id"]].strip() and gens[i["id"]].strip() != i["reference"].strip()]
    rng.shuffle(usable)
    return usable[:count]


def _context_text(context: List[dict], last: int = 3) -> str:
    lines = []
    for t in context[-last:]:
        who = "you" if t["role"] == "assistant" else "them"
        lines.append(f"{who}: {t['text']}")
    return "\n".join(lines)


def distinguishability(correct: int, total: int) -> dict:
    """Blind-test summary. 50% correct = indistinguishable from the real person."""
    rate = correct / total if total else 0.0
    return {"correct": correct, "total": total, "rate": round(100 * rate, 1),
            "p_value": round(stylometry.binomial_p(correct, total), 3)}


def blind_test(label: str, count: int = 20, ui=None, rng: Optional[random.Random] = None,
               items=None, generations=None) -> Optional[dict]:
    """You pick which of two replies is really yours. Returns the summary."""
    from doppelganger import tui
    ui = ui or tui
    rng = rng or random.Random()
    pairs = _pairs_for(label, count, rng, items, generations)
    if not pairs:
        return None
    correct = asked = 0
    for n, (item, generated) in enumerate(pairs, 1):
        options = [("real", item["reference"]), ("model", generated)]
        rng.shuffle(options)
        print(f"\n{tui.DIM}[{n}/{len(pairs)}]{tui.RESET}\n{_context_text(item['context'])}")
        pick = ui.select("Which reply is really yours?",
                         [f"A: {options[0][1]}", f"B: {options[1][1]}"])
        if pick is None:
            break
        asked += 1
        correct += options[pick][0] == "real"
    return distinguishability(correct, asked) if asked else None


_JUDGE_PROMPT = """You will see examples of how one person writes text messages, then a new \
conversation with two candidate replies. Exactly one candidate was written by that person; \
the other was written by an AI imitating them.

Examples of the person's real replies:
{examples}

Conversation:
{context}

Reply A: {a}
Reply B: {b}

Which reply did the person really write? Answer with just the letter A or B."""


def llm_judge(label: str, count: int = 50, client=None, model: Optional[str] = None,
              rng: Optional[random.Random] = None, items=None, generations=None,
              examples: int = 5) -> Optional[dict]:
    """An LLM does the blind test, given a few of your real replies as reference."""
    from ingest import llm
    rng = rng or random.Random()
    items = items if items is not None else load_items()
    pairs = _pairs_for(label, count, rng, items, generations)
    if not pairs:
        return None
    client = client or llm.get_client()
    model = model or llm.model()
    correct = asked = 0
    for item, generated in pairs:
        refs = [i["reference"] for i in items if i["id"] != item["id"]]
        shown = rng.sample(refs, min(examples, len(refs)))
        real_first = rng.random() < 0.5
        a, b = (item["reference"], generated) if real_first else (generated, item["reference"])
        prompt = _JUDGE_PROMPT.format(examples="\n".join(f"- {r}" for r in shown),
                                      context=_context_text(item["context"]), a=a, b=b)
        resp = client.chat.completions.create(model=model, temperature=0,
                                              messages=[{"role": "user", "content": prompt}])
        letter = _judge_letter(resp.choices[0].message.content or "")
        if not letter:
            continue
        asked += 1
        correct += (letter == "A") == real_first
    return distinguishability(correct, asked) if asked else None


def _judge_letter(content: str) -> Optional[str]:
    """The A/B answer in a judge reply. Uses the last standalone capital A or B
    (so the article "a" in "it's a close call, but B" isn't mistaken for A),
    or a bare one-letter reply in either case."""
    letters = re.findall(r"\b([AB])\b", content)
    if letters:
        return letters[-1]
    bare = content.strip().strip(".").upper()
    return bare if bare in ("A", "B") else None


# ── History ───────────────────────────────────────────────────────────────────
def record(kind: str, label: str, result: dict, path: Optional[str] = None,
           now: Optional[datetime] = None) -> None:
    path = path or HISTORY_PATH
    entry = {"time": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
             "kind": kind, "label": label, **{k: v for k, v in result.items() if k != "label"}}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_history(path: Optional[str] = None) -> List[dict]:
    out = []
    try:
        with open(path or HISTORY_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except FileNotFoundError:
        pass
    return out


def report(history: Optional[List[dict]] = None) -> str:
    """Latest result per label and measure, as a text table."""
    history = history if history is not None else load_history()
    if not history:
        return "No evaluation results yet."
    latest: Dict[Tuple[str, str], dict] = {}
    for e in history:
        latest[(e.get("label", "?"), e.get("kind", "?"))] = e
    labels = sorted({label for label, _ in latest})
    head = f"{'model':<18}{'chrF':>8}{'style':>8}{'you spot':>11}{'judge spots':>13}{'when':>22}"
    lines = [head, "-" * len(head)]
    for label in labels:
        s, b, j = (latest.get((label, k), {}) for k in ("score", "blind", "judge"))
        when = max((e.get("time", "") for e in (s, b, j) if e), default="")[:16].replace("T", " ")
        cell = lambda e, key, fmt: fmt.format(e[key]) if key in e else "·"
        lines.append(f"{label:<18}{cell(s, 'chrf', '{:.1f}'):>8}{cell(s, 'style_match', '{:.0f}%'):>8}"
                     f"{cell(b, 'rate', '{:.0f}%'):>11}{cell(j, 'rate', '{:.0f}%'):>13}{when:>22}")
    lines += ["", "chrF, style: higher = closer to how you write.",
              "you spot / judge spots: how often the real reply was picked out. ~50% = indistinguishable."]
    return "\n".join(lines)


def format_score(result: dict) -> str:
    lines = [f"{result['label']}: {result['n']} replies · chrF {result['chrf']:.1f} · "
             f"style match {result['style_match']:.0f}%"]
    lines += [f"  biggest difference: {g}" for g in result["gaps"]]
    return "\n".join(lines)


MIN_TRIALS = 10  # fewer answers than this can't support a verdict either way


def format_spotting(result: dict, who: str = "You") -> str:
    if result["total"] < MIN_TRIALS:
        verdict = f"too few answers to tell yet (aim for {MIN_TRIALS}+)"
    elif result["p_value"] >= 0.05:
        verdict = "can't reliably tell them apart (as good as it gets)"
    elif result["rate"] > 50:
        verdict = "can tell them apart"
    else:
        verdict = "the model's replies looked more like you than your own"
    return (f"{who} picked the real reply {result['correct']}/{result['total']} times "
            f"({result['rate']:.0f}%; 50% = chance). → {verdict}, p={result['p_value']}")
