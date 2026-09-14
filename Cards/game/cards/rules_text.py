"""Offline descriptive card text; executable abilities remain the rules engine."""

from functools import lru_cache
import json
from game.paths import DATA_DIR


@lru_cache(maxsize=1)
def starter_rules_text():
    """!
    @brief Load the checked-in Scryfall text once for all starter definitions.
    @return Name-to-text data, independent of effect execution and game state.
    """
    path = DATA_DIR / "starter_rules_text.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {name: row["oracle_text"] for name, row in rows.items()}
