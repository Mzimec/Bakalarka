"""Reusable dynamic positions over stable handles in one OWNED sampled world.

Supports filtered native priority and combat roots; continuation validation
belongs to the current-position snapshot, before worker construction.

Not a live-game sampler or an undo journal. Each restoration copies dynamic
components through one ownership memo, then switches handle dictionaries. Card,
Player, State and repository identities survive; arbitrary component references
must not escape the synchronous simulation loan. Static definitions stay shared.
"""
from time import perf_counter

from game.ai.simulation.runtime_clone import UnsupportedSnapshot


class PriorityPosition:
    """One immutable-by-ownership root recipe, reusable after native unwind."""

    def __init__(self, state, memories, *, immutable_values=(), share_root_values=True,
                 planned_restore=False, record_plans=False, capture_recipe=True):
        if type(capture_recipe) is not bool:
            raise ValueError('capture_recipe must be boolean')
        if type(share_root_values) is not bool:
            raise ValueError('share_root_values must be boolean')
        if type(planned_restore) is not bool:
            raise ValueError('planned_restore must be boolean')
        if type(record_plans) is not bool:
            raise ValueError('record_plans must be boolean')
        self.planned_restore = planned_restore
        self.record_plans = record_plans
        from game.game_state.state import State
        from game.game_state.card import Card
        from game.game_state.player import Player
        from game.game_state.card_repository import CardRepository
        from game.game_state.card_snapshot_cache import CardSnapshotCache
        from game.ai.information.knowledge import PlayerKnowledge
        from game.enums import TurnPhase

        if (type(state) is not State or type(state.cards) is not CardRepository
                or type(state._card_snapshots) is not CardSnapshotCache
                or state.turn.phase in (TurnPhase.UNTAP, TurnPhase.CLEANUP)
                or state._refreshing_effects or state._deferred_events
                or getattr(state, '_layer_ceiling', None) is not None):
            raise UnsupportedSnapshot('Unsettled native position')
        player_memory = getattr(state, 'record_player_information', True)
        if ((player_memory and len(memories) != len(state.players)) or
                (not player_memory and memories) or any(type(m) is not PlayerKnowledge for m in memories)):
            raise UnsupportedSnapshot('Native position requires player memories')
        cards = tuple(state.cards.values())
        if any(type(c) is not Card or c._game_state is not state for c in cards):
            raise UnsupportedSnapshot('Native position requires owned card handles')
        if any(type(p) is not Player or p.game_state is not state for p in state.players):
            raise UnsupportedSnapshot('Native position requires owned player handles')
        self.state, self.memories = state, tuple(memories)
        self.card_snapshots = state._card_snapshots
        self.handles = (state, state.cards, *state.players, *cards, *memories)
        if len({id(h) for h in self.handles}) != len(self.handles):
            raise UnsupportedSnapshot('Position handles must have distinct ownership')
        self._handle_bindings = tuple((handle, handle) for handle in self.handles)
        self.readers = getattr(state, '_observation_readers', None) if player_memory else None
        if player_memory and self.readers is None:
            from game.ai.information.runtime_observation import ObservationReaders
            self.readers = state._observation_readers = ObservationReaders()
        if self.readers is not None and any(v._builder is not None for v in self.readers.views.values()):
            raise RuntimeError('Cannot capture a position during a live observation read')
        self.constants = dict(immutable_values)
        self.constants[id(state.knowledge_token)] = state.knowledge_token
        self.constants[id(state.cards._position_token)] = state.cards._position_token
        self.copies, self.restore_seconds, self.digest_restores = 0, 0.0, 0
        started = perf_counter()
        # Populate proven native plans only after layers have already settled.
        # Capturing a recipe must not execute arbitrary lazy stat callbacks or
        # advance RNG. Unknown pipelines remain on the ordinary live evaluator.
        from game.stat_type import (STAT_POWER, STAT_TOUGHNESS, STAT_TYPES,
                                    STAT_SUBTYPES, STAT_CONTROLLER, STAT_COLORS, STAT_KEYWORDS)
        from game.game_state.native_modifier_plan import build_plan, sources_empty, MISSING
        self.materialized_values = 0
        if not state._effects_dirty and state._effects_checked_at == state.time_stamp:
            for card in cards:
                if (sources_empty(card) or '_get_modifier_pipeline' in vars(card)
                        or 'get_stat' in vars(card)):
                    continue
                plan = build_plan(card, state)
                if plan is None:
                    continue
                for stat in (STAT_POWER, STAT_TOUGHNESS, STAT_TYPES, STAT_SUBTYPES,
                             STAT_CONTROLLER, STAT_COLORS, STAT_KEYWORDS):
                    self.materialized_values += plan.read(card, stat, state) is not MISSING
                card.__dict__['_native_modifier_plan'] = plan
        from game.ai.simulation.simulation_world import immutable_world_values
        self.constants.update(immutable_world_values(state, stable_handles=self.handles))
        self.share_root_values = share_root_values
        self.root_value_counts = {}
        if share_root_values:
            # These identities were already explicitly rebound to themselves.
            # Mark all of them constant for dictionary recipes as well.
            self.constants.update((id(handle), handle) for handle in self.handles)
        # A branch can temporarily replace a getter without replacing its stat
        # table. Its new cache entry must never become the restored root's proof.
        self.cache_roots = (dict(self.card_snapshots._entries),
                            dict(self.card_snapshots._index_entries))
        self.attributes = ()
        self._recipe_captured = capture_recipe
        self.memo_entries = 0
        if capture_recipe:
            self.attributes = self._copy(tuple(vars(h) for h in self.handles))
        from game.ai.simulation.runtime_clone import DictionaryCopyPlans
        self.dictionary_plans = DictionaryCopyPlans(self.constants)
        if planned_restore:
            from game.ai.simulation.runtime_clone import IdentityBindings
            self.identity_bindings = IdentityBindings(self.constants)
        self.capture_seconds = perf_counter() - started

    def capture_copy_recipe(self):
        """Finish a prepare-only position before handing it to the copy driver."""
        if not self._recipe_captured:
            started = perf_counter()
            self.attributes = self._copy(tuple(vars(h) for h in self.handles))
            self._recipe_captured = True
            self.capture_seconds += perf_counter() - started

    def _copy(self, attributes):
        from game.ai.simulation.simulation_clone import SimulationClone
        from game.mana.source_cache import StaticManaSourceCache
        from game.game_actions.triggers.trigger_registry import NativeTriggerRegistry
        state = self.state
        bindings = list(self._handle_bindings)
        bindings.extend((p.controller, None) for p in state.players if p.controller is not None)
        # Reuse only world-local services with reviewed live-input guards.
        # Other derived services stay cold, without retaining a replay driver.
        root = attributes[0]
        bindings.append((root['_card_snapshots'], self.card_snapshots))
        for name, new in (('_mana_source_cache', StaticManaSourceCache()),
                          ('_trigger_registry', NativeTriggerRegistry())):
            bindings.append((root[name], new))
        readers = root.get('_observation_readers')
        if readers is not None:
            bindings.append((readers, self.readers))
        clone = SimulationClone(getattr(self, 'identity_bindings', self.constants),
                                object_replacements=bindings, scalar_fields=True,
                                dictionary_plans=getattr(self, 'dictionary_plans', None))
        result = clone.copy(attributes)
        if self.share_root_values and not hasattr(self, 'dictionary_plans'):
            from game.ai.simulation.position_values import recipe_constants
            constants, self.root_value_counts = recipe_constants(clone, self.handles)
            self.constants.update(constants)
        if hasattr(self, 'dictionary_plans'):
            if self.record_plans and self.dictionary_plans.collecting:
                self.dictionary_plans.prepare_records(clone)
            self.dictionary_plans.collecting = False
        self.memo_entries = len(clone.memo)
        self.copies += 1
        return result

    def rollback(self):
        """Copy first, then install one coherent position without gameplay hooks."""
        if not self._recipe_captured:
            raise RuntimeError('Position has no captured copy recipe')
        readers = getattr(self.state, '_observation_readers', None)
        if readers is not None and any(v._builder is not None for v in readers.views.values()):
            raise RuntimeError('Cannot restore a position during a live observation read')
        started = perf_counter()
        attributes = self._copy(self.attributes)
        for handle, data in zip(self.handles, attributes):
            object.__setattr__(handle, '__dict__', data)
        from game.ai.simulation.simulation_world import rebind_replacement_instances
        rebind_replacement_instances(self.state)
        if self.readers is not None:
            self.readers.fields.retain_definitions()
            self.readers.knowledge_rows.clear()
        present = set(self.state.get_cards())
        cache = self.card_snapshots
        # Restore ROOT projections, never entries learned under a branch's
        # temporary reader overrides. Live-input guards still validate hits.
        for name, entries in zip(('_entries', '_index_entries'), self.cache_roots):
            setattr(cache, name, {key: entry for key, entry in entries.items()
                    if entry.card in present and cache._matches(entry, entry.card)})
        stats = {id(card.stats): card.stats for card in present}
        cache._validated_stats = {key: value for key, value in cache._validated_stats.items()
                                  if stats.get(key) is value}
        if hasattr(cache, '_observation_base_rows'):
            cache._observation_base_rows = {key: entry for key, entry in cache._observation_base_rows.items()
                    if entry[0] in present and entry[0].stats is entry[1]}
        if self.readers is None:
            self.restore_seconds += perf_counter() - started
            return
        from game.ai.information.runtime_digest import DigestNode
        from game.ai.information.runtime_observation import _session
        session = _session.get()
        reuse = session is None or session.seed_reuse
        for view in self.readers.views.values():
            view.memo.clear()
            view.extra.clear()
            view._versions.clear()
            view._identity_lease = view._lease_token = None
            view._actor_revisions = False
            # Resume the restored root's comparison basis, not the previous
            # suffix's leaf. Handles survive; the lazy digest cursor is reset.
            seed = self.readers.seeds.view(view.player.idx) if reuse else None
            view._digests = DigestNode.seeded(seed, self.readers.digest_counts, root_view=True)
            self.digest_restores += 1
            view.rows = {card: row for card, row in view.rows.items() if card in present}
            for row in view.rows.values():
                row.memo.clear()
                row._rule_group = None
                row._actor_receipt = None
                if row._generic_digests is not None:
                    seed = (self.readers.seeds.generic(view.player.idx, row.card.command_id)
                            if reuse else None)
                    row._generic_digests = DigestNode.seeded(seed, self.readers.digest_counts)
        self.restore_seconds += perf_counter() - started

