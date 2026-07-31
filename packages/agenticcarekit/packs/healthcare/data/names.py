"""Curated name word lists.

Used by ``synthetic.py`` to generate deterministic fake patient/staff names,
and by ``phi.py`` (the PHI redactor) as a same-list signal for catching
``Firstname Lastname`` mentions that lack an honorific or other context cue.
Sharing one list between generator and redactor is intentional: it is the
easiest way to prove the redactor actually catches what the generator plants.

None of these are real patients. They are common first/last names chosen
for plausibility only.
"""

FIRST_NAMES: list[str] = [
    "James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael",
    "Linda", "David", "Elizabeth", "William", "Barbara", "Richard", "Susan",
    "Joseph", "Jessica", "Thomas", "Sarah", "Charles", "Karen", "Maria",
    "Nancy", "Daniel", "Lisa", "Matthew", "Betty", "Anthony", "Margaret",
    "Priya", "Wei", "Fatima", "Carlos", "Aisha", "Kenji", "Sofia", "Omar",
    "Grace", "Miguel", "Hana", "Andre", "Jane",
]

LAST_NAMES: list[str] = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez",
    "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin",
    "Lee", "Perez", "Thompson", "White", "Harris", "Sanchez", "Clark",
    "Ramirez", "Lewis", "Robinson", "Patel", "Nguyen", "Kim", "Chen",
    "Okafor", "Haddad", "Suzuki", "Kowalski", "Dubois", "Osei", "Doe",
]

# A few disease/syndrome eponyms that pattern-match like "Honorific + Name"
# but are NOT PHI (they name a condition, not a person present in the
# document). Kept small and curated deliberately — this is the blocklist
# that lets the redactor tell "Dr. Alzheimer" (rare, would be a name) apart
# from "Alzheimer's disease" (never a name in this corpus).
EPONYM_CONDITIONS: list[str] = [
    "alzheimer", "alzheimer's", "parkinson", "parkinson's", "crohn",
    "crohn's", "graves", "grave's", "addison", "addison's", "hodgkin",
    "hodgkin's", "asperger", "asperger's", "down", "down's",
]

HONORIFICS: list[str] = ["Mr.", "Mrs.", "Ms.", "Mx.", "Dr.", "Miss"]
