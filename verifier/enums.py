"""Shared enumerations for logical relations and numeric comparison outcomes."""
from enum import Enum


class Relation(str, Enum):
    """Logical relation of a proposition to the evidence (never a policy finding)."""
    ENTAILED = "entailed"
    CONTRADICTED = "contradicted"
    UNKNOWN = "unknown"


class Outcome(str, Enum):
    """Result of comparing a claimed number with a source number."""
    MATCH = "match"
    UNCLEAR = "unclear"
    MISMATCH = "mismatch"
