"""
Rupees text to integer paisa: the ONE strict gate for money typed by a person.

    parse_rupees_to_paisa("1,250.50") -> 125050

Rules (money is never "repaired"):
  * only digits, an optional thousands comma pattern (1,250 or the Pakistani 1,00,000), and an optional
    decimal part of 1 or 2 digits are accepted; an optional "Rs", "Rs." or "PKR" in front is ignored
  * Urdu/Arabic digits are read as the digits they are (۱۲۵۰ is 1250), including the Arabic decimal and
    thousands separators
  * a third decimal digit is an error (we do not round), and so are zero, negatives, exponents (1e3),
    letters, and anything above the 10^12 paisa cap
  * a JSON number is accepted only if it is a whole number of rupees; floats are refused on purpose
No floats are used anywhere, so there is no rounding error to worry about.
"""
import re
import unicodedata

from app.database.models import MAX_AMOUNT_PAISA


class MoneyError(ValueError):
    """`code` is stable: empty, format, too_many_decimals, not_positive, too_large."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


_ARABIC_INDIC = {ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")}
_EXT_ARABIC_INDIC = {ord(c): str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")}
_SEPARATORS = {ord("٫"): ".", ord("٬"): ","}
_PREFIX = re.compile(r"^(?:pkr|rs\.?|₨)\s*", re.IGNORECASE)
_SHAPE = re.compile(r"^(?P<whole>\d{1,3}(?:,\d{2,3})+|\d+)(?:\.(?P<frac>\d+))?$", re.ASCII)


def parse_rupees_to_paisa(value) -> int:
    if isinstance(value, bool):
        raise MoneyError("format")
    if isinstance(value, int):
        whole, frac = str(value), ""
        if value < 0:
            raise MoneyError("not_positive")
    elif isinstance(value, str):
        text = unicodedata.normalize("NFKC", value).strip()
        text = text.translate(_ARABIC_INDIC).translate(_EXT_ARABIC_INDIC).translate(_SEPARATORS)
        text = _PREFIX.sub("", text, count=1).strip()
        if not text:
            raise MoneyError("empty")
        match = _SHAPE.match(text)
        if match is None:
            raise MoneyError("format")
        whole = match.group("whole").replace(",", "")
        frac = match.group("frac") or ""
    else:
        raise MoneyError("format")

    if len(frac) > 2:
        raise MoneyError("too_many_decimals")
    if len(whole.lstrip("0")) > 12:                      # far above the cap; also stops silly-long digit strings
        raise MoneyError("too_large")
    paisa = int(whole) * 100 + int(frac.ljust(2, "0") or "0")
    if paisa <= 0:
        raise MoneyError("not_positive")
    if paisa > MAX_AMOUNT_PAISA:
        raise MoneyError("too_large")
    return paisa
