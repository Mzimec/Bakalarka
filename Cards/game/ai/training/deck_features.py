"""Schema 3: shared deck facts and visible tactical edges for Deep Sets.

Deck identities resolve entries only. Learned inputs contain counts, semantic
facts, role evidence and actor-relative groups, never card names or hashes.
Replay retains these detached numbers and edges, not runtime worlds.
"""
from dataclasses import dataclass
from typing import ClassVar

from game.ai.evaluation.deck_profile import CardRole
from game.ai.evaluation.tactical_context import TacticalContextBuilder
from game.ai.training.entity_features import (EntityFeatureEncoder, EntityState, ENTITY_SIZE, GLOBAL_SIZE,
    RELATION_SIZE, ZONES, normalized, hot, semantic_rows)

ROLES = tuple(CardRole)
MECHANICS = ('life_gained', 'card_drawn', 'card_moved', 'counter_placed', 'card_tapped', 'damage_dealt', 'spell_cast')
STATUSES = ('conditional_present', 'preparable', 'unavailable')
EXTRA_ENTITY = 2 + len(ROLES) + 3  # live/deck flags, role counts, copies/unknown/missing
EXTRA_RELATION = len(MECHANICS) + len(STATUSES) + 2  # requirements, static-deck flag
EXTRA_GLOBAL = 2 * (len(ROLES) + 3)  # per-seat roles, size, unknown occurrences, profile present


@dataclass(frozen=True)
class DeckEntityState(EntityState):
    schema_version: ClassVar[int] = 3
    global_width: ClassVar[int] = GLOBAL_SIZE + EXTRA_GLOBAL
    entity_width: ClassVar[int] = ENTITY_SIZE + EXTRA_ENTITY
    relation_width: ClassVar[int] = RELATION_SIZE + EXTRA_RELATION
    feature_version: int = 3


def role_counts(roles):
    return tuple(sum(r.role == role for r in roles) for role in ROLES)


class DeckFeatureEncoder(EntityFeatureEncoder):
    """Profiles are explicitly supplied per game, in absolute seat order."""
    def __init__(self):
        super().__init__()
        self.profiles = ()
        self.profile_rows = ()
        self.profile_summaries = ()
        self.profile_links = ()
        self.tactics = TacticalContextBuilder()

    def bind_profiles(self, profiles):
        from game.ai.evaluation.deck_profile import DeckProfile
        if len(profiles) != 2 or any(not isinstance(p, DeckProfile) for p in profiles):
            raise ValueError('Deck model requires two explicitly permitted seat-indexed DeckProfiles')
        self.profiles = tuple(profiles)
        # Pure static numeric facts may survive optimizer updates. No learned
        # embeddings are cached: they are recomputed in every forward graph.
        self.profile_rows = tuple(tuple((semantic_rows(e.card.semantic.to_dict()),
            normalized((0., 1., *role_counts(e.roles), e.copies, sum(n for _, n in e.card.unknowns),
                        len(e.missing_support)))) for e in p.entries) for p in self.profiles)
        self.profile_summaries = tuple(normalized((*(sum(e.copies*role_counts(e.roles)[i] for e in p.entries)
            for i in range(len(ROLES))),sum(e.copies for e in p.entries),sum(n for _,n in p.unknowns),1.)) for p in self.profiles)
        self.profile_links = tuple(tuple((link.source,link.target,(*(0.,)*RELATION_SIZE,
            *normalized((*hot(link.signal.event,MECHANICS),*(0.,)*len(STATUSES),len(link.requirements),1.))))
            for link in p.links) for p in self.profiles)

    @property
    def context_key(self):
        return tuple(p.fingerprint for p in self.profiles)

    def tactical_context(self, information):
        if not self.profiles:
            raise ValueError('Bind permitted deck profiles before using a deck-aware model')
        return self.tactics.build(information.current.observation(), self.profiles, information.actions)

    def encode(self, information, *, tactical_context=None):
        context = self.tactical_context(information) if tactical_context is None else tactical_context
        base, actions = super().encode(information)
        view, own = information.current.observation(), information.current.player
        visible = {(c.reference.identity, c.reference.zone_revision): c for c in context.cards}
        entities, semantics, groups = [], list(base.semantics), list(base.groups)
        refs = {}
        for zone in ZONES:
            for card in view.get(zone, ()):
                index = len(entities)
                key = (str(card['id']), card.get('zone_revision', 0))
                refs[key] = index
                item = visible.get(key)
                roles = item.roles if item else ()
                missing = sum(m.card == item.reference for m in context.missing) if item else 0
                unknown = sum(n for _, n in item.profile.unknowns) if item else 0
                extras = normalized((1., 0., *role_counts(roles), 0., unknown, missing))
                entities.append((*base.entities[index], *extras))
        for index in range(len(entities), len(base.entities)):
            entities.append((*base.entities[index], *(0.,)*EXTRA_ENTITY))
        relations = [(s, t, (*row, *(0.,)*EXTRA_RELATION)) for s, t, row in base.relations]
        summary = []
        for seat in (own, 1-own):
            profile = self.profiles[seat]
            indexes = []
            for entry, (semantic, extras) in zip(profile.entries, self.profile_rows[seat]):
                indexes.append(len(entities))
                entities.append((*(0.,)*ENTITY_SIZE, *extras))
                # Transform every card's semantics before pooling. Copies stay
                # explicit, rather than making N identical runtime entities.
                semantics.append(semantic)
                groups.append(10 if seat == own else 11)
            for source,target,row in self.profile_links[seat]:
                relations.append((indexes[source],indexes[target],row))
            summary.extend(self.profile_summaries[seat])
        for link in context.connections:
            source = refs[(link.source.identity, link.source.zone_revision)]
            target = refs[(link.target.identity, link.target.zone_revision)]
            row = (*(0.,)*RELATION_SIZE, *normalized((*hot(link.mechanic, MECHANICS), *hot(link.status, STATUSES),
                                                    len(link.requirements), 0.)))
            relations.append((source, target, row))
        state = DeckEntityState((*base.globals, *summary), tuple(entities), tuple(semantics), tuple(groups), tuple(relations))
        state.validate()
        for action in actions:
            action.validate(state)
        return state, actions
