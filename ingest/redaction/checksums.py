"""Check-digit algorithms shared by detectors.

Each function takes the matched string (separators allowed) and returns whether
its check digit(s) are correct. They never raise on bad input; they return
False. The specification for each is linked so it can be checked.
"""

from datetime import date


def digits_of(value: str) -> str:
    return "".join(c for c in value if c.isdigit())


def luhn_valid(number: str, min_len: int = 13, max_len: int = 19) -> bool:
    """Luhn (mod 10): payment cards, Canadian SIN, … (ISO/IEC 7812-1)."""
    digits = [int(c) for c in digits_of(number)]
    if not min_len <= len(digits) <= max_len:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def iban_valid(value: str) -> bool:
    """IBAN mod-97 check (ISO 13616): move the first 4 chars to the end, map
    letters to 10..35, and the number mod 97 must be 1."""
    s = "".join(value.split()).upper()
    if not 15 <= len(s) <= 34 or not s[:2].isalpha() or not s[2:4].isdigit() or not s.isalnum():
        return False
    rearranged = s[4:] + s[:4]
    try:
        number = int("".join(str(int(c, 36)) for c in rearranged))
    except ValueError:
        return False
    return number % 97 == 1


# Verhoeff tables (dihedral group D5), as used by India's Aadhaar.
_V_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_V_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


def verhoeff_valid(value: str) -> bool:
    """Verhoeff check digit (catches all single-digit and transposition errors)."""
    digits = digits_of(value)
    if not digits:
        return False
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _V_D[c][_V_P[i % 8][int(ch)]]
    return c == 0


def valid_date(year: int, month: int, day: int) -> bool:
    try:
        date(year, month, day)
    except ValueError:
        return False
    return True
