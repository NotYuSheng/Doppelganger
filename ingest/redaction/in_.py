"""India (IN) sensitive-data detectors.

(The module is ``in_.py`` because ``in`` is a Python keyword.)
"""

from ingest.redaction import make
from ingest.redaction.checksums import verhoeff_valid

# Aadhaar: 12 digits, first digit 2-9, last digit a Verhoeff check digit.
# Usually written 4-4-4. One check digit over a plain digit run still lets ~10%
# of random 12-digit numbers through, so it's standard precision (IN only).
make(
    "in_aadhaar",
    "AADHAAR",
    "IN",
    r"(?<!\d)[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}(?!\d)",
    severity="high",
    validator=verhoeff_valid,
    description="Indian Aadhaar number (12 digits, Verhoeff check digit).",
    reference="UIDAI Aadhaar numbering; Verhoeff algorithm",
    examples=("aadhaar 2345 6789 0124",),
    counter_examples=("aadhaar 2345 6789 0123", "aadhaar 1234 5678 9012"),
)

# PAN (Permanent Account Number): 5 letters, 4 digits, 1 letter. The 4th letter
# is the holder type (P person, C company, H HUF, F firm, A AOP, T trust,
# B BOI, L local authority, J artificial juridical person, G government).
# No check digit.
make(
    "in_pan",
    "PAN",
    "IN",
    r"\b[A-Z]{3}[PCHFATBLJG][A-Z]\d{4}[A-Z]\b",
    severity="high",
    description="Indian Permanent Account Number (format only).",
    reference="Income Tax Department PAN structure",
    examples=("pan ABCPE1234F",),
    counter_examples=("pan ABCXE1234F", "code abcpe1234f"),
)
