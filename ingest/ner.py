"""Optional NER pass: names, places and organisations (issue #13).

The regex tier (``ingest/redaction``) catches structured data: emails, cards,
ID numbers. It can't catch unstructured identifiers like a friend's name, a
street or a workplace. This tier runs a classical named-entity recogniser,
spaCy, entirely offline, and reports:

* ``PERSON``: people's names (spaCy ``PERSON`` / ``PER``)
* ``LOCATION``: places (``GPE``, ``LOC``, ``FAC``)
* ``ORG``: organisations (``ORG``)

Findings go into the same ``redaction_report.json`` and the same ``--redact``
step as the other tiers. It's off by default (``--ner-redact``), and spaCy is an
optional install, kept out of the core requirements::

    pip install -r requirements-ner.txt
    python -m spacy download en_core_web_sm        # English
    python -m spacy download xx_ent_wiki_sm        # multilingual (PER/LOC/ORG)

**Your own names aren't redacted.** The names you appear under in your exports
(and any ``--ner-allow`` names) form an allowlist, so the dataset doesn't
scrub its owner. Classical NER is per-language: coverage is best for the
model's language. The LLM tier (``--llm-redact``) handles mixed-language chat.
"""

from typing import Callable, Iterable, List, Optional, Sequence, Set, Tuple

from ingest import redaction

DEFAULT_MODEL = "en_core_web_sm"

# spaCy label -> report category. Other labels (DATE, MONEY, …) are ignored.
LABELS = {"PERSON": "PERSON", "PER": "PERSON",
          "GPE": "LOCATION", "LOC": "LOCATION", "FAC": "LOCATION",
          "ORG": "ORG"}
SEVERITY = {"PERSON": "medium", "LOCATION": "low", "ORG": "low"}

# Classical NER models misread chat slang as names ("see ya" -> PERSON "ya").
# Entities shorter than this, or in this list, are ignored.
MIN_LENGTH = 3
CHAT_WORDS = {
    "lol", "lmao", "haha", "hahaha", "hehe", "omg", "btw", "idk", "tbh", "imo", "ngl",
    "bro", "bruh", "sis", "dude", "man", "mate", "babe", "fam", "yo", "ya", "yea", "yeah",
    "nah", "ok", "okay", "pls", "plz", "thx", "ty", "np", "gg", "wtf", "smh", "fyi",
    "asap", "brb", "gn", "gm", "hey", "hi", "hello", "bye", "cya", "lah", "leh", "lor",
    "sia", "meh", "mum", "mom", "dad", "grandma", "grandpa", "boss",
}

# A recogniser maps a text to (start, end, label) spans.
Recogniser = Callable[[str], List[Tuple[int, int, str]]]


class NerUnavailable(RuntimeError):
    """spaCy or the requested model isn't installed. The message says how to fix it."""


def load_spacy(model: str = DEFAULT_MODEL) -> Recogniser:
    """A recogniser backed by a spaCy model."""
    try:
        import spacy
    except ImportError:
        raise NerUnavailable(
            "NER redaction needs spaCy, which isn't installed. Install it with:\n"
            "  pip install -r requirements-ner.txt\n"
            f"  python -m spacy download {model}"
        )
    try:
        nlp = spacy.load(model, disable=["parser", "lemmatizer", "tagger", "attribute_ruler"])
    except OSError:
        raise NerUnavailable(
            f"spaCy model {model!r} isn't installed. Download it with:\n"
            f"  python -m spacy download {model}\n"
            "(en_core_web_sm for English, xx_ent_wiki_sm for multilingual)."
        )

    def recognise(text: str) -> List[Tuple[int, int, str]]:
        return [(e.start_char, e.end_char, e.label_) for e in nlp(text).ents]

    return recognise


def allowlist(names: Iterable[str]) -> Set[str]:
    """Lower-cased names plus their individual words ("Alex Tan" also allows
    "Alex" and "Tan"), ignoring one-letter words."""
    allowed: Set[str] = set()
    for name in names:
        name = (name or "").strip().lower()
        if len(name) < 2:
            continue
        allowed.add(name)
        allowed.update(w for w in name.replace(".", " ").split() if len(w) > 1)
    return allowed


def _allowed(span: str, allowed: Set[str]) -> bool:
    s = span.strip().lower()
    if s in allowed:
        return True
    words = [w for w in s.replace(".", " ").split() if w]
    return bool(words) and all(w in allowed for w in words)


def ner_scan_samples(samples, recognise: Recogniser,
                     allow: Optional[Sequence[str]] = None) -> List[dict]:
    """NER findings for every turn, in the same shape as the LLM tier's (with
    offsets, so ``redactor.apply`` can replace them)."""
    allowed = allowlist(allow or [])
    findings: List[dict] = []
    for ci, turns in enumerate(samples):
        for ti, turn in enumerate(turns):
            text = turn.get("text", "")
            if not text.strip():
                continue
            for start, end, label in recognise(text):
                category = LABELS.get(label)
                span = text[start:end]
                if not category or not span.strip() or _allowed(span, allowed):
                    continue
                if len(span.strip()) < MIN_LENGTH or span.strip().lower() in CHAT_WORDS:
                    continue  # chat slang misread as a name
                if span.startswith("[") and span.endswith("]"):
                    continue  # an existing [PLACEHOLDER]
                findings.append({
                    "conversation": ci,
                    "turn": ti,
                    "role": turn.get("role"),
                    "category": category,
                    "detector": "ner",
                    "severity": SEVERITY[category],
                    "start": start,
                    "end": end,
                    "preview": redaction.mask(span),
                })
    return findings
