from benchmarks.core_reads import board
from game.enums import CardType, CardSubtype
from game.game_actions.resolution.cost_transaction import RuntimeCheckpoint
from game.game_actions.data_structs.ability import ManaAbilityDefinition
from game.game_state import Card, CardDefinition
from game.game_state.modifier import only_empty_builtin_sources
from game.rules.lands import PLAY_LAND_DEFINITION
from game.stat_type import STAT_TYPES
from helper.change_tracking import TrackedSet, TrackedDict


def test_checkpoint_keeps_mutable_scalar_subclasses_and_cycles():
    class MutableInt(int):
        __module__ = "game.test_runtime_hotspots"

    value = MutableInt(7)
    value.payload = [1]
    graph = [42, "immutable", value]
    graph.append(graph)
    checkpoint = RuntimeCheckpoint(graph)
    value.payload.append(2)
    graph.append("extra")
    checkpoint.rollback()
    assert value.payload == [1]
    assert len(graph) == 4 and graph[3] is graph and graph[2] is value
    # A subsequent root can add a newly reachable mutable child.
    child = [3]
    checkpoint.watch(child, root=True)
    child.append(4)
    checkpoint.rollback()
    assert child == [3]


def test_native_empty_checks_keep_live_mutations_and_custom_truth():
    _, card = board(0)
    sources = card.modifier_sources
    changes = []
    effects = TrackedSet(on_change=lambda: changes.append("effect"))
    counters = TrackedDict(on_change=lambda: changes.append("counter"))
    sources[0].active_cont_effects = effects
    sources[1].counters = counters
    assert only_empty_builtin_sources(sources)
    effects.add("effect")
    assert not only_empty_builtin_sources(sources)
    effects.discard("effect")
    counters["counter"] = 1
    assert not only_empty_builtin_sources(sources)
    del counters["counter"]
    assert only_empty_builtin_sources(sources)
    assert changes == ["effect", "effect", "counter", "counter"]

    class NonemptySet(TrackedSet):
        def __bool__(self):
            return True

    sources[0].active_cont_effects = NonemptySet()
    assert not only_empty_builtin_sources(sources)


def test_land_abilities_follow_live_type_changes():
    state, creature = board(0)
    card = Card(CardDefinition("Plains", types=frozenset({CardType.LAND}),
                               subtypes=frozenset({CardSubtype.PLAINS})), creature.owner)
    definitions = card.get_ability_defs(state)
    assert PLAY_LAND_DEFINITION.key in definitions
    assert any(isinstance(d, ManaAbilityDefinition) for d in definitions.values())
    card.set_base_stat(STAT_TYPES, frozenset({CardType.CREATURE}))
    definitions = card.get_ability_defs(state)
    assert PLAY_LAND_DEFINITION.key not in definitions
    assert not any(isinstance(d, ManaAbilityDefinition) for d in definitions.values())
    card.set_base_stat(STAT_TYPES, frozenset({CardType.LAND}))
    assert PLAY_LAND_DEFINITION.key in card.get_ability_defs(state)
