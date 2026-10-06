"""Declarative continuous targets with explicit, narrow runtime dependencies."""
from dataclasses import dataclass

from .target_spec import TargetSpec
from ..enums import ZoneType


@dataclass(frozen=True)
class CardIncarnationsSpec(TargetSpec):
    """The selected battlefield incarnations, never a later return of a card."""
    incarnations: tuple

    def dependency_targets(self, source, controller, state):
        return frozenset(card for card, revision in self.incarnations
                         if state.card_register.get_by_key(card.key) is card
                         and card.zone_revision == revision
                         and card.get_zone() == ZoneType.BATTLEFIELD)

    def generate_candidates(self, source, controller, state, reserved=None):
        # Keep query-engine ordering and membership semantics for actual reads.
        from helper.query_system.query import EqQuery, InQuery
        from ..game_state.registers.card_register import IK_KEY, IK_ZONE
        selected = self.dependency_targets(source, controller, state)
        query = (EqQuery(IK_ZONE, ZoneType.BATTLEFIELD)
                 & InQuery(IK_KEY, frozenset(card.key for card in selected)))
        for card in state.query_cards(query):
            if card in selected and (not reserved or card not in reserved):
                yield card


@dataclass(frozen=True)
class SourceControllerLifeSpec(TargetSpec):
    """The source itself while its controller meets a life threshold."""
    minimum: int

    def dependency_targets(self, source, controller, state):
        return (frozenset({source}) if controller.health >= self.minimum
                and state.card_register.get_by_key(source.key) is source
                and source.get_zone() == ZoneType.BATTLEFIELD else frozenset())

    def generate_candidates(self, source, controller, state, reserved=None):
        for card in self.dependency_targets(source, controller, state):
            if not reserved or card not in reserved:
                yield card
