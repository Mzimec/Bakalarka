from __future__ import annotations

from functools import wraps

from game.game_state.state import State
from game.game_state.registers.card_register import CardRegister
from game.game_actions.generation.decision_abstraction.option_space import (
    DecisionOptionSpace,
)


def install_cost_checkpoint_probe(profiler):
    from game.game_actions.resolution.cost_transaction import RuntimeCheckpoint
    from game.game_actions.resolution import cost_scope
    original_fallback = cost_scope._full_checkpoint

    def fallback(reason):
        profiler.count(f"cost.scope.fallback.{reason}")
        return original_fallback(reason)

    profiler.patch_replacement(cost_scope, "_full_checkpoint", fallback)
    original = RuntimeCheckpoint.__init__

    @wraps(original)
    def wrapped(checkpoint, *args, **kwargs):
        original(checkpoint, *args, **kwargs)
        mode = checkpoint.mode
        profiler.count(f"cost.checkpoint.{mode}")
        profiler.count("cost.checkpoint.restore_records", len(checkpoint._restore))
        profiler.count("cost.checkpoint.skipped_cards", checkpoint.skipped_cards)

    profiler.patch_replacement(RuntimeCheckpoint, "__init__", wrapped)


def install_layer_candidate_probe(profiler):
    from game.game_state.registers.layer_candidate_register import LayerCandidateRegister
    original = LayerCandidateRegister.select

    @wraps(original)
    def wrapped(candidates):
        result = original(candidates)
        profiler.count("continuous.candidates.registered", len(candidates.register._by_key))
        profiler.count("continuous.candidates.classified", candidates.last_classified)
        profiler.count("continuous.candidates.selected", len(result))
        profiler.count("continuous.candidates.definitions_checked", candidates.last_definitions_checked)
        return result

    profiler.patch_replacement(LayerCandidateRegister, "select", wrapped)
    profiler.patch_method(LayerCandidateRegister, "select", "continuous.select_candidates")


def install_continuous_refresh_probe(profiler):
    """Count why continuous-effect refresh was requested.

    This intentionally does not time every call: there can be more than a
    million requests and timing every fast-return would heavily distort the run.
    """
    original = State.refresh_continuous_effects

    @wraps(original)
    def wrapped(state, *args, **kwargs):
        profiler.count("continuous.refresh.requests")

        was_recursive = state._refreshing_effects
        was_dirty = state._effects_dirty
        checked_before = state._effects_checked_at
        rebuilds_before = getattr(state, "_continuous_graph_rebuilds", 0)
        reuses_before = getattr(state, "_continuous_graph_reuses", 0)

        result = original(state, *args, **kwargs)

        if not was_recursive:
            profiler.count("continuous.layers.rebuild", getattr(state, "_continuous_graph_rebuilds", 0) - rebuilds_before)
            profiler.count("continuous.layers.reuse", getattr(state, "_continuous_graph_reuses", 0) - reuses_before)

        if was_recursive:
            profiler.count("continuous.refresh.recursive_return")

        elif (
            was_dirty
            or state._effects_checked_at != checked_before
        ):
            profiler.count("continuous.refresh.actual_rebuild")

        else:
            profiler.count("continuous.refresh.clean_return")

        return result

    profiler.patch_replacement(
        State,
        "refresh_continuous_effects",
        wrapped,
    )


def install_card_sync_probe(profiler):
    """Count useful versus empty card-index synchronization."""
    original = CardRegister.synchronise

    @wraps(original)
    def wrapped(register, *args, **kwargs):
        profiler.count("index.card_sync.requests")

        if register._synchronising:
            profiler.count("index.card_sync.recursive")
            return original(register, *args, **kwargs)

        dirty_before = len(register._dirty)

        if dirty_before == 0:
            profiler.count("index.card_sync.empty")
        else:
            profiler.count("index.card_sync.nonempty")
            profiler.count(
                "index.card_sync.dirty_objects",
                dirty_before,
            )

        return original(register, *args, **kwargs)

    profiler.patch_replacement(
        CardRegister,
        "synchronise",
        wrapped,
    )


def install_decision_generation_probe(profiler):
    original = DecisionOptionSpace.__iter__

    def wrapped(space):
        request_type = type(space.request).__name__

        if request_type == "ManaGenerationRequest":
            requirement = space.request.requirement

            if requirement:
                profiler.count("mana.request.nonempty")
                profiler.count(
                    f"mana.request.cmc.{requirement.cmc()}"
                )
            else:
                profiler.count("mana.request.empty")

        return profiler.profile_iterator(
            original(space),
            f"generation.{request_type}",
        )

    profiler.patch_replacement(
        DecisionOptionSpace,
        "__iter__",
        wrapped,
    )


def install_mana_discovery_probe(profiler):
    from game.mana.discovery_context import ManaDiscoveryContext
    original = ManaDiscoveryContext.lookup

    @wraps(original)
    def lookup(context, state, player):
        result = original(context, state, player)
        profiler.count("mana.discovery_cache.hit" if result is not None else "mana.discovery_cache.miss")
        return result

    profiler.patch_replacement(ManaDiscoveryContext, "lookup", lookup)

    from game.mana.source_cache import StaticManaSourceCache
    persistent_lookup = StaticManaSourceCache.lookup

    @wraps(persistent_lookup)
    def lookup_persistent(cache, state, player):
        result = persistent_lookup(cache, state, player)
        suffix = "hit" if result is not None else f"miss.{cache.miss_reason}"
        profiler.count(f"mana.source_revision_cache.{suffix}")
        return result

    profiler.patch_replacement(StaticManaSourceCache, "lookup", lookup_persistent)


def install_mana_search_probe(profiler):
    from game.mana.mana_generator import ManaGenerator
    original = ManaGenerator.generate

    @wraps(original)
    def generate(generator, *args, **kwargs):
        result = original(generator, *args, **kwargs)
        for name in ("states_visited", "equivalent_branches_skipped",
                     "capacity_rejections", "greedy_successes"):
            profiler.count(f"mana.search.{name}", getattr(generator.statistics, name, 0))
        return result

    profiler.patch_replacement(ManaGenerator, "generate", generate)


def install_sba_candidate_probe(profiler):
    from game.game_actions.resolution.sba_candidates import SBACandidateFilter
    original = SBACandidateFilter.select

    @wraps(original)
    def select(selection, state):
        result = original(selection, state)
        profiler.count("sba.candidates.skipped" if result is not None and not result else "sba.candidates.required")
        return result

    profiler.patch_replacement(SBACandidateFilter, "select", select)
    profiler.patch_method(SBACandidateFilter, "select", "sba.candidates")
