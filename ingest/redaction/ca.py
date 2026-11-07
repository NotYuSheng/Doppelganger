"""Canada (CA) sensitive-data detectors."""

from ingest.redaction import make
from ingest.redaction.checksums import digits_of, luhn_valid


def sin_valid(value: str) -> bool:
    """Social Insurance Number: 9 digits, Luhn-valid, first digit not 0 or 8
    (never assigned)."""
    digits = digits_of(value)
    return len(digits) == 9 and digits[0] not in "08" and luhn_valid(digits, 9, 9)


# Grouped 3-3-3 with spaces or dashes. A single Luhn digit over plain digits is
# standard precision (CA only).
make(
    "ca_sin",
    "SIN",
    "CA",
    r"(?<![\d-])\d{3}[ -]\d{3}[ -]\d{3}(?![\d-])",
    severity="high",
    validator=sin_valid,
    description="Canadian Social Insurance Number (grouped 3-3-3, Luhn-checked).",
    reference="Employment and Social Development Canada SIN format",
    # 046 454 286 is the government's published sample, but real SINs never
    # start with 0, so it's rejected by design (a counter-example below).
    examples=("sin 130 421 985", "sin 130-421-985"),
    counter_examples=("sin 130 421 986", "sin 046 454 286", "sin 846 454 286"),
)
