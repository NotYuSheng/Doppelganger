"""United Kingdom (GB) sensitive-data detectors."""

import re

from ingest.redaction import make

# National Insurance number: 2 letters, 6 digits, a final letter A-D.
# First letter not D, F, I, Q, U or V; second letter not D, F, I, O, Q, U or V;
# the prefixes BG, GB, KN, NK, NT, TN and ZZ are never issued. No check digit.
_NINO_BAD_PREFIXES = {"BG", "GB", "KN", "NK", "NT", "TN", "ZZ"}


def nino_valid(value: str) -> bool:
    s = "".join(value.split()).upper()
    return len(s) == 9 and s[:2] not in _NINO_BAD_PREFIXES


make(
    "gb_nino",
    "NATIONAL_INSURANCE_NUMBER",
    "GB",
    r"\b[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z] ?\d{2} ?\d{2} ?\d{2} ?[A-D]\b",
    severity="high",
    flags=re.IGNORECASE,
    validator=nino_valid,
    description="UK National Insurance number (format and prefix rules; no check digit).",
    reference="HMRC National Insurance Manual NIM39110",
    examples=("NI number AB 12 34 56 C", "nino ab123456c"),
    counter_examples=("ref GB123456A", "code QQ123456C", "id AB123456E"),
)
