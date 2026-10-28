"""Exact, consumer-scoped sufficient information for deterministic policy reuse.

These records are not replacements for PlayerInformation or sampled worlds.
History/replay keys keep their full evidence. A later lossy abstraction must use
a different contract and must not interpret these pure ratings as outcome visits.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING

from game.ai.structured_values import Value, ValueStore

if TYPE_CHECKING:
    from game.ai.search_recall import PlayerInformation, RecallEntry
    from game.ai.search_actions import PlayerAction


@dataclass(frozen=True, slots=True)
class CounterEvidence:
    """!
    @brief Latest public rule evidence for one visible physical identity and name.
    @var card_id Viewer-observed identity, never an unobserved sampled card.
    @var name Observed name used to match current still-known hand locations.
    @var counter Whether the latest matching description contained counter features.
    """
    card_id: str
    name: str
    counter: bool


@dataclass(frozen=True)
class KnowledgeSummary:
    """!
    @brief Exact index of the history facts read by the built-in counter-risk prior.

    This is a sufficient statistic for that reader only, not a belief posterior
    or a general permission to discard history. Locations are checked afresh.
    @var evidence Latest matching public descriptions, including negative evidence.
    """
    evidence: tuple[CounterEvidence, ...] = ()

    def __reduce__(self):
        # Derived mapping proxies are local indexes, not process payloads.
        return KnowledgeSummary, (self.evidence,)

    @cached_property
    def index(self):
        """!
        @brief Expose an immutable lookup without retaining historical card graphs.
        """
        from types import MappingProxyType
        return MappingProxyType({(row.card_id, row.name): row.counter for row in self.evidence})

    def append(self, entry: RecallEntry) -> KnowledgeSummary:
        """!
        @brief Incorporate one observation, preserving the old reverse-reader semantics.
        """
        if entry.kind != 'observation':
            return self
        view = entry.readonly().get('view', {})
        changed = {}
        # The old reader visits zones/rows in this order even inside one
        # observation. First matching row wins; a negative result masks older
        # positive evidence. Keep distinct names for the same physical card.
        for zone in ('stack', 'battlefield', 'graveyard', 'exile'):
            for card in view.get(zone, ()):
                if not card.get('identity_visible', True):
                    continue
                key = card.get('id'), card.get('name')
                if key not in changed:
                    changed[key] = self._counter(card.get('abilities', ()))
        if not changed or all(self.index.get(key) == value for key, value in changed.items()):
            return self
        rows = dict(self.index)
        rows.update(changed)
        return KnowledgeSummary(tuple(CounterEvidence(identity, name, value)
                                      for (identity, name), value in rows.items()))

    @staticmethod
    def _counter(value) -> bool:
        if isinstance(value, Mapping):
            features = value.get('features')
            if isinstance(features, Mapping) and features.get('kind') == 'counter':
                return True
            return any(KnowledgeSummary._counter(child) for key, child in value.items() if key != 'features')
        if isinstance(value, (list, tuple)):
            return any(KnowledgeSummary._counter(child) for child in value)
        return False

    def counter_sources(self, view) -> tuple[str, ...]:
        """!
        @brief Query only identities whose opponent-hand locations remain known.
        """
        wanted = {row['card_id']: row['name'] for row in (view.get('knowledge') or {}).get('known_locations', ())
                  if row['zone'] == 'HAND' and row['owner'] != view['self']}
        return tuple(sorted(identity for identity, name in wanted.items() if self.index.get((identity, name), False)))


@dataclass(frozen=True, slots=True)
class CanonicalAction:
    """!
    @brief Complete action facts with consistently relabeled card references.
    @var kind Original offered action kind.
    @var value All original action fields, with no parameter or target reduction.
    """
    kind: str
    value: Value


class CanonicalPlayerView:
    """!
    @brief Reversible normalization of the complete current player observation.

    All card fields, sequence order, player IDs, incarnations, targets and rule
    descriptions are preserved. Only physical card labels are renamed bijectively.
    Recall and the hidden world are separate; this is not a tree transposition key.
    @var value Complete immutable observation, including unknown extension fields.
    @var counter_present Additional explicitly supplied policy evidence, if any.
    @var bindings Local original-to-canonical card labels; never cached as game state.
    @var external Reverse labels for references absent from the visible card rows.
    """
    version = 'exact-player-view-v1'
    fields = None
    zones = ('hand', 'battlefield', 'stack', 'graveyard', 'exile')

    @classmethod
    def source_key(cls, information: PlayerInformation, counter_sources=()):
        """!
        @brief Identify the exact sufficient input using already frozen subtrees.
        """
        fields = dict(information.current.structured.value.items)
        selected = cls.fields if cls.fields is not None else tuple(fields)
        return (information.current.player, information.current.request_kind, bool(counter_sources),
                tuple((key, fields[key]) for key in selected if key in fields))

    def __init__(self, information: PlayerInformation, counter_sources=(), *, canonical_ids=True):
        view = information.current.readonly()
        self.canonical_ids = canonical_ids
        self.bindings = {}
        self.external = {}
        self.counter_present = bool(counter_sources)
        self.player, self.request_kind = information.current.player, information.current.request_kind
        selected = self.fields if self.fields is not None else tuple(view)
        if not canonical_ids:
            # Reuse the immutable card/rule subtrees already built by projection.
            # This path avoids a full traversal and preserves physical labels.
            self.value = ValueStore(0).freeze({key: view[key] for key in selected if key in view})
            return
        # Labels follow visible row order, never sorted physical IDs. Preserve
        # that order: canonicalization is deliberately not graph isomorphism.
        for zone in self.zones:
            for card in view.get(zone, ()):
                self._declare(card['id'])
        self._collect_rows(view.get('decision_context', {}))
        self.value = ValueStore(0).freeze({key: self._rewrite(view[key], context=key)
                                         for key in selected if key in view})

    def _declare(self, identity):
        if identity not in self.bindings:
            self.bindings[identity] = '\x00card:' + str(len(self.bindings))

    def _collect_rows(self, value):
        if isinstance(value, Mapping):
            if 'id' in value and 'zone_revision' in value:
                self._declare(value['id'])
            for child in value.values():
                self._collect_rows(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                self._collect_rows(child)

    def _reference(self, identity):
        # Undeclared historical references must remain nonmatching; never let an
        # unbound old ID collide with one of the synthetic visible labels.
        if identity in self.bindings:
            return self.bindings[identity]
        label = '\x00external:' + repr(identity)
        self.external[label] = identity
        return label

    def _rewrite(self, value, *, context=(), card_row=False, reference=None):
        rename = self._reference if reference is None else reference
        if isinstance(context, str):
            context = (context,)
        if isinstance(value, Mapping):
            if context in (('combat', 'defenders'), ('combat', 'blockers')):
                return {rename(key): (rename(child) if context[-1] == 'blockers'
                                      else self._rewrite(child, reference=rename)) for key, child in value.items()}
            row = card_row or ('id' in value and 'zone_revision' in value)
            result, unchanged = {}, True
            for key, child in value.items():
                rewritten = (rename(child) if (key == 'id' and row) or
                             (key in ('card', 'card_id') and type(child) in (str, int)) else
                             self._rewrite(child, context=(*context, key), reference=rename))
                result[key] = rewritten
                unchanged &= rewritten is child
            # Frozen rule descriptions without card references are shared whole.
            # Do not rebuild their complete trees just to relabel one source.
            return value if unchanged else result
        if isinstance(value, (list, tuple)):
            if context in (('combat', 'attackers'), ('combat', 'blocked')):
                return [rename(child) for child in value]
            result = [self._rewrite(child, context=context,
                                   card_row=len(context) == 1 and context[0] in self.zones,
                                   reference=rename) for child in value]
            return value if all(a is b for a, b in zip(result, value)) else result
        return value

    @property
    def key(self):
        """!
        @brief Compare full typed facts as well as their hash; digest collisions cannot merge inputs.
        """
        return self.version, self.canonical_ids, self.player, self.request_kind, self.value, self.counter_present

    def action(self, action: PlayerAction) -> CanonicalAction:
        """!
        @brief Relabel a complete current action without modifying its native binding.
        """
        return CanonicalAction(action.kind, ValueStore(0).freeze(self._rewrite(action.readonly()))
                               if self.canonical_ids else action.structured.value)

    def restore(self):
        """!
        @brief Export detached original facts, including original labels and unknown fields.
        """
        if not self.canonical_ids:
            return self.value.thaw()
        inverse = {label: identity for identity, label in self.bindings.items()}
        inverse.update(self.external)
        return ValueStore(0).freeze(self._rewrite(self.value.readonly, reference=inverse.__getitem__)).thaw()


class CanonicalPolicyView(CanonicalPlayerView):
    """!
    @brief Exact sufficient input specifically for the built-in per-action scorer.

    Fields outside this explicit consumer contract remain in CanonicalPlayerView
    and PlayerInformation. The only historical branch reads counter presence.
    @var value Immutable scorer input with complete retained card/rule rows.
    """
    version = 'exact-policy-v1'
    fields = ('self', 'players', 'hand', 'battlefield', 'stack', 'graveyard', 'exile',
              'pending_abilities', 'combat', 'decision_context')
