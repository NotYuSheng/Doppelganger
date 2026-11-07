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
# These words are ignored. Two-letter entities are kept only when written like a
# name ("Li", "Wu", "Ng"), not all-caps acronyms ("IC") or lowercase slang.
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

    def many(texts: List[str]) -> List[List[Tuple[int, int, str]]]:
        """Batched: much faster than one call per turn on large exports."""
        return [[(e.start_char, e.end_char, e.label_) for e in doc.ents]
                for doc in nlp.pipe(texts, batch_size=256)]

    recognise.many = many
    return recognise


def add_cli_flags(parser) -> None:
    """The --ner-* flags, shared by ``python -m ingest`` and ``python -m doppelganger``."""
    parser.add_argument(
        "--ner-redact", action="store_true",
        help="Also find names, places and organisations offline with spaCy NER "
        "(optional install: pip install -r requirements-ner.txt). Your own names are "
        "never redacted.")
    parser.add_argument(
        "--ner-model", default=DEFAULT_MODEL,
        help=f"spaCy model for --ner-redact (default {DEFAULT_MODEL}; xx_ent_wiki_sm for multilingual).")
    parser.add_argument(
        "--ner-allow", action="append", default=[],
        help="A name NER should never redact (repeatable). Your own names from the exports "
        "are always allowed.")
    parser.add_argument(
        "--ner-strict-self", action="store_true",
        help="Only skip your full name(s), not each word of them (so someone who shares "
        "your first name is still redacted).")


def _too_short_or_slang(span: str) -> bool:
    s = span.strip()
    if len(s) < 2 or s.lower() in CHAT_WORDS:
        return True
    return len(s) == 2 and not (s[0].isupper() and s[1].islower())


def allowlist(names: Iterable[str]) -> Set[str]:
    """Lower-cased names plus their individual words ("Alex Tan" also allows
    "Alex" and "Tan"), ignoring one-letter words.

    Trade-off: in your own chats, "Alex" almost always means you, so the words
    are allowed too. The cost is that someone else who shares your first name or
    surname isn't redacted. ``--ner-strict-self`` allows only the full names.
    """
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
                     allow: Optional[Sequence[str]] = None, strict_self: bool = False) -> List[dict]:
    """NER findings for every turn, in the same shape as the LLM tier's (with
    offsets, so ``redactor.apply`` can replace them). ``strict_self`` allows
    only full names, not their individual words."""
    allowed = ({(n or "").strip().lower() for n in (allow or []) if len((n or "").strip()) > 1}
               if strict_self else allowlist(allow or []))
    findings: List[dict] = []
    where = [(ci, ti, turn) for ci, turns in enumerate(samples)
             for ti, turn in enumerate(turns) if turn.get("text", "").strip()]
    texts = [turn["text"] for _, _, turn in where]
    many = getattr(recognise, "many", None)
    all_spans = many(texts) if many else [recognise(t) for t in texts]
    for (ci, ti, turn), text, spans in zip(where, texts, all_spans):
        for start, end, label in spans:
            category = LABELS.get(label)
            span = text[start:end]
            if not category or not span.strip() or _allowed(span, allowed):
                continue
            if _too_short_or_slang(span):
                continue  # chat slang / acronyms misread as names
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
