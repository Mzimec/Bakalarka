"""Counted decklists, including Arena text exports, independent of card behavior."""

from collections import Counter
from dataclasses import dataclass
from collections.abc import Mapping
import re
from game.paths import DATA_DIR

from game.game_state.card import CardDefinition

__all__ = [
    "BASIC_LANDS",
    "ARENA_STARTERS",
    "DeckList",
    "load_arena_starter",
]

BASIC_LANDS = frozenset({"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes"})
ARENA_STARTERS = {
    "white": ("Keep the Peace", "keep-the-peace"),
    "blue": ("Aerial Domination", "aerial-domination"),
    "black": ("Cold-Blooded Killers", "cold-blooded-killers"),
    "red": ("Goblins Everywhere!", "goblins-everywhere"),
    "green": ("Large and in Charge", "large-and-in-charge"),
}


@dataclass(frozen=True)
class DeckList:
    name: str
    cards: tuple[tuple[str, int], ...]

    def __post_init__(self):
        counts = Counter()
        for name, count in self.cards:
            if not isinstance(name, str) or not name.strip() or type(count) is not int or count < 1:
                raise ValueError("Deck entries need a card name and a positive integer count.")
            counts[name.strip()] += count
        object.__setattr__(self, "cards", tuple(counts.items()))

    @classmethod
    def from_arena(cls, text: str, name="Imported deck"):
        """!
        @brief Read the main deck; reject sideboards rather than silently merging them.
        """
        entries = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line == "Deck":
                continue
            if line in {"Sideboard", "Commander", "Companion"}:
                raise ValueError(f"{line} is not supported by duel deck imports.")
            match = re.fullmatch(
                r"([1-9][0-9]*)\s+(.+?)(?:\s+\([A-Za-z0-9]+\)\s+[A-Za-z0-9]+)?", line
            )
            if not match:
                raise ValueError(f"Invalid deck entry: {line}")
            entries.append((match[2], int(match[1])))
        return cls(name, tuple(entries))

    @property
    def size(self):
        return sum(count for _, count in self.cards)

    def resolve(self, catalog: Mapping[str, CardDefinition], *, minimum_size=60, maximum_copies=4):
        """!
        @brief Validate before creating any runtime objects. This is not format legality.
        """
        if self.size < minimum_size:
            raise ValueError(
                f"{self.name}: expected at least {minimum_size} cards, got {self.size}."
            )
        missing = [name for name, _ in self.cards if name not in catalog]
        if missing:
            raise ValueError(f"{self.name}: missing card definitions: {', '.join(missing)}")
        result = []
        for name, count in self.cards:
            if maximum_copies is not None and name not in BASIC_LANDS and count > maximum_copies:
                raise ValueError(f"{self.name}: too many copies of {name} ({count}).")
            definition = catalog[name]
            if not isinstance(definition, CardDefinition) or definition.name != name:
                raise ValueError(f"Catalog entry does not match {name}.")
            result.extend([definition] * count)
        return tuple(result)


def load_arena_starter(color):
    """!
    @brief Read an ANB reference main deck; card mechanics still require a catalog.
    """
    if color not in ARENA_STARTERS:
        raise ValueError(f"Unknown starter color: {color}. Choose {', '.join(ARENA_STARTERS)}.")
    name, slug = ARENA_STARTERS[color]
    path = DATA_DIR / "decks" / "arena_anb" / f"{slug}.txt"
    return DeckList.from_arena(path.read_text(encoding="utf-8"), name)
