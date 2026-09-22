"""Autopass skips policy work, preserves legal choices and remains observable."""
import pytest

from game.ai.decision_maker import PriorityDecisionRequest
from game.ai.simple_agent import SimpleAgent, DecisionLimitReached
from game.ai.modular_agent import ModularAgent
from game.cards.starter_cards import starter_catalog
from game.enums import TurnPhase, ZoneType
from game.game_state import State, Player, Card
from game.game_actions.data_structs.game_action import PassPriorityAction
from game.game_actions.generation.decision_abstraction.auto_pass import PRIORITY_AUTO_PASS
from game.game_actions.generation.decision_abstraction.measurement import observe_decisions
from game.reporting.decision_statistics import DecisionStatistics, format_decision_statistics
from game.simulation.config import make_controller


def board(agent):
    state = State([Player([], agent, idx=0), Player([], SimpleAgent(), idx=1)])
    state.turn.phase = TurnPhase.PRECOMBAT_MAIN
    return state, state.active_player


def add(state, name, zone=ZoneType.BATTLEFIELD):
    card = Card(starter_catalog()[name], state.active_player)
    state.active_player.add_card(card, zone)
    return card


@pytest.mark.parametrize("agent_type", [SimpleAgent, ModularAgent])
@pytest.mark.parametrize("phase", [TurnPhase.UPKEEP, TurnPhase.PRECOMBAT_MAIN, TurnPhase.END_STEP])
def test_quiet_window_uses_one_pipeline_without_mana_or_equivalence_work(agent_type, phase, monkeypatch):
    logs, metrics = [], []
    agent = agent_type(log=lambda *args: logs.append(args))
    state, player = board(agent)
    state.turn.phase = phase
    island = add(state, "Island")

    def unexpected(*args, **kwargs):
        pytest.fail("Quiet priority must not expand actions or invoke selection")

    from game.game_actions.generation.equivalence import ConservativeEquivalencePolicy
    from game.game_actions.generation.decision_abstraction.action_pipelines import AbilityDecisionGenerationPipeline
    monkeypatch.setattr(PRIORITY_AUTO_PASS, "can_pass", unexpected)
    monkeypatch.setattr(state, "get_mana_sources", unexpected)
    monkeypatch.setattr(ConservativeEquivalencePolicy, "bind", unexpected)
    monkeypatch.setattr(AbilityDecisionGenerationPipeline, "generate", unexpected)
    if isinstance(agent, ModularAgent):
        monkeypatch.setattr(agent.selector, "choose", unexpected)
    with observe_decisions(lambda controller, request, sample: metrics.append(sample)):
        result = agent.decide(PriorityDecisionRequest(state, player))
    assert isinstance(result.value, PassPriorityAction)
    assert agent.decisions == 1 and len(logs) == 1
    assert logs[0][2] == "pass" and logs[0][3]["auto_pass"]
    assert metrics[0]["auto_pass"] and metrics[0]["auto_pass_reason"] == "only_pass_candidate"
    assert metrics[0]["options_observed"] == metrics[0]["option_space_size"] == 1
    assert len(metrics[0]["option_spaces"]) == 1
    assert state.priority.current_player is player and state.turn.phase == phase
    assert not island.is_tapped and state.stack.is_empty()


def test_unpayable_spell_does_not_trigger_a_second_generation_or_model_call(monkeypatch):
    agent = ModularAgent()
    state, player = board(agent)
    add(state, "Soulblade Djinn", ZoneType.HAND)
    calls = []
    generate = agent.candidates.generate

    def counted(*args):
        calls.append(True)
        return generate(*args)

    monkeypatch.setattr(agent.candidates, "generate", counted)
    monkeypatch.setattr(agent.selector, "choose", lambda *args: pytest.fail("Unexpected model call"))
    monkeypatch.setattr("game.ai.modular_agent.observation", lambda *args: pytest.fail("Unexpected observation"))
    result = agent.decide(PriorityDecisionRequest(state, player))
    assert isinstance(result.value, PassPriorityAction) and len(calls) == 1
    assert result.info["auto_pass_reason"] == "only_pass_candidate"
    assert result.info["decision_metrics"]["option_space_size"] == 1


@pytest.mark.parametrize("agent_type", [SimpleAgent, ModularAgent])
@pytest.mark.parametrize("action", ["land", "spell"])
def test_playable_actions_are_not_bypassed(agent_type, action):
    agent = agent_type()
    state, player = board(agent)
    if action == "land":
        add(state, "Island", ZoneType.HAND)
    else:
        for _ in range(3):
            add(state, "Island")
        add(state, "Cloudkin Seer", ZoneType.HAND)
    result = agent.decide(PriorityDecisionRequest(state, player))
    assert not isinstance(result.value, PassPriorityAction)
    assert not result.info["decision_metrics"]["auto_pass"]


def test_disabled_autopass_keeps_selector_and_observation(monkeypatch):
    agent = ModularAgent(auto_pass=False)
    state, player = board(agent)
    calls = []
    monkeypatch.setattr(agent.selector, "choose", lambda *args: calls.append(args) or 0)
    result = agent.decide(PriorityDecisionRequest(state, player))
    assert isinstance(result.value, PassPriorityAction) and len(calls) == 1
    assert not result.info["decision_metrics"]["auto_pass"]


@pytest.mark.parametrize("agent_type", [SimpleAgent, ModularAgent])
def test_autopass_still_enforces_runaway_budget(agent_type):
    agent = agent_type(max_decisions=1)
    state, player = board(agent)
    agent.decide(PriorityDecisionRequest(state, player))
    with pytest.raises(DecisionLimitReached):
        agent.decide(PriorityDecisionRequest(state, player))


@pytest.mark.parametrize("reason", ["trigger", "replacement", "other_player", "untap", "cleanup"])
def test_console_probe_defers_sensitive_or_invalid_windows(reason):
    state, player = board(SimpleAgent())
    add(state, "Island")
    if reason == "trigger":
        state.runtime_triggers = [object()]
    elif reason == "replacement":
        state.replacement_rules.append(object())
    elif reason == "other_player":
        player = state.players[1]
    else:
        state.turn.phase = getattr(TurnPhase, reason.upper())
    assert not PRIORITY_AUTO_PASS.can_pass(state, player)


@pytest.mark.parametrize("kind", ["simple", "modular", "llm", "human"])
def test_config_can_disable_autopass(kind):
    config = {"type": kind, "auto_pass": False}
    if kind == "llm":
        config["llm"] = {"model": "unused"}
    assert not make_controller(config, seed=0, max_decisions=10).auto_pass


def test_config_rejects_non_boolean_autopass():
    with pytest.raises(ValueError, match="boolean"):
        make_controller({"auto_pass": "false"}, seed=0, max_decisions=10)


def test_autopass_is_reported_and_merge_accepts_old_summaries():
    agent = SimpleAgent()
    state, player = board(agent)
    sample = agent.decide(PriorityDecisionRequest(state, player)).info["decision_metrics"]
    stats = DecisionStatistics()
    stats.record(0, "Player", "SimpleAgent", "agent", sample)
    old = stats.summary()
    for bucket in [old[0]["overall"], *old[0]["by_request"].values(),
                   *old[0]["by_options_observed"]["PriorityDecisionRequest"].values()]:
        del bucket["auto_pass_count"]
    stats.merge(old)
    result = stats.summary()
    assert result[0]["overall"]["count"] == 2
    assert result[0]["overall"]["auto_pass_count"] == 1
    assert "auto-passes 1" in "\n".join(format_decision_statistics(result))


def test_autopass_preserves_seeded_game_events(tmp_path):
    import json
    from game.simulation.match_runner import run_match
    events, outcomes = [], []
    for enabled in (False, True):
        path = tmp_path / f"autopass-{enabled}.jsonl"
        result = run_match(("white", "white"), path, seed=123, max_turns=6,
                           controllers=(ModularAgent(auto_pass=enabled), SimpleAgent(auto_pass=enabled)))
        assert result.status == "turn_limit", result.error
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        events.append([row for row in rows if row["kind"] in {"event", "action_result", "resolution"}])
        outcomes.append((result.status, result.turns, result.decisions, result.winner_index))
    assert outcomes[0] == outcomes[1]
    assert events[0] == events[1]
