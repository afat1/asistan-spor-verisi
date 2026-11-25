"""Editorial records live only in Firestore, never in repository data files."""
from copy import deepcopy

_editorial = None


def configure(value):
    global _editorial
    required = {"boxing_main_events", "featured_combat_events", "reviewed_mens_boxing"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("Firebase editorial configuration is incomplete")
    if not isinstance(value["boxing_main_events"], dict) or not all(
        isinstance(value[key], list) for key in required - {"boxing_main_events"}
    ):
        raise ValueError("Firebase editorial configuration has invalid types")
    _editorial = deepcopy(value)


def editorial(name):
    if _editorial is None:
        raise RuntimeError("Load editorial configuration from Firebase before collecting")
    return deepcopy(_editorial[name])
