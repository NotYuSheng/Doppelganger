"""China (CN) sensitive-data detectors."""

import re

from ingest.redaction import make
from ingest.redaction.checksums import valid_date

_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CHECK = "10X98765432"


def resident_id_valid(value: str) -> bool:
    """18-character Resident Identity Card number (GB 11643-1999): 6-digit
    region code, 8-digit birth date, 3-digit sequence, ISO 7064 MOD 11-2
    check character (0-9 or X)."""
    s = value.upper()
    if len(s) != 18 or not s[:17].isdigit():
        return False
    if not valid_date(int(s[6:10]), int(s[10:12]), int(s[12:14])) or not 1900 <= int(s[6:10]) <= 2100:
        return False
    total = sum(int(d) * w for d, w in zip(s[:17], _ID_WEIGHTS))
    return _ID_CHECK[total % 11] == s[17]


# A real birth date inside the number plus the check character make this
# high precision.
make(
    "cn_resident_id",
    "RESIDENT_ID",
    "CN",
    r"(?<!\d)[1-9]\d{16}[\dX](?![\dA-Za-z])",
    severity="high",
    flags=re.IGNORECASE,
    validator=resident_id_valid,
    precision="high",
    description="Chinese Resident Identity Card number (birth date + MOD 11-2 check).",
    reference="GB 11643-1999",
    examples=("身份证 11010519491231002X", "id 11010519491231002x"),
    counter_examples=("id 110105194912310021", "id 110105194913310020"),
)
