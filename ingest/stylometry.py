"""Stylometry: how close is a set of replies to how someone actually writes?

Pure Python, no dependencies. Two kinds of measures, used by the evaluation
harness (``python -m doppelganger eval``):

* **chrF**, a character n-gram F-score between a generated reply and the real
  one (Popović 2015). It rewards using the same words, spellings and fragments,
  and is robust for short, informal text where word-level BLEU breaks down.
* **Style profile**: per-reply habits (length, emoji, punctuation, lowercase,
  laughter, splitting a reply into several messages) averaged over a set of
  replies. :func:`style_match` compares two profiles, so "does the model text
  like you?" can be scored even when the exact words differ.
"""

import math
import re
from collections import Counter
from typing import Dict, Iterable, List

# Emoji and pictographs (broad ranges; enough for rates, not for rendering).
_EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]")
_LAUGH = re.compile(r"\b(?:a?ha(?:ha)+h?|he(?:he)+|lol+|lmao+|rofl|xd+|kek+|w{3,}|ㅋ{2,}|哈{2,}|555+)\b", re.I)
_WORD = re.compile(r"\w+", re.UNICODE)

# Each feature: (name, human label, how far apart two profiles can sensibly be).
# The scale normalises differences so no single feature dominates style_match.
FEATURES = [
    ("words", "words per reply", 15.0),
    ("messages", "messages per reply", 1.5),
    ("emoji", "emoji per reply", 1.0),
    ("exclaim", "replies with !", 0.5),
    ("question", "replies with ?", 0.5),
    ("lower_start", "replies starting lowercase", 0.5),
    ("end_period", "replies ending with a period", 0.5),
    ("ellipsis", "replies with …/...", 0.5),
    ("laugh", "replies with lol/haha", 0.5),
    ("caps_word", "replies with an ALL-CAPS word", 0.5),
]


def features(text: str) -> Dict[str, float]:
    """Style features of one reply (a reply may span several message lines)."""
    lines = [l for l in text.split("\n") if l.strip()]
    stripped = text.strip()
    words = _WORD.findall(text)
    first_alpha = next((c for c in stripped if c.isalpha()), "")
    return {
        "words": float(len(words)),
        "messages": float(max(1, len(lines))),
        "emoji": float(len(_EMOJI.findall(text))),
        "exclaim": float("!" in text),
        "question": float("?" in text),
        "lower_start": float(bool(first_alpha) and first_alpha.islower()),
        "end_period": float(stripped.endswith(".") and not stripped.endswith("...")),
        "ellipsis": float("..." in text or "…" in text),
        "laugh": float(bool(_LAUGH.search(text))),
        "caps_word": float(any(len(w) >= 2 and w.isupper() and w.isalpha() for w in words)),
    }


def profile(texts: Iterable[str]) -> Dict[str, float]:
    """Average features over a set of replies."""
    rows = [features(t) for t in texts]
    if not rows:
        return {name: 0.0 for name, _, _ in FEATURES}
    return {name: sum(r[name] for r in rows) / len(rows) for name, _, _ in FEATURES}


def style_match(generated: Dict[str, float], reference: Dict[str, float]) -> float:
    """How alike two style profiles are, 0–100 (100 = identical habits).

    Each feature's gap is scaled by its typical range and capped at 1, then
    averaged; the score is 100 × (1 − mean gap).
    """
    gaps = [min(1.0, abs(generated[n] - reference[n]) / scale) for n, _, scale in FEATURES]
    return round(100 * (1 - sum(gaps) / len(gaps)), 1)


def biggest_gaps(generated: Dict[str, float], reference: Dict[str, float], top: int = 3) -> List[str]:
    """The features that differ most, as readable lines (for reports)."""
    scored = sorted(
        ((min(1.0, abs(generated[n] - reference[n]) / s), n, label) for n, label, s in FEATURES),
        reverse=True,
    )
    out = []
    for gap, name, label in scored[:top]:
        if gap < 0.1:
            break
        fmt = (lambda v: f"{v:.1f}") if name in ("words", "messages", "emoji") else (lambda v: f"{v:.0%}")
        out.append(f"{label}: you {fmt(reference[name])}, model {fmt(generated[name])}")
    return out


def _ngrams(text: str, n: int) -> Counter:
    text = " ".join(text.split())
    return Counter(text[i:i + n] for i in range(len(text) - n + 1))


def chrf(hypothesis: str, reference: str, max_n: int = 6, beta: float = 2.0) -> float:
    """chrF score (0–100) of one hypothesis against one reference."""
    precisions, recalls = [], []
    for n in range(1, max_n + 1):
        hyp, ref = _ngrams(hypothesis, n), _ngrams(reference, n)
        if not hyp or not ref:
            continue
        overlap = sum((hyp & ref).values())
        precisions.append(overlap / sum(hyp.values()))
        recalls.append(overlap / sum(ref.values()))
    if not precisions:
        return 0.0
    p, r = sum(precisions) / len(precisions), sum(recalls) / len(recalls)
    if p == 0 and r == 0:
        return 0.0
    b2 = beta * beta
    return round(100 * (1 + b2) * p * r / (b2 * p + r), 1)


def mean(values: Iterable[float]) -> float:
    values = list(values)
    return round(sum(values) / len(values), 1) if values else 0.0


def binomial_p(successes: int, trials: int) -> float:
    """Two-sided p-value that ``successes``/``trials`` differs from a coin flip.

    Used for the blind test: if you can't tell your replies from the model's
    better than chance, the model is passing.
    """
    if trials == 0:
        return 1.0
    k = max(successes, trials - successes)
    tail = sum(math.comb(trials, i) for i in range(k, trials + 1)) / 2 ** trials
    return min(1.0, 2 * tail)
