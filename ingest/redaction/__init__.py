"""Regex-based sensitive-data detection.

A *detector* is a named, locale-tagged regex, optionally backed by a checksum
validator, that flags one category of sensitive data: an email, a credit card,
a national ID number, and so on. Detectors register themselves at import time
via :func:`make`, exactly like source adapters do. Every module in this package
is loaded automatically, so adding coverage for a country is one drop-in file,
``ingest/redaction/<cc>.py``, with no changes to the scanner or the pipeline.

Detection is **non-destructive**: :func:`scan_text` only *reports* matches (as
:class:`Finding` objects). Whether to redact is the user's decision, taken
later against the audit report.

Which detectors run
-------------------
Every detector has a **precision** tier:

* ``"high"``: a checksum *and* a structure random text rarely has (letters
  mixed with digits, an embedded date), or two or more check digits. False
  positives are rare enough to run these for every user, whatever country
  they're in.
* ``"standard"``: format-only patterns, a single check digit over a plain run
  of digits, phone numbers, postcodes. Useful, but noisy for someone in another
  country, so they only run when their country is selected.

Locale selections (``--redact-locales``, comma-separated) can mix:

* ``auto`` (the default): universal detectors plus every country's
  high-precision detectors.
* Country codes (``SG``, ``US``, …): all of that country's detectors.
* ``all``: every detector. ``none``: universal detectors only.

Universal detectors (email, cards, IBAN, API keys) always run.

See ``docs/redaction.md`` for how to write a detector.
"""

import importlib
import pkgutil
import re
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Pattern, Sequence, Tuple

UNIVERSAL = "universal"  # locale tag for patterns that are the same worldwide

PRECISIONS = ("high", "standard")
SEVERITIES = ("low", "medium", "high")

# Special --redact-locales tokens (case-insensitive).
AUTO, ALL, NONE = "auto", "all", "none"
DEFAULT_LOCALES = [AUTO]


@dataclass(frozen=True)
class Detector:
    """One category of sensitive data and how to recognise it.

    Attributes:
        name: Unique id, e.g. ``"sg_nric"`` or ``"email"``.
        category: Human-facing label shown in reports, e.g. ``"NRIC"``.
        locale: ``"universal"`` or an ISO 3166-1 alpha-2 code (``"SG"``).
        pattern: Compiled regex. Every full match is a candidate. A named
            ``id`` group, if present, is the part reported (so context such as
            a keyword can be required without being redacted).
        severity: ``"low" | "medium" | "high"``; drives the suggested action.
        validator: Optional extra check on the matched string (checksum, date
            validity, …). A candidate is only flagged if it returns True.
        precision: ``"high"`` or ``"standard"``; see the module docstring.
        description: One line on what this detects.
        reference: Where the format or checksum is specified (URL or document).
        examples: Texts that MUST produce a finding from this detector.
        counter_examples: Texts that must NOT produce one (near misses: a wrong
            check digit, a similar-looking number, missing context).
    """

    name: str
    category: str
    locale: str
    pattern: Pattern
    severity: str = "medium"
    validator: Optional[Callable[[str], bool]] = None
    precision: str = "standard"
    description: str = ""
    reference: str = ""
    examples: Tuple[str, ...] = ()
    counter_examples: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Finding:
    """A single detected span of sensitive data within one text."""

    detector: str
    category: str
    locale: str
    severity: str
    start: int
    end: int
    value: str
    preview: str  # masked, safe to print/log


_REGISTRY: "List[Detector]" = []


def register(detector: Detector) -> Detector:
    """Register a detector. Duplicate ``name`` is a programming error."""
    if any(d.name == detector.name for d in _REGISTRY):
        raise ValueError(f"Duplicate detector name: {detector.name!r}")
    if detector.precision not in PRECISIONS:
        raise ValueError(f"{detector.name}: precision must be one of {PRECISIONS}")
    if detector.severity not in SEVERITIES:
        raise ValueError(f"{detector.name}: severity must be one of {SEVERITIES}")
    _REGISTRY.append(detector)
    return detector


def make(
    name: str,
    category: str,
    locale: str,
    regex: str,
    *,
    severity: str = "medium",
    flags: int = 0,
    validator: Optional[Callable[[str], bool]] = None,
    precision: str = "standard",
    description: str = "",
    reference: str = "",
    examples: Sequence[str] = (),
    counter_examples: Sequence[str] = (),
) -> Detector:
    """Compile a regex and register it as a detector in one call."""
    return register(
        Detector(
            name=name,
            category=category,
            locale=locale,
            pattern=re.compile(regex, flags),
            severity=severity,
            validator=validator,
            precision=precision,
            description=description,
            reference=reference,
            examples=tuple(examples),
            counter_examples=tuple(counter_examples),
        )
    )


def available_locales() -> "List[str]":
    return sorted({d.locale for d in _REGISTRY})


def iter_detectors(locales: Optional[Iterable[str]] = None) -> "List[Detector]":
    """Detectors for a locale selection (see the module docstring).

    ``None`` means every detector. An empty selection means universal only.
    Unknown country codes are ignored (they simply have no detectors yet).
    """
    if locales is None:
        return list(_REGISTRY)
    tokens = {str(t).strip() for t in locales if str(t).strip()}
    lowered = {t.lower() for t in tokens}
    if ALL in lowered:
        return list(_REGISTRY)
    countries = {t.upper() for t in tokens if t.lower() not in (AUTO, NONE)}
    auto = AUTO in lowered
    return [
        d for d in _REGISTRY
        if d.locale == UNIVERSAL
        or d.locale.upper() in countries
        or (auto and d.precision == "high")
    ]


def country_hint(locales: Iterable[str]) -> Optional[str]:
    """A one-line nudge when no country was selected, else None.

    With only ``auto``/``none`` (or nothing), phone numbers, postcodes and
    format-only IDs aren't checked for any country. That's silent unless we say
    so, and it used to be the default for Singapore.
    """
    tokens = {str(t).strip().lower() for t in locales}
    if ALL in tokens or any(t not in (AUTO, NONE, "") for t in tokens):
        return None
    countries = ", ".join(l for l in available_locales() if l != UNIVERSAL)
    return ("Phone numbers, postcodes and format-only IDs are only checked for the "
            "countries you choose. Add yours, e.g. --redact-locales auto,SG "
            f"(available: {countries}).")


def parse_locales(value: Optional[str]) -> "List[str]":
    """Split a ``--redact-locales`` value; empty or None gives the default."""
    if value is None or not value.strip():
        return list(DEFAULT_LOCALES)
    return [s.strip() for s in value.split(",") if s.strip()]


def mask(value: str) -> str:
    """Mask a value for safe display in a report (keep shape, hide content)."""
    if "@" in value:  # email: keep first char + domain
        local, _, domain = value.partition("@")
        head = local[0] if local else ""
        return f"{head}***@{domain}"
    stripped = value.strip()
    if len(stripped) <= 4:
        return "*" * len(stripped)
    return f"{stripped[:2]}{'*' * (len(stripped) - 3)}{stripped[-1]}"


def scan_text(text: str, locales: Optional[Iterable[str]] = None) -> "List[Finding]":
    """Return all sensitive-data findings in ``text`` (non-destructive)."""
    findings: List[Finding] = []
    for det in iter_detectors(locales):
        # A detector may match surrounding context but expose only the sensitive
        # span via a named ``id`` group (e.g. require "NRIC" before the number,
        # but report just the number). Otherwise the whole match is the value.
        report_id = "id" in det.pattern.groupindex
        for m in det.pattern.finditer(text):
            start, end = m.span("id") if report_id else m.span()
            if start == -1:  # an optional ``id`` group that didn't match this hit
                continue
            value = m.group("id") if report_id else m.group()
            if det.validator and not det.validator(value):
                continue
            findings.append(
                Finding(
                    detector=det.name,
                    category=det.category,
                    locale=det.locale,
                    severity=det.severity,
                    start=start,
                    end=end,
                    value=value,
                    preview=mask(value),
                )
            )
    return findings


# Checksum helpers live in their own module; re-exported for existing imports.
from ingest.redaction.checksums import luhn_valid  # noqa: E402,F401

# Load every detector module in this package. A new ``<cc>.py`` lights up
# automatically. Modules starting with "_" and the helpers module are skipped.
for _info in sorted(pkgutil.iter_modules(__path__), key=lambda i: i.name):
    if not _info.name.startswith("_") and _info.name != "checksums":
        importlib.import_module(f"{__name__}.{_info.name}")
