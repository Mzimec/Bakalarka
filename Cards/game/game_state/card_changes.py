"""Narrow runtime notifications; unknown mutations still invalidate all indexes."""
from enum import Enum, auto


class CardChange(Enum):
    TAPPED = auto()
    DAMAGE = auto()
    SKIP_UNTAP = auto()
