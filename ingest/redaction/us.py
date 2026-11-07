"""United States (US) sensitive-data detectors."""

from ingest.redaction import make
from ingest.redaction.checksums import digits_of


def ssn_valid(value: str) -> bool:
    """SSN structure rules: the area (first 3) is never 000, 666 or 900-999,
    the group (middle 2) never 00, the serial (last 4) never 0000.
    There is no check digit."""
    digits = digits_of(value)
    if len(digits) != 9:
        return False
    area, group, serial = digits[:3], digits[3:5], digits[5:]
    return area not in ("000", "666") and area[0] != "9" and group != "00" and serial != "0000"


# Only the dashed form (123-45-6789): a bare 9-digit run is far too common.
make(
    "us_ssn",
    "SSN",
    "US",
    r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])",
    severity="high",
    validator=ssn_valid,
    description="US Social Security Number, dashed form, with SSA structure rules.",
    reference="SSA: SSN randomization / never-issued ranges",
    examples=("ssn 219-09-9999", "my social is 123-45-6789"),
    counter_examples=("ssn 000-12-3456", "ssn 666-12-3456", "ssn 912-34-5678",
                      "ssn 123-00-4567", "phone 555-123-4567"),
)
