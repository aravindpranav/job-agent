"""Infer a country from a free-text location string.

Many ATS boards don't populate a structured country field — they only put the
place in a human string like ``"Bengaluru, India"`` or ``"London, UK"``. Without
this, the location filter would treat those as *unknown country* and keep them,
so non-US roles reach scoring and the results table. ``infer_country`` reads the
country back out of that string so the existing allow-list can drop them.

Design: err toward NOT dropping a US role. The city list holds only foreign
metros with no significant US namesake (so a real US city is never misread as
foreign), and when a string names both the US and a foreign country ("US or
India") the US wins. Genuinely ambiguous strings return ``None`` (unknown) and
are kept — the filter only drops what's *clearly* outside the US.
"""

from __future__ import annotations

import re

# --- foreign metros with no significant US namesake -------------------------
# Deliberately EXCLUDES names that are also US cities/towns (Cambridge, Berlin,
# Manchester, Birmingham, Melbourne, Paris, Vienna, Ontario, and — per the same
# rule — Vancouver/WA, Dublin/OH+CA, Waterloo/IA, Geneva/NY, Athens/GA, ...):
# those are disambiguated by an explicit country token instead, never by the
# city alone.
_CITY_COUNTRY: dict[str, tuple[str, ...]] = {
    "India": ("bengaluru", "bangalore", "hyderabad", "mumbai", "pune", "chennai",
              "gurgaon", "gurugram", "noida", "kolkata", "ahmedabad", "new delhi"),
    "Canada": ("toronto", "montreal", "mississauga", "calgary", "edmonton", "ottawa"),
    "United Kingdom": ("london", "edinburgh", "glasgow", "leeds", "bristol"),
    "Germany": ("munich", "münchen", "frankfurt am main", "düsseldorf"),
    "Netherlands": ("amsterdam", "rotterdam", "eindhoven"),
    "Spain": ("barcelona", "madrid"),
    "Australia": ("sydney",),
    "New Zealand": ("auckland",),
    "Japan": ("tokyo", "osaka", "kyoto"),
    "South Korea": ("seoul", "busan", "incheon", "pangyo"),
    "Taiwan": ("taipei",),
    "China": ("shanghai", "beijing", "shenzhen", "hangzhou", "guangzhou"),
    "Hong Kong": ("hong kong",),
    "Israel": ("tel aviv", "tel-aviv", "herzliya"),
    "Poland": ("warsaw", "kraków", "krakow", "wrocław", "wroclaw"),
    "Portugal": ("lisbon", "lisboa", "porto"),
    "Singapore": ("singapore",),
    "Switzerland": ("zurich", "zürich"),
    "Sweden": ("stockholm", "gothenburg"),
    "Denmark": ("copenhagen",),
    "Norway": ("oslo",),
    "Finland": ("helsinki",),
    "Belgium": ("brussels",),
    "Czechia": ("prague",),
    "Hungary": ("budapest",),
    "Romania": ("bucharest",),
    "Brazil": ("são paulo", "sao paulo"),
    "Argentina": ("buenos aires",),
    "Colombia": ("bogotá", "bogota"),
    "Indonesia": ("jakarta",),
    "Malaysia": ("kuala lumpur",),
    "Thailand": ("bangkok",),
    "Vietnam": ("hanoi", "ho chi minh"),
    "Philippines": ("manila",),
    "Nigeria": ("lagos",),
    "Kenya": ("nairobi",),
}

# --- foreign country names / aliases ----------------------------------------
_COUNTRY_ALIASES: dict[str, tuple[str, ...]] = {
    "India": ("india",),
    "Canada": ("canada",),
    "United Kingdom": ("united kingdom", "u.k.", "uk", "england", "scotland",
                       "wales", "great britain", "britain"),
    "Ireland": ("ireland",),
    "Germany": ("germany", "deutschland"),
    "France": ("france",),
    "Netherlands": ("netherlands", "holland"),
    "Spain": ("spain", "españa"),
    "Portugal": ("portugal",),
    "Italy": ("italy",),
    "Poland": ("poland",),
    "Australia": ("australia",),
    "New Zealand": ("new zealand",),
    "Japan": ("japan",),
    "China": ("china",),
    "South Korea": ("south korea", "republic of korea", "korea"),
    "Taiwan": ("taiwan",),
    "Hong Kong": ("hong kong",),
    "Singapore": ("singapore",),
    "Israel": ("israel",),
    "Brazil": ("brazil", "brasil"),
    "Mexico": ("mexico", "méxico"),
    "Switzerland": ("switzerland",),
    "Sweden": ("sweden",),
    "Denmark": ("denmark",),
    "Norway": ("norway",),
    "Finland": ("finland",),
    "Austria": ("austria",),
    "Belgium": ("belgium",),
    "Romania": ("romania",),
    "Ukraine": ("ukraine",),
    "Czechia": ("czechia", "czech republic"),
    "Hungary": ("hungary",),
    "Greece": ("greece",),
    "Turkey": ("turkey", "türkiye"),
    "United Arab Emirates": ("united arab emirates", "u.a.e.", "uae", "dubai", "abu dhabi"),
    "Philippines": ("philippines",),
    "Indonesia": ("indonesia",),
    "Malaysia": ("malaysia",),
    "Thailand": ("thailand",),
    "Vietnam": ("vietnam",),
    "Pakistan": ("pakistan",),
    "Bangladesh": ("bangladesh",),
    "Sri Lanka": ("sri lanka",),
    "Argentina": ("argentina",),
    "Colombia": ("colombia",),
    "Chile": ("chile",),
    "Peru": ("peru",),
    "Costa Rica": ("costa rica",),
    "South Africa": ("south africa",),
    "Egypt": ("egypt",),
    "Nigeria": ("nigeria",),
    "Kenya": ("kenya",),
    "Estonia": ("estonia",),
    "Lithuania": ("lithuania",),
    "Latvia": ("latvia",),
    "Bulgaria": ("bulgaria",),
    "Croatia": ("croatia",),
    "Serbia": ("serbia",),
    "Slovakia": ("slovakia",),
    "Slovenia": ("slovenia",),
}

_US_STATE_NAMES = (
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho", "illinois",
    "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine", "maryland",
    "massachusetts", "michigan", "minnesota", "mississippi", "missouri", "montana",
    "nebraska", "nevada", "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode island", "south carolina", "south dakota", "tennessee", "texas", "utah",
    "vermont", "virginia", "washington", "west virginia", "wisconsin", "wyoming",
    "district of columbia",
)
_US_STATE_ABBREV = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL",
    "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT",
    "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
}
_US_NAME_RE = re.compile(
    r"\b(u\.?\s?s\.?\s?a?\.?|usa|united states(?: of america)?)\b", re.IGNORECASE
)


def _word_present(low: str, term: str) -> bool:
    """True if ``term`` appears in ``low`` as a whole word (not a substring).

    Word boundaries keep 'india' from matching 'Indianapolis' and 'us' from
    matching 'Columbus'.
    """
    return re.search(rf"\b{re.escape(term)}\b", low) is not None


def _first_match(low: str, table: dict[str, tuple[str, ...]]) -> str | None:
    for country, terms in table.items():
        if any(_word_present(low, t) for t in terms):
            return country
    return None


def _has_us_marker(location: str) -> bool:
    low = location.lower()
    if _US_NAME_RE.search(location):
        return True
    if any(_word_present(low, name) for name in _US_STATE_NAMES):
        return True
    # State abbreviations only in ORIGINAL case, so 'in'/'or'/'me' as English
    # words never match Indiana/Oregon/Maine — only an uppercase "IN"/"OR"/"ME".
    return any(tok in _US_STATE_ABBREV for tok in re.findall(r"\b[A-Z]{2}\b", location))


def infer_country(location: str | None) -> str | None:
    """Best-effort country from a location string, or None if unclear.

    Tiers, strongest first:
      1. An unambiguous foreign metro (e.g. "Bengaluru") → that country. This
         wins over a bare abbreviation, so "Bengaluru, IN" reads as India, not
         Indiana.
      2. Country names: if a foreign country is named and the US is not, → that
         country; if the US is named (state, abbrev, or "US"), → "US" (so
         "US or India" is kept as US).
    """
    if not location or not location.strip():
        return None
    low = location.lower()

    city_country = _first_match(low, _CITY_COUNTRY)
    if city_country is not None:
        return city_country

    foreign = _first_match(low, _COUNTRY_ALIASES)
    us = _has_us_marker(location)
    if foreign is not None and not us:
        return foreign
    if us:
        return "US"
    return foreign
