"""Locale-independent detectors: same format the world over.

Email, payment cards, IBANs, IP/MAC addresses and vendor API keys don't change
by country, so they live here and always run, whatever ``--redact-locales``
says. Country-specific catches (national IDs, local phone formats, postal
codes) belong in a per-country module instead.
"""

import re

from ingest.redaction import UNIVERSAL, make
from ingest.redaction.checksums import iban_valid, luhn_valid

# --- Contact / network -------------------------------------------------------

make(
    "email",
    "EMAIL",
    UNIVERSAL,
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
    severity="medium",
    precision="high",
    description="Email address.",
    reference="RFC 5322 (simplified)",
    examples=("mail john.doe@acme.co please",),
    counter_examples=("meet @ the cafe", "user@localhost"),
)

make(
    "ipv4",
    "IP_ADDRESS",
    UNIVERSAL,
    r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b",
    severity="low",
    precision="high",
    description="IPv4 address.",
    reference="RFC 791",
    examples=("server at 192.168.1.20",),
    counter_examples=("version 1.2.3", "300.1.1.1"),
)

make(
    "ipv6",
    "IP_ADDRESS",
    UNIVERSAL,
    r"\b(?:[A-Fa-f0-9]{1,4}:){2,7}[A-Fa-f0-9]{1,4}\b",
    severity="low",
    precision="high",
    description="IPv6 address (full or partly compressed form).",
    reference="RFC 4291",
    examples=("ip 2001:db8:85a3:0:0:8a2e:370:7334",),
    counter_examples=("time 12:30",),
)

make(
    "mac",
    "MAC_ADDRESS",
    UNIVERSAL,
    r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b",
    severity="low",
    precision="high",
    description="MAC address.",
    reference="IEEE 802",
    examples=("mac 00:1A:2B:3C:4D:5E",),
    counter_examples=("00:1A:2B",),
)

# --- Financial ---------------------------------------------------------------

# Broad 13–19 digit run (optionally space/dash grouped); Luhn rejects the noise.
make(
    "credit_card",
    "CARD_NUMBER",
    UNIVERSAL,
    r"\b(?:\d[ -]?){13,19}\b",
    severity="high",
    validator=luhn_valid,
    precision="high",
    description="Payment card number (13-19 digits, Luhn-checked).",
    reference="ISO/IEC 7812-1",
    examples=("card 4111 1111 1111 1111",),
    counter_examples=("card 4111 1111 1111 1112",),
)

# IBAN: country code, 2 check digits, then up to 30 letters/digits, often typed
# in groups of four. The mod-97 check makes random matches very unlikely.
make(
    "iban",
    "BANK_ACCOUNT",
    UNIVERSAL,
    r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b",
    severity="high",
    validator=iban_valid,
    precision="high",
    description="International Bank Account Number (mod-97 checked).",
    reference="ISO 13616",
    examples=("iban GB82 WEST 1234 5698 7654 32", "DE89370400440532013000"),
    counter_examples=("GB82 WEST 1234 5698 7654 33",),
)

# --- Secrets / credentials ---------------------------------------------------

make(
    "openai_key", "API_KEY", UNIVERSAL, r"\bsk-[A-Za-z0-9]{20,}\b",
    severity="high", precision="high", description="OpenAI-style secret key.",
    reference="vendor key format",
    examples=("key sk-abcdefghijklmnopqrstuvwx",), counter_examples=("sk-short",),
)
make(
    "aws_access_key", "API_KEY", UNIVERSAL, r"\bAKIA[0-9A-Z]{16}\b",
    severity="high", precision="high", description="AWS access key id.",
    reference="AWS IAM key format",
    examples=("AKIAIOSFODNN7EXAMPLE",), counter_examples=("AKIA1234",),
)
make(
    "github_token",
    "API_KEY",
    UNIVERSAL,
    r"\bgh[pousr]_[A-Za-z0-9]{36,}\b",
    severity="high",
    precision="high",
    description="GitHub token.",
    reference="GitHub token formats",
    examples=("ghp_" + "a" * 36,),
    counter_examples=("ghp_short",),
)
make(
    "private_key_block",
    "PRIVATE_KEY",
    UNIVERSAL,
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----",
    severity="high",
    flags=re.IGNORECASE,
    precision="high",
    description="PEM private key header.",
    reference="RFC 7468",
    examples=("-----BEGIN RSA PRIVATE KEY-----",),
    counter_examples=("-----BEGIN PUBLIC KEY-----",),
)
