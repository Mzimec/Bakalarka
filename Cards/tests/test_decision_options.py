"""Request generation stays lazy, legal and independent of state mutation."""
import pytest
from itertools import islice
from game.ai.decision_maker import DecisionMaker, DecisionResult, decision_hook
from game.game_actions.generation.decision_abstraction.requests import *
from game.game_actions.generation.decision_abstraction.decision_option import (
    AbilityGenerationPolicy, PriorityGenerationPolicy, SelectionGenerationPolicy,
    ManaGenerationPolicy, DecisionOptionSpace,
)
from game.game_actions.generation.pruning.pruning_strategy import LimitPruning
from game.game_actions.data_structs.ability import (
    AbilityDefinition, CastSpellAbility, ActivatedAbility, ManaAbility, TriggerAbilityDefinition,
    TriggerAbility,
)
from game.game_actions.data_structs.game_action import PassPriorityAction, ConcedeAction
from game.game_state import Player, State, Card, CardDefinition
from game.game_loop.minimal_game import ScriptedController
from game.enums import CardType, ZoneType, TurnPhase, ManaType
from game.mana.mana_value import ManaRequirement
from game.rules.lands import LandPlayAction

@pytest.fixture
def state():
    state = State([Player([], ScriptedController()), Player([], ScriptedController(), idx=1)])
    state.turn.phase = TurnPhase.PRECOMBAT_MAIN
    return state


def card(state, player=0, zone=ZoneType.HAND, **kwargs):
    owner = state.players[player]
    value = Card(CardDefinition("Test", **kwargs), owner)
    owner.add_card(value, zone, state)
    return value


def test_priority_land_pruning_does_not_remove_pass_or_concede(state):
    land = card(state, types=frozenset({CardType.LAND}))
    request = PriorityDecisionRequest(state, state.active_player)
    choices = list(request.options)
    assert [type(c) for c in choices] == [PassPriorityAction, LandPlayAction, ConcedeAction]
    assert land.get_zone() == ZoneType.HAND
    choices = list(request.option_space(PriorityGenerationPolicy(land_play_ps=LimitPruning(0))))
    assert [type(c) for c in choices] == [PassPriorityAction, ConcedeAction]


def test_wrong_priority_and_policy_fail(state):
    with pytest.raises(ValueError, match="priority"):
        list(PriorityDecisionRequest(state, state.players[1]).options)
    with pytest.raises(TypeError, match="PriorityGenerationPolicy"):
        list(PriorityDecisionRequest(state, state.active_player).option_space(AbilityGenerationPolicy()))


def test_spaces_are_lazy_reiterable_live_views(state):
    request = MulliganBottomRequest(state, state.active_player, 1)
    space = request.options
    a, b = card(state), card(state)
    assert list(space) == [(a,), (b,)]
    assert list(space) == list(space)
    state.active_player.move_card(a, ZoneType.GRAVEYARD, state)
    assert list(space) == [(b,)]


def test_bottom_order_matters_but_discard_order_does_not(state):
    a, b, c = card(state), card(state), card(state)
    player = state.active_player
    assert len(list(MulliganBottomRequest(state, player, 2).options)) == 6
    assert list(DiscardRequest(state, player, 2).options) == [(a,b), (a,c), (b,c)]
    assert list(DiscardRequest(state, player, 0).options) == [()]
    assert list(MulliganRequest(state, player).options) == [False, True]
    assert list(MulliganRequest(state, player, can_mulligan=False).options) == [False]


@pytest.mark.parametrize("count", [-1, True, 1])
def test_invalid_card_counts(state, count):
    with pytest.raises(ValueError):
        list(DiscardRequest(state, state.active_player, count).options)


def test_pruning_consumes_only_requested_witnesses():
    seen = []
    def candidates():
        for i in range(100):
            seen.append(i)
            yield i
    assert list(LimitPruning(2).prune(candidates())) == [0, 1]
    assert seen == [0, 1]


def test_ability_subclasses_and_single_ability_options(state):
    definition = AbilityDefinition()
    source = card(state, zone=ZoneType.BATTLEFIELD, types=frozenset({CardType.ARTIFACT}),
                  abilities=frozenset({definition}))
    ability = definition.to_ability(source, state.active_player)
    assert isinstance(ability, ActivatedAbility)
    assert isinstance(AbilityDefinition(is_spell=True).to_ability(source, state.active_player), CastSpellAbility)
    assert isinstance(AbilityDefinition(is_mana_ability=True).to_ability(source, state.active_player), ManaAbility)
    assert isinstance(TriggerAbilityDefinition().to_ability(source, state.active_player), TriggerAbility)
    choices = list(AbilityDecisionRequest(state, state.active_player, ability).options)
    assert len(choices) == 1 and choices[0].source is source
    assert list(PriorityDecisionRequest(state, state.active_player).option_space(
        PriorityGenerationPolicy(ability_space_ps=LimitPruning(0), include_concede=False)
    ))[0].player is state.active_player
    with pytest.raises(ValueError, match="requesting player"):
        list(AbilityDecisionRequest(state, state.players[1], ability).options)


def test_mana_payment_space_does_not_spend_pool(state):
    player = state.active_player
    req = ManaRequirement()
    req.add_pair(frozenset({ManaType.RED}), 1)
    request = ManaGenerationRequest(state, player, req)
    assert list(request.options) == []
    player.mana_pool.add_pair(ManaType.RED, 1)
    plans = list(request.options)
    assert len(plans) == 1 and not plans[0].mana_plan
    assert player.mana_pool[ManaType.RED] == 1


def creature(state, player=0, **kwargs):
    result = card(state, player, ZoneType.BATTLEFIELD, types=frozenset({CardType.CREATURE}),
                  power=2, toughness=2, **kwargs)
    state.players[player].last_turn_started = 1
    result.controlled_since = 0
    return result


def test_combat_spaces_validate_whole_declarations_and_do_not_tap(state):
    attacker = creature(state, keywords=frozenset({"menace"}))
    b1, b2 = creature(state, 1), creature(state, 1)
    state.combat.begin()
    state.turn.phase = TurnPhase.DECLARE_ATTACKERS
    choices = list(DeclareAttackersRequest(state, state.active_player).options)
    assert choices == [{}, {attacker: state.players[1]}]
    assert not attacker.is_tapped and not state.combat.attackers
    state.combat.declare_attackers(state.active_player, choices[1])
    state.turn.phase = TurnPhase.DECLARE_BLOCKERS
    blocks = list(DeclareBlockersRequest(state, state.players[1]).options)
    assert blocks == [{}, {b1: attacker, b2: attacker}]
    assert not state.combat.blockers


def test_resolution_choice_belongs_to_affected_player_without_priority(state):
    victim = state.players[1]
    a, b = card(state, 1), card(state, 1)
    request = AbilityResolutionRequest(state, victim, object(), (a, b), 1, "discard")
    assert list(request.options) == [(a,), (b,)]
    assert len(victim.hand) == 2


def test_new_controller_uses_same_request_entrypoint_from_legacy_hooks(state):
    class First(DecisionMaker):
        def _decide(self, request):
            self.last = request
            return DecisionResult(next(iter(request.options)), {"selected": True})
    controller = First()
    player = state.active_player
    player.controller = controller
    assert isinstance(player.get_action(state), PassPriorityAction)
    assert isinstance(controller.last, PriorityDecisionRequest)
    assert decision_hook(controller, "choose_mulligan")(state, player, 0) is False
    assert isinstance(controller.last, MulliganRequest)
    result = controller.decide(MulliganRequest(state, player))
    assert result.value is False and result.info["selected"] and result.info["elapsed_time"] >= 0


def test_resolution_operation_calls_new_decision_maker(state):
    from game.cards.starter_support import ChooseMoveOperation
    from game.game_actions.data_structs.game_action import ResolutionContext
    class Last(DecisionMaker):
        def _decide(self, request):
            self.request = request
            return DecisionResult(list(request.options)[-1])
    player = state.players[1]
    controller = Last()
    player.controller = controller
    a, b = card(state, 1), card(state, 1)
    context = ResolutionContext(controller=state.active_player)
    ChooseMoveOperation(context, player, 1).execute(state)
    assert isinstance(controller.request, AbilityResolutionRequest)
    assert a.get_zone() == ZoneType.HAND and b.get_zone() == ZoneType.GRAVEYARD


def test_wrong_combat_step_is_rejected(state):
    with pytest.raises(ValueError):
        list(DeclareBlockersRequest(state, state.players[1]).options)


def test_unpayable_mana_activation_is_omitted_from_priority(state):
    from game.enums import CardSubtype
    land = card(state, zone=ZoneType.BATTLEFIELD, types=frozenset({CardType.LAND}),
                subtypes=frozenset({CardSubtype.MOUNTAIN}))
    land.is_tapped = True
    choices = list(PriorityDecisionRequest(state, state.active_player).options)
    assert [type(c) for c in choices] == [PassPriorityAction, ConcedeAction]
    assert land.is_tapped
