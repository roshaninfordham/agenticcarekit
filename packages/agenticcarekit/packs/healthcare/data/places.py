"""Curated geography word lists for synthetic address generation.

Street/city names here are generic placeholders, not real locations tied
to real people. State names are U.S. state names — the redactor must
*never* treat a bare state name as PHI (geographic subdivisions smaller
than state are the HIPAA category; the state itself is explicitly not).
"""

STREET_NAMES: list[str] = [
    "Maple", "Oak", "Cedar", "Elm", "Pine", "Birch", "Willow", "Chestnut",
    "Walnut", "Spruce", "Magnolia", "Sycamore", "Aspen", "Poplar", "Juniper",
]

STREET_SUFFIXES: list[str] = [
    "St", "Ave", "Rd", "Blvd", "Dr", "Ln", "Way", "Ct",
]

CITIES: list[str] = [
    "Rivertown", "Fairview", "Lakeview", "Springfield", "Greenfield",
    "Millbrook", "Oakdale", "Brookhaven", "Cedar Falls", "Eastwood",
]

# Two-letter state abbreviations (not exhaustive — a representative subset
# is sufficient for synthetic generation and redactor test coverage).
STATE_ABBREVIATIONS: list[str] = [
    "CA", "NY", "TX", "FL", "WA", "IL", "PA", "OH", "GA", "NC",
]

# Full state names used in the eval set as a "this is NOT PHI" negative —
# a state name alone is not a geographic subdivision smaller than a state.
STATE_NAMES: list[str] = [
    "California", "New York", "Texas", "Florida", "Washington",
]
