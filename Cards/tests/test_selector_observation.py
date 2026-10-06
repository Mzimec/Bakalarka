import pytest

from game.ai import modular_agent
from game.ai.modular_agent import ModularAgent, HeuristicSelector, Candidate
from game.ai.simple_agent import SimpleAgent
from game.ai.llm_policy import OllamaSelector
from game.game_state import State, Player


def choose(selector, monkeypatch, observer):
    agent = ModularAgent(selector=selector)
    state = State([Player([], agent), Player([], SimpleAgent())])
    monkeypatch.setattr(modular_agent, "observation", observer)
    candidates = [Candidate("first", 2, {}), Candidate("second", 5, {})]
    return agent._choose(state, state.players[0], candidates)


def test_default_heuristic_never_constructs_observation(monkeypatch):
    result = choose(HeuristicSelector(), monkeypatch,
                    lambda *args: pytest.fail("Unused observation was constructed"))
    assert result.action == "second"


@pytest.mark.parametrize("inherit", [False, True])
def test_legacy_and_overridden_selectors_receive_view(monkeypatch, inherit):
    view = {"public": "view"}
    class Selector(HeuristicSelector if inherit else object):
        def choose(self, observation, candidates):
            assert observation is view
            return 1
    assert choose(Selector(), monkeypatch, lambda *args: view).action == "first"


def test_instance_override_keeps_legacy_observation(monkeypatch):
    selector = HeuristicSelector()
    view = {"public": "view"}
    seen = []
    selector.choose = lambda observation, candidates: seen.append(observation) or 0
    assert choose(selector, monkeypatch, lambda *args: view).action == "second"
    assert seen == [view]


def test_custom_selector_can_explicitly_opt_out(monkeypatch):
    class Selector:
        requires_observation = False
        def choose(self, observation, candidates):
            assert observation is None
            return 0
    assert choose(Selector(), monkeypatch, lambda *args: pytest.fail("Unused view")).action == "second"


def test_llm_still_receives_observation(monkeypatch):
    import json
    payloads = []
    selector = OllamaSelector("fake", transport=lambda payload: payloads.append(payload) or '{"choice": 0}')
    view = {"public": "view"}
    choose(selector, monkeypatch, lambda *args: view)
    assert json.loads(payloads[0]["messages"][1]["content"])["state"] == view
