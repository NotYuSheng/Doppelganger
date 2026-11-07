# Sensitive-data detectors

Before training, ingest scans every conversation for sensitive data (emails,
card numbers, national ID numbers, phone numbers, API keys, …) and writes a
report. With `--redact replace|drop` the findings are removed. This page
explains how detectors are chosen at run time and how to write new ones,
especially for countries that aren't covered yet.

Code: [`ingest/redaction/`](../ingest/redaction/). One module per country
(`<cc>.py`, ISO 3166-1 alpha-2, lower-case), plus `universal.py` for formats
that are the same everywhere and `checksums.py` for shared check-digit
algorithms.

## Coverage

| Locale | Detector | What | Precision |
|---|---|---|---|
| universal | `email`, `ipv4`, `ipv6`, `mac` | contact / network identifiers | high |
| universal | `credit_card` | card numbers (Luhn) | high |
| universal | `iban` | bank account numbers (ISO 13616 mod 97) | high |
| universal | `openai_key`, `aws_access_key`, `github_token`, `private_key_block` | credentials | high |
| BR | `br_cpf` | CPF taxpayer number, punctuated, both check digits | high |
| CA | `ca_sin` | Social Insurance Number, grouped, Luhn | standard |
| CN | `cn_resident_id` | Resident ID card, birth date + MOD 11-2 check | high |
| GB | `gb_nino` | National Insurance number, format and prefix rules | standard |
| IN | `in_aadhaar` | Aadhaar, Verhoeff check digit | standard |
| IN | `in_pan` | PAN, format | standard |
| MY | `my_mykad` | MyKad, dashed, birth date + place code | high |
| SG | `sg_nric` | NRIC/FIN, all series incl. M (2022+), check letter | high |
| SG | `sg_nric_short`, `sg_phone`, `sg_postal`, `sg_uen` | partial NRIC, phone, postcode, UEN | standard |
| US | `us_ssn` | SSN, dashed, SSA structure rules | standard |

## Which detectors run

Each detector has a **precision** tier:

- **high**: has a checksum *and* a structure random text rarely has (letters
  mixed with digits, an embedded date of birth), or two or more check digits.
  False positives are rare, so these run for **every user, whatever country
  they're in**. People chat with friends abroad, so a Singapore user's chats
  can still contain a Chinese resident ID.
- **standard**: format-only patterns, a single check digit over a plain digit
  run, phone numbers, postcodes. Useful, but they'd misfire on unrelated
  numbers for someone in another country, so they run only when their country
  is selected.

`--redact-locales` (comma-separated) chooses:

| Value | Runs |
|---|---|
| `auto` (default) | universal + every country's **high** detectors |
| `auto,US,IN` | the above + **all** US and IN detectors |
| `SG` | universal + all SG detectors (the pre-`auto` behaviour) |
| `all` | everything |
| `none` | universal only |

Universal detectors always run. Pick your own country (and those of people you
chat with a lot) on top of `auto` for the best coverage. If you don't name any
country, the scan summary says so and shows how to add one.

## Writing a detector

A detector is a regex plus metadata, registered with `make(...)`:

```python
from ingest.redaction import make
from ingest.redaction.checksums import luhn_valid

make(
    "xx_national_id",            # <cc>_<what>, unique
    "NATIONAL_ID",               # category shown in the report / used as [PLACEHOLDER]
    "XX",                        # ISO 3166-1 alpha-2 country code
    r"(?<!\d)\d{3}-\d{3}-\d{3}(?!\d)",
    severity="high",             # low | medium | high: how bad a leak would be
    validator=my_check,          # checksum / date / code rules on the matched text
    precision="standard",        # high | standard: see above
    description="One line on what this is.",
    reference="Where the format/checksum is specified (law, agency page, standard).",
    examples=("id 123-456-782",),           # MUST be detected
    counter_examples=("id 123-456-789",),   # must NOT be (e.g. wrong check digit)
)
```

Save it as `ingest/redaction/xx.py`. It's picked up automatically: no import
list to edit, and no change to the scanner or pipeline. (If the country code is
a Python keyword, add an underscore: India is `in_.py`.)

### Rules of thumb

1. **Start from the official specification** and put it in `reference`.
   Community parsers and Wikipedia are fine for orientation, but check digits
   and reserved ranges must come from the issuer where possible. If the rule
   isn't officially published (like Singapore's M-series), say whose
   implementation you followed.
2. **Use a validator whenever the identifier has one**: a check digit, an
   embedded date that must be real, ranges that are never issued. This is what
   keeps the report trustworthy instead of a wall of false positives. Shared
   algorithms (Luhn, mod 97, Verhoeff, date checks) are in `checksums.py`; add
   new ones there with a link to their spec.
3. **Prefer the written form people actually type** (dashes, spaces, dots)
   over bare digit runs, which collide with phone numbers, order numbers and
   timestamps. Use lookarounds (`(?<!\d)…(?!\d)`) so you don't match a slice
   of a longer number.
4. **No checksum and noisy? Require context.** Put the keyword in the pattern
   and the sensitive part in a named group `(?P<id>…)`; only the group is
   reported and redacted. See `sg_nric_short`.
5. **Choose `precision` honestly.** `high` means it will run for every user in
   the world by default. Only use it with a validator and a distinctive
   structure. When in doubt, use `standard`.
6. **Give real examples and near-miss counter-examples**: the same number with
   a wrong check digit, a reserved range, a similar-looking phone number. Don't
   use real people's numbers; use the issuer's published samples or
   synthetic ones that pass the checksum.

### Testing

[`tests/test_detectors.py`](../tests/test_detectors.py) runs every registered
detector against its own `examples` and `counter_examples`, and checks its
metadata (description, reference, naming, a validator for `high` country
detectors). A new country module is tested as soon as it exists. Add
detector-specific tests only for logic the examples can't express (e.g. a
validator's edge cases).

```bash
python -m unittest tests.test_detectors
```

Countries with good, documented identifiers that would make useful
contributions: Japan (My Number), South Korea (RRN), Indonesia (NIK),
Philippines, Thailand (citizen ID check digit), Vietnam, Australia (TFN,
Medicare), Germany (Steuer-ID), France (NIR), Spain (DNI/NIE), Italy (codice
fiscale), Mexico (CURP/RFC), South Africa (ID number).
