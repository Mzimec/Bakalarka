"""Immutable deck-conditioned roles, evidence and indexed mechanic plans.

Build once from an explicitly permitted DeckList/catalog. Records retain no
runtime State/Card, model embedding or executable definition. Names only bind
catalog entries; they are never numerical model features.
"""
from collections import defaultdict
from dataclasses import dataclass, asdict, replace
from enum import Enum
from hashlib import sha256
import json

from game.ai.evaluation.card_mechanics import CardProfile, card_profile, compatible
from game.ai.evaluation.semantic_encoding import DeckSemanticEncoder
from game.cards.decks import DeckList

PROFILE_VERSION = 1


class CardRole(str, Enum):
    RESOURCE = 'resource'
    CARD_ADVANTAGE = 'card_advantage'
    SELECTION = 'selection'
    ENABLER = 'enabler'
    PAYOFF = 'payoff'
    ENGINE = 'engine'
    THREAT = 'threat'
    FINISHER = 'finisher'
    INTERACTION = 'interaction'
    PROTECTION = 'protection'
    STABILIZER = 'stabilizer'
    RECOVERY = 'recovery'


@dataclass(frozen=True, slots=True)
class RoleAssignment:
    role: CardRole
    ability: tuple
    mechanic: str
    supported_by: tuple = ()
    supports: tuple = ()
    requirements: tuple = ()
    evidence: str = ''
    link_space: str = 'deck'


@dataclass(frozen=True, slots=True)
class DeckCardProfile:
    catalog_key: str
    copies: int
    card: CardProfile
    roles: tuple
    missing_support: tuple


@dataclass(frozen=True, slots=True)
class MechanicLink:
    source: int
    source_ability: tuple
    target: int
    target_ability: tuple
    signal: object
    requirement: object
    requirements: tuple


@dataclass(frozen=True, slots=True)
class DeckPlan:
    mechanic: str
    links: tuple
    enablers: tuple
    payoffs: tuple
    # These are structural plans, not proven combos or win conditions.


@dataclass(frozen=True, slots=True)
class DeckProfile:
    entries: tuple
    links: tuple
    plans: tuple
    unknowns: tuple
    fingerprint: str
    schema_version: int = PROFILE_VERSION

    def entry(self, catalog_key):
        return next((e for e in self.entries if e.catalog_key == catalog_key), None)

    def to_dict(self):
        return asdict(self)


def basic_roles(card):
    """Explain functional roles without assigning hand-written action scores."""
    result = []
    if 'CREATURE' in card.types and (card.power or 0) > 0:
        result.append(RoleAssignment(CardRole.THREAT, (), 'combat', requirements=('can_attack_or_deal_damage',), evidence='positive_power_creature'))
    for ability in card.abilities:
        for trait, role in (('add_mana', CardRole.RESOURCE), ('draw_multiple', CardRole.CARD_ADVANTAGE),
                            ('selection', CardRole.SELECTION),
                            ('interaction', CardRole.INTERACTION), ('protection', CardRole.PROTECTION),
                            ('gain_life', CardRole.STABILIZER), ('recovery', CardRole.RECOVERY)):
            if trait in ability.traits:
                result.append(RoleAssignment(role, ability.path, trait, requirements=ability.requirements, evidence='reviewed_effect'))
        if 'draw' in ability.traits and ability.repeatable and 'draw_multiple' not in ability.traits:
            result.append(RoleAssignment(CardRole.CARD_ADVANTAGE, ability.path, 'draw', requirements=ability.requirements,
                                         evidence='repeatable_draw_potential_not_net_advantage_proof'))
    return result


class DeckProfiler:
    """Reuse immutable card analyses; deck-dependent roles never enter CardDefinition."""
    def __init__(self, encoder=None, capacity=256, *, matcher=None, analyze_tree=None):
        from collections import OrderedDict
        if type(capacity) is not int or capacity < 0:
            raise ValueError('Profile cache capacity must be nonnegative')
        self.encoder = encoder if encoder is not None else DeckSemanticEncoder()
        self.capacity, self._cards = capacity, OrderedDict()
        self.matcher = matcher if matcher is not None else compatible
        self.analyze_tree = analyze_tree if analyze_tree is not None else card_profile

    def analyze(self, definition):
        tree = self.encoder.encode(definition)
        found = self._cards.get(tree)
        if found is not None:
            self._cards.move_to_end(tree)
            return found
        found = self.analyze_tree(tree)
        if self.capacity:
            self._cards[tree] = found
            while len(self._cards) > self.capacity:
                self._cards.popitem(last=False)
        return found

    def profile(self, deck, catalog):
        if not isinstance(deck, DeckList):
            raise TypeError('DeckProfiler requires an explicitly supplied DeckList')
        records = tuple((key, count, self.analyze(catalog[key])) for key, count in sorted(deck.cards))
        by_event = defaultdict(list)
        for index, (_, _, card) in enumerate(records):
            for ability in card.abilities:
                for signal in ability.produces:
                    by_event[signal.event].append((index, ability, signal))
        links = []
        for target, (_, count, card) in enumerate(records):
            for ability in card.abilities:
                for requirement in ability.reacts_to:
                    for source, source_ability, signal in by_event[requirement.event]:
                        if self.matcher(signal, requirement, same_card=source == target, copies=count):
                            signal = signal if requirement.card_type is not None else replace(signal, card_type=None)
                            links.append(MechanicLink(source, source_ability.path, target, ability.path, signal, requirement,
                                tuple(sorted(set((*source_ability.requirements, *ability.requirements, 'event_conditions_must_hold'))))))
        links = tuple(dict.fromkeys(links))
        entries, unknowns = [], defaultdict(int)
        for index, (key, count, card) in enumerate(records):
            roles = basic_roles(card)
            incoming = [(i, link) for i, link in enumerate(links) if link.target == index]
            outgoing = [(i, link) for i, link in enumerate(links) if link.source == index]
            missing = []
            for ability in card.abilities:
                for requirement in ability.reacts_to:
                    supporting = tuple(i for i, link in incoming if link.target_ability == ability.path and link.requirement == requirement)
                    if supporting:
                        roles.append(RoleAssignment(CardRole.PAYOFF, ability.path, requirement.event,
                            supported_by=supporting, requirements=ability.requirements, evidence='matching_deck_producers'))
                        if ability.repeatable:
                            roles.append(RoleAssignment(CardRole.ENGINE, ability.path, requirement.event,
                                supported_by=supporting, requirements=ability.requirements, evidence='repeatable_supported_trigger_potential'))
                            if 'player_damage' in ability.traits and any(next(a for a in records[links[i].source][2].abilities
                                    if a.path == links[i].source_ability).repeatable for i in supporting):
                                roles.append(RoleAssignment(CardRole.FINISHER, ability.path, requirement.event,
                                    supported_by=supporting, requirements=tuple(sorted(set((*ability.requirements,
                                        'repeat_event_until_lethal', 'opponent_must_receive_damage')))),
                                    evidence='supported_repeatable_damage_payoff_potential'))
                    else:
                        missing.append((ability.path, requirement))
                supported = tuple(i for i, link in outgoing if link.source_ability == ability.path)
                if supported:
                    roles.append(RoleAssignment(CardRole.ENABLER, ability.path, 'event_production', supports=supported,
                        requirements=ability.requirements, evidence='matching_deck_consumers'))
            entries.append(DeckCardProfile(key, count, card, tuple(roles), tuple(missing)))
            for reason, amount in card.unknowns:
                unknowns[reason] += amount * count
        plans = tuple(DeckPlan(event, tuple(i for i, link in enumerate(links) if link.signal.event == event),
            tuple(sorted({link.source for link in links if link.signal.event == event})),
            tuple(sorted({link.target for link in links if link.signal.event == event}))) for event in sorted({l.signal.event for l in links}))
        payload = json.dumps((PROFILE_VERSION, [(key, count, card.semantic.to_dict()) for key, count, card in records]),
                             sort_keys=True, separators=(',', ':'), allow_nan=False)
        return DeckProfile(tuple(entries), links, plans, tuple(sorted(unknowns.items())), sha256(payload.encode()).hexdigest())
