"""Actor-visible, detached tactical connections over immutable deck profiles.

Connections are conditional opportunities, not predicted resolutions or legal
actions. Only the rules engine validates timing, costs, targets and outcomes.
No hidden library/hand or simulation State is accepted by this interface.
"""
from collections import defaultdict
from dataclasses import dataclass, replace

from game.ai.evaluation.card_mechanics import card_profile, compatible, from_data
from game.ai.evaluation.deck_profile import basic_roles, CardRole, RoleAssignment
from game.ai.evaluation.semantic_encoding import node

VISIBLE_ZONES = ('hand', 'battlefield', 'stack', 'graveyard', 'exile')


@dataclass(frozen=True, slots=True)
class CardReference:
    identity: str
    zone_revision: int


@dataclass(frozen=True, slots=True)
class PlayerReference:
    player: int


@dataclass(frozen=True, slots=True)
class TacticalCard:
    reference: CardReference
    owner: int
    controller: int
    zone: str
    profile: object
    roles: tuple
    deck_entry: int | None
    tapped: bool
    summoning_sick: bool


@dataclass(frozen=True, slots=True)
class TacticalConnection:
    source: CardReference
    target: CardReference
    source_ability: tuple
    target_ability: tuple
    mechanic: str
    status: str
    requirements: tuple
    # 'conditional_present' means both sources are in their active zones,
    # not that an event is happening or that an ability can legally be used.


@dataclass(frozen=True, slots=True)
class MissingRequirement:
    card: CardReference
    ability: tuple
    signal: object
    status: str


@dataclass(frozen=True, slots=True)
class ActionConnection:
    action_index: int
    source: CardReference | None
    targets: tuple
    effects: tuple
    outgoing_connections: tuple
    incoming_connections: tuple


@dataclass(frozen=True, slots=True)
class ActionEffect:
    phase: str
    semantic: object
    targets: tuple


@dataclass(frozen=True, slots=True)
class TacticalContext:
    player: int
    profile_fingerprints: tuple
    cards: tuple
    connections: tuple
    missing: tuple
    actions: tuple
    schema_version: int = 1

    def roles(self, reference):
        return next((c.roles for c in self.cards if c.reference == reference), ())

    def connections_for(self, reference):
        return tuple(c for c in self.connections if reference in (c.source, c.target))

    def missing_requirements(self, reference):
        return tuple(c for c in self.missing if c.card == reference)

    def action_connections(self, index):
        return self.actions[index]


def visible_profile(card):
    """Use current projected abilities, including gained/lost abilities.

    Never fill missing visible semantics from a catalog name: a transformed,
    silenced or face-down card need not have its printed abilities.
    """
    groups = [node(field, (from_data(a.get('semantic')) for a in card.get(field, ()) if isinstance(a, dict)))
              for field in ('abilities', 'triggers')]
    groups.extend(node('types', value=t) for t in card.get('types', ()))
    groups.extend(node('keywords', value=k) for k in card.get('keywords', ()))
    return card_profile(node('card', groups, power=card.get('power'), toughness=card.get('toughness')))


def _references(data, known):
    if isinstance(data, dict):
        if type(data.get('player')) is int and data['player'] in (0, 1):
            yield PlayerReference(data['player'])
        elif 'card' in data:
            reference = known.get((str(data['card']), data.get('zone_revision', 0)))
            if reference is not None:
                yield reference
        else:
            for value in data.values():
                yield from _references(value, known)
    elif isinstance(data, (list, tuple)):
        for value in data:
            yield from _references(value, known)


def _bound_targets(part, binding, known):
    """Resolve the native slot/group references of this effect, not its neighbors."""
    slots = dict((part.get('targets') or {}).get('mapping', ()))
    for slot, group in binding.get('slots', ()):
        groups = dict(slots.get(slot, {}).get('mapping', ()))
        yield from _references(groups.get(group), known)


class TacticalContextBuilder:
    """Build a position-local graph; profiles are shared, contexts are detached."""
    def __init__(self, *, matcher=None, profile_builder=None):
        self.matcher = matcher if matcher is not None else compatible
        self.profile_builder = profile_builder if profile_builder is not None else visible_profile

    def build(self, view, profiles=(), actions=()):
        if not isinstance(view, dict):
            raise TypeError('Tactical context requires a detached actor observation')
        if profiles and len(profiles) != 2:
            raise ValueError('Supply two seat-indexed profiles or no declared profiles')
        own = view['self']
        if own not in (0, 1):
            raise ValueError('Invalid actor seat')
        cards, producers = [], defaultdict(list)
        for zone in VISIBLE_ZONES:
            for card in view.get(zone, ()):
                if not card.get('identity_visible', True) or card.get('face_down', False):
                    continue
                profile = self.profile_builder(card)
                owner, controller = card['owner'], card['controller']
                deck = profiles[controller] if profiles else None
                entry = next((i for i, e in enumerate(deck.entries) if e.catalog_key == card.get('name')), None) if deck else None
                # Deck roles only apply to still-present ability semantics.
                roles = list(basic_roles(profile))
                if entry is not None:
                    original = deck.entries[entry]
                    matching = {a.path: b.path for a in original.card.abilities for b in profile.abilities
                                if a.semantic == b.semantic and (a.path == b.path or
                                a.path[0] in ('abilities', 'triggers') and a.path[0] == b.path[0])}
                    roles.extend(replace(r, ability=matching[r.ability]) for r in original.roles if r.ability in matching
                                 and replace(r, ability=matching[r.ability]) not in roles)
                item = TacticalCard(CardReference(str(card['id']), card.get('zone_revision', 0)), owner, controller,
                    zone.upper(), profile, tuple(roles), entry, bool(card.get('tapped')), bool(card.get('summoning_sick')))
                cards.append(item)
                for ability in profile.abilities:
                    for signal in ability.produces:
                        producers[signal.event].append((item, ability, signal))
        links, missing = [], []
        for target in cards:
            for ability in target.profile.abilities:
                for requirement in ability.reacts_to:
                    found = False
                    for source, source_ability, signal in producers[requirement.event]:
                        if (requirement.subject in ('controlled', 'another_controlled') and source.controller != target.controller
                                and signal.subject in ('source', 'created', 'controller')):
                            continue
                        if not self.matcher(signal, requirement, same_card=source.reference == target.reference):
                            continue
                        source_active = source.zone in source_ability.active_zones
                        target_active = target.zone in ability.active_zones
                        preparable = ((source_active or source.zone == 'HAND') and (target_active or target.zone == 'HAND'))
                        status = 'conditional_present' if source_active and target_active else 'preparable' if preparable else 'unavailable'
                        requirements = set((*source_ability.requirements, *ability.requirements, 'event_conditions_must_hold'))
                        if source.tapped:
                            requirements.add('source_tapped')
                        if source.summoning_sick:
                            requirements.add('source_summoning_sick')
                        if signal.subject in ('target', 'unspecified'):
                            requirements.add('recipient_must_match')
                        if source.controller != own or target.controller != own:
                            requirements.add('opponent_choices_unknown')
                        links.append(TacticalConnection(source.reference, target.reference, source_ability.path,
                            ability.path, signal.event, status, tuple(sorted(requirements))))
                        found |= status != 'unavailable'
                    if not found:
                        missing.append(MissingRequirement(target.reference, ability.path, requirement, 'no_visible_support'))
        links = list(dict.fromkeys(links))
        # Emergent support also matters for tokens, stolen cards and abilities
        # absent from the original roster. Keep deck and tactical link indices
        # in explicitly different namespaces.
        for index, card in enumerate(cards):
            roles = list(card.roles)
            for ability in card.profile.abilities:
                incoming = tuple(i for i, link in enumerate(links) if link.target == card.reference
                                 and link.target_ability == ability.path and link.status != 'unavailable')
                outgoing = tuple(i for i, link in enumerate(links) if link.source == card.reference
                                 and link.source_ability == ability.path and link.status != 'unavailable')
                for role, ids in ((CardRole.PAYOFF, incoming), (CardRole.ENABLER, outgoing)):
                    if ids and not any(r.role == role and r.ability == ability.path for r in roles):
                        roles.append(RoleAssignment(role, ability.path, 'visible_event_support',
                            supported_by=ids if role == CardRole.PAYOFF else (), supports=ids if role == CardRole.ENABLER else (),
                            requirements=ability.requirements, evidence='conditional_visible_support', link_space='tactical'))
            cards[index] = replace(card, roles=tuple(roles))
        known = {(c.reference.identity, c.reference.zone_revision): c.reference for c in cards}
        action_links = []
        for index, action in enumerate(actions):
            data = action.describe() if hasattr(action, 'describe') else action
            sources = tuple(_references(data.get('source', data.get('card')), known))
            source = sources[0] if sources else None
            targets = tuple(dict.fromkeys(_references(((data.get('effect') or {}).get('targets'),
                (data.get('cost') or {}).get('targets'), data.get('selection')), known)))
            effects = []
            for phase in ('cost', 'effect'):
                for effect in (data.get(phase) or {}).get('effects', ()):
                    semantic = from_data(effect.get('semantic'))
                    bound = tuple(dict.fromkeys(_bound_targets(data[phase], effect, known)))
                    attrs = dict(semantic.attributes)
                    if attrs.get('recipient') == 'source' and source is not None:
                        bound = (source,)
                    elif attrs.get('recipient') == 'controller':
                        controller = (data.get('controller') or {}).get('player', own)
                        bound = (PlayerReference(controller),)
                    effects.append(ActionEffect(phase, semantic, bound))
            action_links.append(ActionConnection(index, source, targets, tuple(effects),
                tuple(i for i, link in enumerate(links) if link.source == source),
                tuple(i for i, link in enumerate(links) if link.target == source)))
        return TacticalContext(own, tuple(p.fingerprint for p in profiles), tuple(cards), tuple(links), tuple(missing), tuple(action_links))
