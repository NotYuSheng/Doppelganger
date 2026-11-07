"""Singapore (SG) sensitive-data detectors.

See ``docs/redaction.md`` for how detectors are written and tested.
"""

import re

from ingest.redaction import make

# NRIC/FIN check letters. S/T/F/G: index = (weighted sum + offset) % 11.
_ST_SUFFIX = "JZIHGFEDCBA"  # S (citizen) and T (citizen, 2000+)
_FG_SUFFIX = "XWUTRQPNMLK"  # F and G (foreigner / long-term pass)
# M (foreigner, from 2022): index = 11 - ((weighted sum + 3) % 11), minus 1.
_M_SUFFIX = "KLJNPQRTUWX"
_WEIGHTS = (2, 7, 6, 5, 4, 3, 2)


def nric_valid(value: str) -> bool:
    """Validate a Singapore NRIC/FIN check letter (S, T, F, G and M series).

    The M-series rule follows GovTech FormSG's validator
    (opengovsg/FormSG ``packages/shared/utils/nric-validation.ts``).
    """
    value = value.strip().upper()
    if len(value) != 9:
        return False
    prefix, digits, suffix = value[0], value[1:8], value[8]
    if prefix not in "STFGM" or not digits.isdigit():
        return False
    total = sum(int(d) * w for d, w in zip(digits, _WEIGHTS))
    if prefix == "M":
        return _M_SUFFIX[11 - (total + 3) % 11 - 1] == suffix
    if prefix in "TG":  # T and G shift the weighted sum by 4
        total += 4
    table = _ST_SUFFIX if prefix in "ST" else _FG_SUFFIX
    return table[total % 11] == suffix


# Long form: prefix + 7 digits + check letter, verified by checksum. A letter,
# seven digits and a check letter is a structure random text rarely has, so
# this is high precision and runs for every user by default.
make(
    "sg_nric",
    "NRIC/FIN",
    "SG",
    r"\b[STFGM]\d{7}[A-Z]\b",
    severity="high",
    flags=re.IGNORECASE,
    validator=nric_valid,
    precision="high",
    description="Singapore NRIC/FIN, all series (S/T/F/G/M), check letter verified.",
    reference="ICA NRIC/FIN format; M-series per GovTech FormSG validator",
    examples=("my ic is S0000001I", "fin M1234567K", "ic s0000001i"),
    counter_examples=("code S0000001A", "fin M1234567L", "ref X1234567A"),
)

# Short form: the last 3 digits + check letter (e.g. "123A"), the way people
# quote "the last 4 of my IC". It has no checksum and "123A" alone matches
# every block/unit number, so precision comes from REQUIRING an NRIC/IC/FIN
# keyword just before it. Only the ID span (named group) is reported.
make(
    "sg_nric_short",
    "NRIC/FIN (partial)",
    "SG",
    r"(?:nric|fin|\bic\b)\D{0,8}?(?P<id>(?<!\d)\d{3}[A-Za-z])\b",
    severity="medium",
    flags=re.IGNORECASE,
    description="Last 4 characters of an NRIC/FIN, only after an NRIC/IC/FIN keyword.",
    reference="common usage ('last 4 of my IC')",
    examples=("NRIC 123A", "my IC is 567B"),
    counter_examples=("Blk 123A Clementi",),
)

# Mobile (8/9), landline (6), VoIP (3): 8 digits, optional +65. Lookarounds keep
# it from grabbing 8-digit slices of longer number runs.
make(
    "sg_phone",
    "PHONE",
    "SG",
    r"(?<!\d)(?:\+?65[\s-]?)?[3689]\d{3}[\s-]?\d{4}(?!\d)",
    severity="medium",
    description="Singapore phone number (8 digits starting 3/6/8/9, optional +65).",
    reference="IMDA National Numbering Plan",
    examples=("call 9123 4567", "call +65 9123 4567"),
    counter_examples=("order 1234 5678", "id 912345678"),
)

# Postal code is 6 bare digits, far too noisy alone, so require an explicit
# "Singapore <code>", "S123456", or "S(123456)" context. The trailing lookahead
# stops it matching the first 6 digits of a longer token (e.g. the NRIC
# "S1234567D", which would otherwise read as "S123456").
make(
    "sg_postal",
    "POSTAL_CODE",
    "SG",
    r"(?:[Ss]ingapore\s+|\bS\(?)\d{6}\)?(?![\dA-Za-z])",
    severity="low",
    description="Singapore postal code, only with 'Singapore' or an S prefix.",
    reference="SingPost postal code format",
    examples=("Singapore 560123", "address S123456"),
    counter_examples=("ic S1234567D", "code 560123"),
)

# UEN (Unique Entity Number) for businesses, companies and other entities.
# There's no published check digit, so it's format-only:
#   businesses (ACRA)  nnnnnnnnX       e.g. 53312345K
#   local companies    yyyynnnnnX      e.g. 200912345A  (yyyy = year registered)
#   other entities     [TSR]yyPQnnnnX  e.g. T09LL0001B  (PQ = entity type)
make(
    "sg_uen",
    "UEN",
    "SG",
    r"\b(?:\d{8}[A-Z]|(?:19|20)\d{2}\d{5}[A-Z]|[TSR]\d{2}[A-Z]{2}\d{4}[A-Z])\b",
    severity="low",
    description="Singapore Unique Entity Number (format only; no public check digit).",
    reference="ACRA / uen.gov.sg UEN formats",
    examples=("uen 53312345K", "company 200912345A", "society T09LL0001B"),
    counter_examples=("order 1234567A", "year 2009"),
)
