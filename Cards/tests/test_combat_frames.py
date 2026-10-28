"""Sequential combat keeps concrete declarations, atomicity and sampled replay."""
from dataclasses import replace
from unittest.mock import patch

import pytest

from benchmarks.mcts_top_k import make_request
from game.ai.decision_maker import DecisionResult
from game.ai.search_actions import PlayerActionBuilder
from game.ai.search_decision import DecisionProjector
from game.ai.search_descriptors import ChoiceDescriptor
from game.ai.search_recall import PlayerRecall
from game.game_actions.generation.decision_abstraction.combat_frames import (
    CombatDecisionFrame, CombatNextRequest, CombatNextOption, choose_combat)
from game.simulation.search_config import SearchGenerationPolicy
from runtime_write_guard import deny_writes


@pytest.fixture(autouse=True)
def no_files():
    with deny_writes():
        yield


@pytest.mark.parametrize('case', ['attackers', 'blockers'])
def test_all_concrete_leaves_equal_exhaustive_declarations_without_partial_mutation(case):
    request = make_request(case)
    frame = CombatDecisionFrame.capture(request)
    expected = {ChoiceDescriptor.from_option(o) for o in request.options}
    initial = (dict(request.state.combat.attackers), dict(request.state.combat.blockers),
               tuple(c.is_tapped for c in frame.creatures))
    leaves = []
    def walk(frame):
        if not frame.remaining_count:
            leaves.append(ChoiceDescriptor.from_option(frame.finish(request.state, request.player)))
            return
        choices = tuple(CombatNextRequest(request.state, request.player, frame).options)
        assert len(choices) <= 1 + max(map(len, frame.targets))
        for option in choices:
            walk(frame.advance(option.target))
    walk(frame)
    assert len(leaves) == len(set(leaves)) == len(expected)
    assert set(leaves) == expected
    assert initial == (dict(request.state.combat.attackers), dict(request.state.combat.blockers),
                       tuple(c.is_tapped for c in frame.creatures))


def test_invalid_prefixes_abort_and_global_blocking_constraints_keep_full_pipeline():
    request = make_request('blockers')
    frame = CombatDecisionFrame.capture(request)
    with pytest.raises(ValueError):
        frame.advance(object())
    with pytest.raises(ValueError):
        frame.finish(request.state, request.player)
    def fail(request):
        raise RuntimeError('interrupted')
    with pytest.raises(RuntimeError):
        choose_combat(request, frame, fail)
    assert not hasattr(request.state, 'combat_decision_frame')
    combat = request.state.combat
    original = combat._keyword
    with patch.object(combat, '_keyword', side_effect=lambda c, k: k == 'menace' or original(c, k)):
        assert CombatDecisionFrame.capture(request) is None


@pytest.mark.parametrize('blockers', [False, True])
@pytest.mark.parametrize('phase_trigger', [False, True])
def test_each_live_prefix_is_a_native_sampled_root_with_private_recall_and_sibling_isolation(blockers, phase_trigger):
    from test_current_position_worlds import position, controllers
    from test_combat_search_continuation import Capture, phase_catalog
    from game.ai.current_position_worlds import CurrentPositionWorlds
    from game.enums import TurnPhase, ZoneType

    origin, game = position()
    if phase_trigger:
        origin = replace(origin, catalog_factory=phase_catalog)
        game = origin.scenario(42)(controllers())
        game.state.turn.number = 9
    policy = SearchGenerationPolicy(sequential_combat=True)
    class SequentialCapture(Capture):
        sequential_combat = True
        def __init__(self):
            super().__init__(origin)
            self.provider = CurrentPositionWorlds(origin, policy_for=policy)

        def _decide(self, request):
            if type(request) is not CombatNextRequest:
                return super()._decide(request)
            options = tuple(request.options)
            info = DecisionProjector(request, options, self.recall).build().information
            factory = self.provider.prepare(request, info, self.recall, self.knowledge)
            assert factory is not None, self.provider.unsupported_reason
            selected = options[-1]
            action = PlayerActionBuilder(request).action(selected)
            self.captured.append((factory, info, action))
            self.recall = info.recall.append('action', action.describe())
            return DecisionResult(selected)

    state = game.state
    controls = (SequentialCapture(), SequentialCapture())
    for player in state.players:
        player.controller = controls[player.idx]
        for name in ('Charmed Stray', 'Angel of Vitality'):
            card = next(c for c in player.get_cards() if c.name == name)
            player.move_card(card, ZoneType.BATTLEFIELD)
            card.controlled_since = 0
    state.combat.begin()
    state.turn.phase = TurnPhase.DECLARE_ATTACKERS
    if blockers:
        state.combat.declare_attackers(state.players[0],
            {c: state.players[1] for c in state.combat.legal_attackers(state.players[0])})
        state.turn.phase = TurnPhase.DECLARE_BLOCKERS
    game.loop.step(state)
    captured = controls[int(blockers)].captured
    assert len(captured) == 2
    live = dict(state.combat.blockers if blockers else state.combat.attackers)
    for factory, info, action in captured:
        sim = factory(123)
        root = sim.start()
        assert root.player_information() == info
        children = tuple(sim.advance_action(root, option) for option in info.actions)
        assert sim.fork(root) == root
        assert sim.advance_action(root, action) in children
        assert live == dict(state.combat.blockers if blockers else state.combat.attackers)
    from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
    from game.ai.observation_policy import ObservationOpponentPolicy
    from game.ai.action_priors import HeuristicActionPrior
    search = InformationSetSearch(selection='puct', action_prior=HeuristicActionPrior(ObservationOpponentPolicy()),
        opponent_policy=ObservationOpponentPolicy(), rollout_policy=ObservationOpponentPolicy(),
        budget=InformationSearchBudget(worlds=1, iterations=4, max_depth=1, max_extensions=32, max_advances=512))
    factory, info, _ = captured[0]
    result = search.search(factory, info, seed=3)
    assert not result.used_fallback and result.metrics.iterations_completed == 4


def test_configuration_is_opt_in_and_training_rejects_unimplemented_prefix_targets():
    from game.simulation.search_config import SearchAgentConfig
    assert not SearchAgentConfig(deck_knowledge='both_lists').sequential_combat
    with pytest.raises(ValueError):
        SearchAgentConfig(deck_knowledge='both_lists', sequential_combat=1)
    with pytest.raises(ValueError):
        SearchAgentConfig(deck_knowledge='both_lists', sequential_combat=True, parallel_workers=2)
    assert SearchAgentConfig(deck_knowledge='both_lists', sequential_combat='blockers').sequential_combat == 'blockers'
    with pytest.raises(ValueError):
        SearchAgentConfig(deck_knowledge='both_lists', sequential_combat='unknown')
    from game.ai.self_play_config import SelfPlayConfig
    with pytest.raises(ValueError, match='complete final-action menus'):
        SelfPlayConfig(mcts={'deck_knowledge': 'both_lists', 'sequential_combat': True})


def test_family_selection_keeps_native_attack_protocol_and_stages_only_blockers():
    from game.ai.decision_maker import ModularDecisionMaker
    class Controller(ModularDecisionMaker):
        sequential_combat = 'blockers'
        def _decide(self, request):
            seen.append(type(request).__name__)
            return super()._decide(request)
    seen = []
    controller = Controller()
    request = make_request('attackers')
    controller.decide(request)
    assert seen == ['DeclareAttackersRequest']
    seen.clear()
    request = make_request('blockers')
    controller.decide(request)
    assert seen and set(seen) == {'CombatNextRequest'}


@pytest.mark.parametrize('case', ['attackers', 'blockers'])
def test_whole_live_declaration_uses_one_search_budget_and_discards_plan(case):
    from benchmarks.mcts_native_components import declaration
    result = declaration(case, True, 8)
    summary = result['summary']
    assert summary['search_attempts'] == 1 and summary['iterations_completed'] == 8
    assert summary['decisions'] > 1 and summary['fallback_decisions'] == 0
    assert sum(v for k, v in summary['reasons'].items() if k.startswith('combat_plan')) == summary['decisions'] - 1


def test_native_construction_shares_board_projection_but_keeps_each_new_prefix_context():
    from benchmarks.mcts_top_k import make_case
    from game.ai.search_simulator import SearchSimulator, _Replay
    from game.ai.observation import PlayerObservationBuilder
    original_build, original_views = PlayerObservationBuilder.build, _Replay._view_data
    def run(cached):
        views, calls = [], []
        def build(builder, *args, **kwargs):
            calls.append(1)
            return original_build(builder, *args, **kwargs)
        def fresh(replay, *args, **kwargs):
            replay._combat_projection = None
            return original_views(replay, *args, **kwargs)
        policy = SearchGenerationPolicy(sequential_combat=True)
        simulator = SearchSimulator(lambda controllers: make_case('attackers', controllers)[0],
                                    policy_for=policy, track_information=True, structured_records=True)
        def choose(node):
            info = node.player_information()
            views.append(info)
            if node.decision.kind == 'CombatNextRequest':
                return next(a for a in info.actions if a.readonly()['target'] is None)
        with patch.object(PlayerObservationBuilder, 'build', build), \
                patch.object(_Replay, '_view_data', original_views if cached else fresh):
            simulator.follow_actions(choose)
        return views, len(calls)
    reference, before = run(False)
    actual, after = run(True)
    assert actual == reference and after < before
    prefixes = [i.current.readonly()['decision_context']['prefix'] for i in actual
                if i.current.request_kind == 'CombatNextRequest']
    assert list(map(len, prefixes)) == list(range(10))
