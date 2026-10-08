"""Small text helpers shared by parsing, reconciliation and output."""

from __future__ import annotations

import re

_NUMBER = re.compile(r"[-+]?(?:\d{1,3}(?:[ ,]\d{3})+|\d+)(?:[.,]\d+)?")


def norm_key(value: object) -> str:
    """Case-, space- and punctuation-insensitive key: 'Harvest Plus', 'HarvestPlus', 'HARVESTPLUS' -> 'harvestplus'."""
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def tidy(value: str) -> str:
    """Collapses whitespace; ALL-CAPS or all-lowercase words get title case ('WHEAT' -> 'Wheat')."""
    value = " ".join(str(value).split())
    if value and (value.isupper() or value.islower()) and len(value) > 3:
        return value.title()
    return value


def parse_number(text: str | None) -> float | None:
    """'7800', '7,800', '7 800' -> 7800; '7.8' and '7,8' (decimal comma) -> 7.8; anything else -> None."""
    if text is None:
        return None
    match = _NUMBER.search(str(text))
    if match is None:
        return None
    raw = match.group().replace(" ", "")
    if re.fullmatch(r"[-+]?\d{1,3}(,\d{3})+(\.\d+)?", raw):
        raw = raw.replace(",", "")
    elif raw.count(",") == 1 and "." not in raw:
        raw = raw.replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def split_value_unit(text: str | None) -> tuple[str | None, str | None]:
    """'7.8 t/ha' -> ('7.8', 't/ha'); '7800' -> ('7800', None); '' -> (None, None)."""
    if text is None:
        return None, None
    text = str(text).strip()
    match = _NUMBER.search(text)
    if match is None:
        return None, None
    unit = text[match.end() :].strip().rstrip(".").strip() or None
    return match.group().strip(), unit


def first_year(text: str | None) -> int | None:
    match = re.search(r"\b(1[89]\d{2}|2[01]\d{2})\b", str(text or ""))
    return int(match.group()) if match else None


def fmt_num(value: float | None, decimals: int = 2) -> str:
    """Rounded without trailing zeros: 8.0 -> '8', 7.85 -> '7.85', 7800 -> '7800'."""
    if value is None:
        return ""
    text = f"{value:.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def natural_key(value: str) -> tuple:
    return tuple(int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value))
