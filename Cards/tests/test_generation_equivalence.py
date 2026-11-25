"""Symmetry is reduced before expansion, without losing concrete resources."""
from dataclasses import replace
import pytest
from game.game_state import State, Player, Card, CardDefinition
from game.game_loop.minimal_game import ScriptedController
from game.enums import ZoneType as Z, ManaType as M, CardType as T, TurnPhase, CounterType
from game.rules.lands import basic_land, mana_ability, LandPlayAction
from game.game_actions.generation.equivalence import EquivalenceContext
from game.game_actions.generation.decision_abstraction.requests import PriorityDecisionRequest
from game.game_actions.generation.decision_abstraction.policies import PriorityGenerationPolicy
from game.game_actions.data_structs.ability import Ability, CastSpellAbilityDefinition, SubAbilityDefinition
from game.game_actions.data_structs.action_node import EffectActionNode, ImmutableEffectToSlotMap
from game.game_actions.card_effects import MoveSourceEffect
from game.game_actions.mana_effects import AddManaEffect
from game.mana.mana_generator import ManaGenerator
from game.mana.mana_value import ManaRequirement, ManaRequirementFragment


def game():
    state = State([Player([], ScriptedController(), name="A"), Player([], ScriptedController(), name="B")])
    state.turn.phase = TurnPhase.PRECOMBAT_MAIN
    return state


def add(state, definition, zone=Z.BATTLEFIELD, owner=None):
    card = Card(definition, owner or state.active_player)
    card.owner.add_card(card, zone)
    return card


def req(*pairs):
    return ManaRequirement(frags=tuple(ManaRequirementFragment(frozenset(colors), count) for colors, count in pairs))


def spell_definition():
    effect = MoveSourceEffect("enter", Z.BATTLEFIELD)
    cast = CastSpellAbilityDefinition(key="cast", action_subdefs=(SubAbilityDefinition(
        action_node=EffectActionNode(ImmutableEffectToSlotMap({"enter": frozenset()})),
        effects=frozenset({effect}),
    ),))
    return CardDefinition("Twins", mana_cost="{0}", types=frozenset({T.CREATURE}),
                          power=2, toughness=2, abilities=frozenset({cast}))


def test_duplicate_land_plays_have_one_representative_and_can_be_disabled():
    state = game()
    cards = [add(state, basic_land("Island"), Z.HAND) for _ in range(3)]
    request = PriorityDecisionRequest(state, state.active_player)
    assert [a.card for a in request.options if isinstance(a, LandPlayAction)] == [cards[0]]
    full = request.option_space(PriorityGenerationPolicy(equivalence=None))
    assert [a.card for a in full if isinstance(a, LandPlayAction)] == cards


def test_duplicate_spells_do_not_reach_expensive_action_generation(monkeypatch):
    state = game()
    cards = [add(state, spell_definition(), Z.HAND) for _ in range(3)]
    seen = []
    original = Ability.generate_actions
    def recording(self, *args, **kwargs):
        seen.append(self.source)
        yield from original(self, *args, **kwargs)
    monkeypatch.setattr(Ability, "generate_actions", recording)
    list(PriorityDecisionRequest(state, state.active_player).options)
    assert seen == cards[:1]


def test_identical_activated_abilities_with_different_local_names_collapse():
    state = game()
    first = mana_ability(M.BLUE)
    second = replace(first, key="other_name")
    card = add(state, CardDefinition("Rock", types=frozenset({T.ARTIFACT}),
                                   abilities=frozenset({first, second})))
    context = EquivalenceContext(state)
    abilities = [d.to_ability(card, state.active_player) for d in (first, second)]
    assert len(list(context.representatives(abilities))) == 1


@pytest.mark.parametrize("change", [
    lambda c, s: setattr(c.state, "tapped", True),
    lambda c, s: setattr(c.state, "damage_marked", 1),
    lambda c, s: c.state.counters.__setitem__(CounterType.LOYALTY, 1),
    lambda c, s: c.set_controller(s.players[1]),
    lambda c, s: setattr(c, "controlled_since", -1),
    lambda c, s: setattr(c.state, "skip_untap", (c.zone_revision, s.active_player)),
])
def test_runtime_differences_keep_distinct_choices(change):
    state = game()
    a, b = [add(state, basic_land("Island")) for _ in range(2)]
    assert EquivalenceContext(state).card_key(a) == EquivalenceContext(state).card_key(b)
    change(b, state)
    context = EquivalenceContext(state)
    assert context.card_key(a) != context.card_key(b)


def test_modified_cost_and_unknown_rules_are_not_merged():
    from game.stat_type import STAT_MANA_COST
    from game.mana.mana_value import ManaValue
    state = game()
    a, b = [add(state, spell_definition(), Z.HAND) for _ in range(2)]
    b.set_base_stat(STAT_MANA_COST, ManaValue("{1}"))
    context = EquivalenceContext(state)
    assert context.card_key(a) != context.card_key(b)
    class CustomCast(CastSpellAbilityDefinition):
        pass
    definition = replace(spell_definition(), abilities=frozenset({CustomCast()}))
    c, d = [add(state, definition, Z.HAND) for _ in range(2)]
    context = EquivalenceContext(state)
    assert context.card_key(c) != context.card_key(d)


def test_active_identity_observers_disable_merging():
    state = game()
    a, b = [add(state, basic_land("Island")) for _ in range(2)]
    state.runtime_triggers = [object()]
    context = EquivalenceContext(state)
    assert context.card_key(a) != context.card_key(b)


def test_mana_keeps_capacity_and_reserved_sources():
    state = game()
    cards = [add(state, basic_land("Island")) for _ in range(3)]
    generator = ManaGenerator()
    plan = generator.generate(req(({M.BLUE}, 2)), state, state.active_player, reserved={cards[0]})
    assert {step.source for step in plan.steps} == set(cards[1:])
    assert generator.generate(req(({M.BLUE}, 3)), state, state.active_player, reserved={cards[0]}) is None


def test_mana_impossible_search_skips_symmetric_branches():
    state = game()
    for _ in range(6):
        add(state, basic_land("Island"))
    requirement = req(({M.BLUE, M.WHITE}, 3), ({M.RED, M.BLACK}, 1))
    reduced = ManaGenerator()
    full = ManaGenerator(deduplicate_equivalent=False)
    assert reduced.generate(requirement, state, state.active_player) is None
    assert full.generate(requirement, state, state.active_player) is None
    assert reduced.statistics.equivalent_branches_skipped > 0
    assert reduced.statistics.states_visited < full.statistics.states_visited


def test_bundle_capacity_and_concrete_representatives():
    state = game()
    base = mana_ability(M.BLUE)
    effect = AddManaEffect("double", M.BLUE, 2)
    definition = replace(base, action_subdefs=(SubAbilityDefinition(
        action_node=EffectActionNode(ImmutableEffectToSlotMap({effect.key: frozenset()})),
        effects=frozenset({effect}),
    ),))
    cards = [add(state, CardDefinition("Double rock", types=frozenset({T.ARTIFACT}),
                                     abilities=frozenset({definition}))) for _ in range(2)]
    plan = ManaGenerator().generate(req(({M.BLUE}, 3)), state, state.active_player)
    assert len(plan.steps) == 2
    assert {step.source for step in plan.steps} == set(cards)
    assert plan.payment[M.BLUE] == 3


def test_source_filter_runs_before_representative_selection():
    from game.game_actions.generation.pruning.pruning_strategy import FilterPruning
    state = game()
    a, b = [add(state, basic_land("Island"), Z.HAND) for _ in range(2)]
    policy = PriorityGenerationPolicy(land_play_ps=FilterPruning(lambda ability: ability.source is b))
    options = PriorityDecisionRequest(state, state.active_player).option_space(policy)
    assert [option.card for option in options if isinstance(option, LandPlayAction)] == [b]


def test_fresh_generation_does_not_reuse_stale_signatures():
    state = game()
    a, b = [add(state, basic_land("Island")) for _ in range(2)]
    request = PriorityDecisionRequest(state, state.active_player)
    first = [option for option in request.options if getattr(option, "ability", None)]
    assert len(first) == 1
    a.state.tapped = True
    second = [option for option in request.options if getattr(option, "ability", None)]
    assert len(second) == 1 and second[0].source is b


def test_attachments_and_live_continuous_effects_keep_sources_distinct():
    from game.game_state.modifier import ContinuousEffect, ContinuousEffectDefinition, ContinuousEffectState
    from game.game_state.modifier import PermanentDuration, DynamicTargetingStrategy, AddIntModifier
    from game.target.target_spec import QueryTargetSpec
    from game.game_state.registers.card_register import IK_KEY
    from helper.query_system.query import EqQuery
    from game.stat_type import STAT_POWER
    state = game()
    a, b = [add(state, spell_definition()) for _ in range(2)]
    b.state.attached[a.key] = a
    context = EquivalenceContext(state)
    assert context.card_key(a) != context.card_key(b)
    b.state.attached.clear()
    effect = ContinuousEffect("bonus", ContinuousEffectDefinition(
        PermanentDuration(), a, state.time_stamp,
        DynamicTargetingStrategy(QueryTargetSpec(EqQuery(IK_KEY, a.key))),
        {STAT_POWER: [AddIntModifier(1)]},
    ), ContinuousEffectState(set()))
    state.add_continuous_effect(effect)
    context = EquivalenceContext(state)
    assert not context.enabled
    assert context.card_key(a) != context.card_key(b)


@pytest.mark.parametrize("bundle", [False, True])
def test_reduced_mana_feasibility_matches_full_search_across_costs(bundle):
    state = game()
    for name in ("Island", "Island", "Mountain"):
        add(state, basic_land(name))
    if bundle:
        base = mana_ability(M.BLUE)
        effect = AddManaEffect("two", M.BLUE, 2)
        definition = replace(base, action_subdefs=(SubAbilityDefinition(
            action_node=EffectActionNode(ImmutableEffectToSlotMap({effect.key: frozenset()})),
            effects=frozenset({effect}),
        ),))
        for _ in range(2):
            add(state, CardDefinition("Double", types=frozenset({T.ARTIFACT}), abilities=frozenset({definition})))
    for blue in range(5):
        for red in range(3):
            requirement = req(({M.BLUE}, blue), ({M.RED}, red))
            reduced = ManaGenerator().generate(requirement, state, state.active_player)
            full = ManaGenerator(deduplicate_equivalent=False).generate(requirement, state, state.active_player)
            assert (reduced is not None) == (full is not None)
            if reduced is not None:
                assert len(reduced.steps) == len(full.steps)
                assert len({step.source for step in reduced.steps}) == len(reduced.steps)


def test_old_basic_lands_are_equivalent_across_entry_turns_but_fresh_land_is_not():
    state = game()
    state.turn.number = 1
    a = add(state, basic_land("Island"))
    state.turn.number = 2
    b = add(state, basic_land("Island"))
    state.turn.number = 5
    state.active_player.last_turn_started = 5
    fresh = add(state, basic_land("Island"))
    context = EquivalenceContext(state)
    assert context.card_key(a) == context.card_key(b)
    assert context.card_key(a) != context.card_key(fresh)
