"""State-owned static source descriptions, separate from activation legality."""
from dataclasses import dataclass

from .source_template import static_mana_template
from ..game_state.modifier import only_empty_builtin_sources
from ..game_state.registers.card_register import IK_ZONE, IK_CONTROLLER, IK_ABILITY_KIND
from ..game_actions.data_structs.ability import (
    ManaAbilityDefinition, PlayLandAbilityDefinition, CastSpellAbilityDefinition,
    ActivatedAbilityDefinition,
)


def definition_signature(definition):
    # Unknown descriptors may read arbitrary state without notifying a register.
    if type(definition) not in (ManaAbilityDefinition, PlayLandAbilityDefinition,
                                CastSpellAbilityDefinition, ActivatedAbilityDefinition):
        return None
    return (definition.key, definition.is_mana_ability, definition.uses_stack,
            definition.allowed_zones, id(definition.subdefs),
            id(definition.cost_subdefs), id(definition.action_subdefs))


@dataclass(frozen=True)
class _Entry:
    token: tuple
    cards: tuple
    inputs: tuple
    definitions: tuple
    templates: tuple
    effects: tuple
    sources: tuple


class StaticManaSourceCache:
    """Bounded by players. Entries contain no payments or availability results.

    Membership revisions are read after synchronization. Per-card immutable
    table identities catch replacements which leave membership unchanged, and
    live definition/template checks cover mutable authoring objects. Opaque
    register tokens also distinguish writes after rollback.
    """

    def __init__(self):
        self.entries = {}
        self.miss_reason = "cold"

    @staticmethod
    def supported(state):
        from ..game_state.state import State
        from ..game_state.registers.card_register import CardRegister
        return (type(state) is State and type(state.card_register) is CardRegister
                and "query_cards" not in vars(state)
                and "query" not in vars(state.card_register)
                and "synchronise" not in vars(state.card_register)
                and not state._refreshing_effects and not state.card_register._synchronising
                and state._layer_ceiling is None)

    @staticmethod
    def token(state):
        return state.card_register.membership_token((IK_ZONE, IK_CONTROLLER, IK_ABILITY_KIND))

    def lookup(self, state, player):
        if not self.supported(state):
            self.miss_reason = "unsupported"
            return None
        entry = self.entries.get(player)
        if entry is None:
            self.miss_reason = "cold"
            return None
        if entry.token != self.token(state):
            self.miss_reason = "membership"
            return None
        for card, stats, definition, revision, zone in entry.inputs:
            if (card.stats is not stats or card.definition is not definition
                    or card.zone_revision != revision or card.get_zone() != zone
                    or card.state.counters or not only_empty_builtin_sources(card.modifier_sources)):
                self.miss_reason = "card_inputs"
                return None
        if any(definition_signature(definition) != signature for definition, signature in entry.definitions):
            self.miss_reason = "definition"
            return None
        if any(static_mana_template(template.definition) is not template for template in entry.templates):
            self.miss_reason = "template"
            return None
        return entry.sources

    def remember(self, state, player, cards, definitions, templates, sources):
        if not self.supported(state):
            return
        # A source card can have several definitions and many cards share them.
        definitions = tuple({id(definition): definition for definition in definitions}.values())
        signatures = tuple((definition, definition_signature(definition)) for definition in definitions)
        if any(signature is None for _, signature in signatures):
            self.entries.pop(player, None)
            return
        templates = tuple({id(template): template for template in templates}.values())
        effects = tuple(effect for template in templates for effect in (*template.costs, *template.effects))
        self.entries[player] = _Entry(
            self.token(state), tuple(cards),
            tuple((card, card.stats, card.definition, card.zone_revision, card.get_zone()) for card in cards),
            signatures, templates, effects, tuple(sources),
        )
