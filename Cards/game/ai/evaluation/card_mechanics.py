"""Detached capabilities derived from semantic ability/effect trees.

This module describes potential interactions, never executes rules or proves
legality. Costs, conditional branches and unresolved targets remain explicit.
Authored names/keys have no role in classification.
"""
from dataclasses import dataclass
from functools import lru_cache

from game.ai.evaluation.semantic_encoding import SemanticNode, node


def walk(tree):
    yield tree
    for child in tree.children:
        yield from walk(child)


def from_data(data):
    """Validate a projected semantic tree; accept no executable objects."""
    def read(value, depth):
        if not isinstance(value, dict) or depth > 64 or value.get('schema_version') != 1:
            return node('unknown', reason='missing_projected_semantics')
        return node(value['kind'], (read(c, depth+1) for c in value.get('children', ())),
                    **dict(value.get('attributes', ())))
    return read(data, 0)


@dataclass(frozen=True, slots=True)
class MechanicSignal:
    """Event plus recipient restrictions, rather than an unqualified keyword."""
    event: str
    subject: str = 'controller'
    origin: str | None = None
    destination: str | None = None
    card_type: str | None = None
    counter_type: str | None = None


@dataclass(frozen=True, slots=True)
class AbilityProfile:
    path: tuple
    semantic: SemanticNode
    produces: tuple = ()
    reacts_to: tuple = ()
    consumes: tuple = ()
    traits: tuple = ()
    requirements: tuple = ()
    active_zones: tuple = ()
    repeatable: bool = False
    unknowns: tuple = ()


@dataclass(frozen=True, slots=True)
class CardProfile:
    semantic: SemanticNode
    abilities: tuple
    types: tuple
    keywords: tuple
    power: int | None
    toughness: int | None
    unknowns: tuple


def _effects(tree):
    # A token definition's abilities are not effects of the creating spell.
    yield tree
    if tree.kind != 'create_token':
        for child in tree.children:
            yield from _effects(child)


def _signal(effect):
    a = dict(effect.attributes)
    kind = effect.kind
    if a.get('amount', 1) <= 0:
        return ()
    if kind == 'gain_life':
        return (MechanicSignal('life_gained', a.get('recipient', 'controller')),)
    if kind == 'draw':
        return (MechanicSignal('card_drawn', 'controller'),)
    if kind == 'create_token':
        types = tuple(dict(c.attributes)['value'] for c in effect.children[0].children if c.kind == 'types')
        return tuple(MechanicSignal('card_moved', 'created', destination='BATTLEFIELD', card_type=t) for t in types)
    if kind == 'put_counter':
        counter = next((dict(c.attributes).get('value') for c in effect.children if c.kind == 'counter_type'), None)
        return (MechanicSignal('counter_placed', a.get('recipient', 'unspecified'), counter_type=counter),)
    if kind == 'sacrifice':
        return (MechanicSignal('card_moved', 'target', origin='BATTLEFIELD', destination='GRAVEYARD'),)
    if kind == 'move_zone':
        return (MechanicSignal('card_moved', a.get('recipient', 'unspecified'), destination=a['destination']),)
    if kind == 'tap':
        return (MechanicSignal('card_tapped', a.get('recipient', 'source')),)
    if kind == 'damage':
        return (MechanicSignal('damage_dealt', a.get('recipient', 'target')),)
    return ()


def _ability(tree, path):
    attrs = dict(tree.attributes)
    produces, reacts, consumes, traits, requirements = set(), set(), set(), set(), set()
    active = tuple(sorted(dict(c.attributes)['value'] for c in tree.children if c.kind == 'allowed_zone'))
    repeatable = not attrs.get('spell', False) and 'BATTLEFIELD' in active
    for part in tree.children:
        if part.kind == 'trigger':
            a = dict(part.attributes)
            subject = ('source' if a.get('source_only') else 'another_controlled' if a.get('another') and a.get('controlled_only')
                       else 'another' if a.get('another') else 'controlled' if a.get('controlled_only') else 'any')
            reacts.add(MechanicSignal(a['event'], subject, a.get('origin'), a.get('destination'), a.get('card_type'), a.get('counter_type')))
            if a.get('power_at_most') is not None:
                requirements.add('trigger_power_at_most:' + str(a['power_at_most']))
        if part.kind not in ('cost', 'resolution', 'variable'):
            continue
        for effect in _effects(part):
            a = dict(effect.attributes)
            is_cost = part.kind == 'cost'
            if part.kind == 'variable':
                requirements.add('variable_choice')
                # Variable cost/action children are not interchangeable.
                continue
            if effect.kind == 'unknown':
                requirements.add('unresolved_rule')
            if effect.kind == 'one_of':
                requirements.add('alternative_branch')
            if effect.kind in ('target', 'card_target', 'player_target', 'self_target', 'any_damage_target'):
                requirements.add('legal_target')
            if is_cost:
                if effect.kind in ('mana', 'tap', 'sacrifice'):
                    consumes.add(effect.kind)
                if effect.kind == 'mana' and effect.children:
                    requirements.add('pay_mana')
                # Paying a cost also creates events, but retain its phase.
                if _signal(effect):
                    requirements.add('cost_event')
                produces.update(_signal(effect))
                continue
            produces.update(_signal(effect))
            if effect.kind in ('gain_life', 'draw', 'add_mana', 'create_token', 'sacrifice', 'attach', 'selection'):
                traits.add(effect.kind)
            if effect.kind == 'draw' and a.get('amount', 1) >= 2:
                traits.add('draw_multiple')
            if effect.kind in ('destroy', 'counter'):
                traits.add('interaction')
            if effect.kind == 'damage':
                traits.add('damage')
                if a.get('recipient') == 'player' or (a.get('recipient') == 'target' and any(
                        c.kind == 'any_damage_target' and dict(c.attributes).get('includes_players')
                        or c.kind == 'player_target' for c in walk(part))):
                    traits.add('player_damage')
            if effect.kind == 'modify_stat':
                keys = {dict(c.attributes).get('value') for c in walk(effect) if c.kind == 'keywords'}
                if keys & {'hexproof', 'indestructible'}:
                    traits.add('protection')
                if a.get('power', 0) > 0 or any(c.kind == 'modifier' and dict(c.attributes).get('field') == 'power'
                        and dict(c.attributes).get('amount', 0) > 0 for c in effect.children):
                    traits.add('pump')
            if effect.kind == 'move_zone':
                zones = {dict(c.attributes).get('value') for c in walk(effect) if c.kind == 'query_value'}
                if 'GRAVEYARD' in zones and a['destination'] in ('HAND', 'BATTLEFIELD'):
                    traits.add('recovery')
                elif a['destination'] in ('HAND', 'EXILE') and a.get('recipient') != 'source':
                    traits.add('interaction')
    if attrs.get('spell'):
        requirements.add('cast_spell')
    if attrs.get('triggered'):
        requirements.add('trigger_event')
    if attrs.get('sorcery_speed'):
        requirements.add('sorcery_speed')
    if any(s.subject == 'source' and s.destination == 'BATTLEFIELD' for s in reacts):
        repeatable = False  # Self ETB requires another zone transition, not ordinary reuse.
    return AbilityProfile(path, tree, tuple(sorted(produces, key=repr)), tuple(sorted(reacts, key=repr)),
                          tuple(sorted(consumes)), tuple(sorted(traits)), tuple(sorted(requirements)), active,
                          repeatable, tuple(tree.unknowns.items()))


@lru_cache(maxsize=256)
def card_profile(tree):
    """Capabilities only: strategic roles are assigned by DeckProfiler."""
    attrs = dict(tree.attributes)
    abilities = []
    for group in tree.children:
        if group.kind in ('abilities', 'triggers'):
            for index, ability in enumerate(group.children):
                abilities.append(_ability(ability, (group.kind, index)))
    types = tuple(sorted(dict(c.attributes)['value'] for c in tree.children if c.kind == 'types'))
    keywords = tuple(sorted(dict(c.attributes)['value'] for c in tree.children if c.kind == 'keywords'))
    # Keyword events are conditional; lifelink does not promise successful damage.
    if 'lifelink' in keywords:
        abilities.append(AbilityProfile(('keyword', 'lifelink'), node('keywords', value='lifelink'),
            produces=(MechanicSignal('life_gained'),), requirements=('deal_positive_damage',),
            active_zones=('BATTLEFIELD',), repeatable=True))
    # Entering/casting a card is itself an event, independently of its effects.
    if set(types) & {'CREATURE', 'ARTIFACT', 'ENCHANTMENT', 'LAND', 'PLANESWALKER'}:
        abilities.append(AbilityProfile(('native', 'enter'), node('empty'),
            produces=tuple(MechanicSignal('card_moved', 'source', destination='BATTLEFIELD', card_type=t) for t in types),
            requirements=('enter_battlefield',), active_zones=('HAND',)))
    if any(dict(a.semantic.attributes).get('spell') for a in abilities):
        abilities.append(AbilityProfile(('native', 'cast'), node('empty'),
            produces=tuple(MechanicSignal('spell_cast', 'source', card_type=t) for t in types),
            requirements=('cast_spell',), active_zones=('HAND',)))
    return CardProfile(tree, tuple(abilities), types, keywords, attrs.get('power'), attrs.get('toughness'), tuple(tree.unknowns.items()))


def compatible(producer, consumer, *, same_card, copies=1):
    """Conservative structural match, with unresolved targeting kept conditional."""
    if producer.event != consumer.event:
        return False
    for field in ('origin', 'destination', 'counter_type'):
        wanted = getattr(consumer, field)
        if wanted is not None and getattr(producer, field) != wanted:
            return False
    if consumer.card_type is not None and producer.card_type != consumer.card_type:
        return False
    if consumer.subject == 'source' and (not same_card or producer.subject != 'source'):
        return False
    if consumer.subject in ('another', 'another_controlled') and same_card and copies < 2 and producer.subject == 'source':
        return False
    return True
