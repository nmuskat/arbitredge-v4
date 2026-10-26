"""
identity/normalize.py

Conservative text normalization used ONLY to equalize harmless formatting
differences (case, punctuation, extra whitespace) between otherwise-
identical alias strings.

This performs NO semantic expansion:
  - it does NOT turn "St." into "State" (Ohio St. vs Ohio State are
    different real teams' abbreviation styles and must never be silently
    bridged)
  - it does NOT strip disambiguating parenthetical text like "(FL)"/"(OH)"
  - it is never used for fuzzy/edit-distance matching -- only for exact
    string comparison after normalization

If two alias strings normalize to the same value, they are treated as the
same string. If they don't, they are treated as different strings, full
stop -- no partial/substring/similarity matching exists anywhere in this
module or in team_resolver.py.
"""

import re
import unicodedata


def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = text.lower()
    text = text.replace("&", " and ")
    text = text.replace("\u2019", "").replace("'", "")  # curly + straight apostrophe
    text = re.sub(r"\.", "", text)               # "St." -> "St" (period only, no expansion)
    text = re.sub(r"[^a-z0-9()]+", " ", text)     # collapse remaining punctuation to space,
                                                    # but keep parentheses (real disambiguators)
    text = re.sub(r"\s+", " ", text).strip()
    return text
