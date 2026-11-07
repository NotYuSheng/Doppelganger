"""Malaysia (MY) sensitive-data detectors."""

from ingest.redaction import make
from ingest.redaction.checksums import digits_of, valid_date

# Place-of-birth codes that are never issued.
_BAD_PLACE_CODES = {0, 17, 18, 19, 20, 69, 70, 73, 80, 81, 94, 95, 96, 97}


def mykad_valid(value: str) -> bool:
    """MyKad number YYMMDD-PB-###G: a real date of birth (19xx or 20xx) and an
    issued place-of-birth code. There is no check digit."""
    digits = digits_of(value)
    if len(digits) != 12:
        return False
    yy, mm, dd, pb = int(digits[:2]), int(digits[2:4]), int(digits[4:6]), int(digits[6:8])
    if pb in _BAD_PLACE_CODES:
        return False
    return valid_date(1900 + yy, mm, dd) or valid_date(2000 + yy, mm, dd)


# Only the dashed form: the embedded date and place code in a fixed 6-2-4
# layout make it high precision.
make(
    "my_mykad",
    "MYKAD",
    "MY",
    r"(?<![\d-])\d{6}-\d{2}-\d{4}(?![\d-])",
    severity="high",
    validator=mykad_valid,
    precision="high",
    description="Malaysian MyKad number, dashed form (date of birth + place code checked).",
    reference="JPN MyKad number structure",
    examples=("ic 900101-14-5678",),
    counter_examples=("ic 901301-14-5678", "ic 900101-00-5678"),
)
