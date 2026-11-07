"""Brazil (BR) sensitive-data detectors."""

from ingest.redaction import make
from ingest.redaction.checksums import digits_of


def cpf_valid(value: str) -> bool:
    """CPF: 9 digits + 2 check digits, each mod 11 over descending weights
    (10..2, then 11..2); a remainder < 2 gives 0. All-same digits are invalid."""
    d = [int(c) for c in digits_of(value)]
    if len(d) != 11 or len(set(d)) == 1:
        return False
    for n in (9, 10):
        total = sum(x * w for x, w in zip(d[:n], range(n + 1, 1, -1)))
        check = 11 - total % 11
        if (0 if check >= 10 else check) != d[n]:
            return False
    return True


# Only the punctuated form 000.000.000-00: Brazilian mobile numbers are also
# 11 digits, so bare digit runs are left alone. Two check digits plus the
# fixed punctuation make it high precision.
make(
    "br_cpf",
    "CPF",
    "BR",
    r"(?<![\d.])\d{3}\.\d{3}\.\d{3}-\d{2}(?![\d-])",
    severity="high",
    validator=cpf_valid,
    precision="high",
    description="Brazilian CPF (taxpayer number), punctuated form, both check digits verified.",
    reference="Receita Federal CPF check-digit rule",
    examples=("cpf 529.982.247-25",),
    counter_examples=("cpf 529.982.247-26", "cpf 111.111.111-11"),
)
