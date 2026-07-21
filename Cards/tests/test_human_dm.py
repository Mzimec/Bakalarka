"""
test_human_input.py — testy pro HumanDecisionMaker a ActionBuilderSession.

Spuštění (ze složky Cards/):
    pytest tests/test_human_input.py -v

Žádný test nezavolá skutečný input() — vše je mockováno přes monkeypatch
nebo přímé volání interních metod.
"""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock
from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# Minimální stub třídy (nepotřebujeme celý game engine)
# ---------------------------------------------------------------------------

@dataclass
class StubCardDef:
    name: str

class StubCard:
    def __init__(self, name: str, owner=None):
        self.card_def = StubCardDef(name)
        self.owner = owner

class StubAbilityDef:
    def __init__(self, key: str, has_cost: bool = False, has_action: bool = True):
        self.key = key
        self.cost_action = MagicMock() if has_cost else None
        self.action = MagicMock() if has_action else None

class StubAbility:
    def __init__(self, source: StubCard, key: str, has_cost: bool = False, has_action: bool = True):
        self.source = source
        self.data = StubAbilityDef(key, has_cost, has_action)
        self._actions = []

    def generate_actions(self, state):
        return self._actions

class StubState:
    pass

class StubPlayer:
    def __init__(self, name: str = "Hráč"):
        self.name = name


# ---------------------------------------------------------------------------
# Import testovaného modulu
# Předpokládáme že test běží z Cards/ (nebo že sys.path je nastaven).
# Pokud ne, upravte cestu.
# ---------------------------------------------------------------------------

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from game.human_input import (  # type: ignore
    ActionBuilderSession,
    HumanDecisionMaker,
    _resolve_command,
    _Step,
    _SessionData,
)


# ---------------------------------------------------------------------------
# _resolve_command
# ---------------------------------------------------------------------------

class TestResolveCommand:
    def test_full_play(self):
        assert _resolve_command("play") == "play"

    def test_alias_p(self):
        assert _resolve_command("p") == "play"

    def test_full_activate(self):
        assert _resolve_command("activate") == "activate"

    def test_alias_a(self):
        assert _resolve_command("a") == "activate"

    def test_pass(self):
        assert _resolve_command("pass") == "pass"

    def test_empty_string_is_pass(self):
        assert _resolve_command("") == "pass"

    def test_unknown_returns_none(self):
        assert _resolve_command("xyz") is None

    def test_case_insensitive(self):
        assert _resolve_command("PLAY") == "play"
        assert _resolve_command("Pass") == "pass"

    def test_whitespace_stripped(self):
        assert _resolve_command("  play  ") == "play"


# ---------------------------------------------------------------------------
# ActionBuilderSession — step_command
# ---------------------------------------------------------------------------

class TestStepCommand:

    def _make_session(self):
        return ActionBuilderSession(StubState(), StubPlayer())

    def test_pass_command_goes_directly_to_done(self):
        session = self._make_session()
        with patch("builtins.input", return_value="pass"):
            advanced = session._step_command()
        assert advanced is True
        assert session._step == _Step.DONE
        assert session._data.command == "pass"

    def test_unknown_command_returns_false(self):
        session = self._make_session()
        with patch("builtins.input", return_value="goblin"), \
             patch("builtins.print"):
            advanced = session._step_command()
        assert advanced is False
        assert session._data.command is None

    def test_play_with_no_cards_returns_false(self):
        session = self._make_session()
        # _get_playable_cards vrací [] by default
        with patch("builtins.input", return_value="play"), \
             patch("builtins.print"):
            advanced = session._step_command()
        assert advanced is False

    def test_play_with_cards_advances_to_source(self):
        session = self._make_session()
        card = StubCard("Goblin")
        ability = StubAbility(card, "play_card")
        session._get_playable_cards = lambda: [ability]
        with patch("builtins.input", return_value="play"), \
             patch("builtins.print"):
            advanced = session._step_command()
        assert advanced is True
        assert session._step == _Step.SOURCE
        assert session._data.command == "play"


# ---------------------------------------------------------------------------
# ActionBuilderSession — step_source
# ---------------------------------------------------------------------------

class TestStepSource:

    def _session_at_source(self, abilities):
        session = ActionBuilderSession(StubState(), StubPlayer())
        session._step = _Step.SOURCE
        session._data.command = "play"
        session._get_available_abilities = lambda: abilities
        return session

    def test_no_abilities_returns_false(self):
        session = self._session_at_source([])
        with patch("builtins.print"):
            result = session._step_source()
        assert result is False

    def test_back_on_empty_input_returns_false(self):
        card = StubCard("Elf")
        ability = StubAbility(card, "play_card")
        session = self._session_at_source([ability])
        with patch("builtins.input", return_value=""), \
             patch("builtins.print"):
            result = session._step_source()
        assert result is False
        assert session._data.ability is None

    def test_valid_index_advances_to_target(self):
        card = StubCard("Dragon")
        ability = StubAbility(card, "play_card", has_cost=False)
        session = self._session_at_source([ability])
        with patch("builtins.input", return_value="0"), \
             patch("builtins.print"):
            result = session._step_source()
        assert result is True
        assert session._data.ability is ability
        # Žádný cost → přeskočíme na TARGET
        assert session._step == _Step.TARGET

    def test_ability_with_cost_advances_to_cost(self):
        card = StubCard("Wizard")
        ability = StubAbility(card, "tap_ability", has_cost=True)
        session = self._session_at_source([ability])
        with patch("builtins.input", return_value="0"), \
             patch("builtins.print"):
            result = session._step_source()
        assert result is True
        assert session._step == _Step.COST


# ---------------------------------------------------------------------------
# ActionBuilderSession — step_confirm
# ---------------------------------------------------------------------------

class TestStepConfirm:

    def _session_at_confirm(self):
        session = ActionBuilderSession(StubState(), StubPlayer())
        session._step = _Step.CONFIRM
        session._data.command = "play"
        session._data.ability = StubAbility(StubCard("X"), "k")
        session._data.cost_binding = {}
        session._data.target_binding = {}
        return session

    def test_y_advances_to_done(self):
        session = self._session_at_confirm()
        with patch("builtins.input", return_value="y"), \
             patch("builtins.print"):
            result = session._step_confirm()
        assert result is True
        assert session._step == _Step.DONE

    def test_n_returns_false(self):
        session = self._session_at_confirm()
        with patch("builtins.input", return_value="n"), \
             patch("builtins.print"):
            result = session._step_confirm()
        assert result is False

    def test_reset_resets_to_command(self):
        session = self._session_at_confirm()
        with patch("builtins.input", return_value="reset"), \
             patch("builtins.print"):
            result = session._step_confirm()
        assert result is True
        assert session._step == _Step.COMMAND
        assert session._data.command is None

    def test_unknown_input_returns_false(self):
        session = self._session_at_confirm()
        with patch("builtins.input", return_value="maybe"), \
             patch("builtins.print"):
            result = session._step_confirm()
        assert result is False


# ---------------------------------------------------------------------------
# ActionBuilderSession — _prev_step a _reset_from
# ---------------------------------------------------------------------------

class TestPrevStepAndReset:

    def test_prev_step_from_source_is_command(self):
        s = ActionBuilderSession(StubState(), StubPlayer())
        s._step = _Step.SOURCE
        assert s._prev_step() == _Step.COMMAND

    def test_prev_step_from_command_is_none(self):
        s = ActionBuilderSession(StubState(), StubPlayer())
        s._step = _Step.COMMAND
        assert s._prev_step() is None

    def test_reset_from_source_clears_ability(self):
        s = ActionBuilderSession(StubState(), StubPlayer())
        s._data.command = "play"
        s._data.ability = StubAbility(StubCard("X"), "k")
        s._data.cost_binding = {}
        s._data.target_binding = {}
        s._reset_from(_Step.SOURCE)
        assert s._data.ability is None
        assert s._data.cost_binding is None
        assert s._data.target_binding is None
        # command zůstane
        assert s._data.command == "play"

    def test_reset_from_command_clears_everything(self):
        s = ActionBuilderSession(StubState(), StubPlayer())
        s._data.command = "play"
        s._data.ability = StubAbility(StubCard("X"), "k")
        s._reset_from(_Step.COMMAND)
        assert s._data.command is None
        assert s._data.ability is None

    def test_reset_from_target_clears_only_target(self):
        s = ActionBuilderSession(StubState(), StubPlayer())
        s._data.command = "activate"
        s._data.ability = StubAbility(StubCard("Y"), "k")
        s._data.cost_binding = {"tap": {}}
        s._data.target_binding = {"enemy": {}}
        s._reset_from(_Step.TARGET)
        assert s._data.target_binding is None
        assert s._data.cost_binding == {"tap": {}}  # zachováno


# ---------------------------------------------------------------------------
# ActionBuilderSession — run() s pass příkazem
# ---------------------------------------------------------------------------

class TestRunPass:

    def test_run_returns_pass_priority_action(self):
        from game.game_actions.data_structs.game_action import PassPriorityAction
        player = StubPlayer()
        session = ActionBuilderSession(StubState(), player)
        with patch("builtins.input", return_value="pass"), \
             patch("builtins.print"):
            action = session.run()
        assert isinstance(action, PassPriorityAction)
        assert action.player is player


# ---------------------------------------------------------------------------
# HumanDecisionMaker
# ---------------------------------------------------------------------------

class TestHumanDecisionMaker:

    def test_returns_action_from_session(self):
        from game.game_actions.data_structs.game_action import PassPriorityAction
        player = StubPlayer()
        dm = HumanDecisionMaker()

        # Simulujeme: hráč zadá "pass" → hotová akce
        with patch("builtins.input", return_value="pass"), \
             patch("builtins.print"):
            action = dm.get_action(StubState(), player)

        assert isinstance(action, PassPriorityAction)

    def test_retries_after_none_session(self):
        """
        Pokud první session vrátí None (hráč šel zpět za COMMAND),
        HumanDecisionMaker to zkusí znovu. Simulujeme: první vstup je prázdný
        (→ None), druhý vstup je „pass" (→ akce).
        """
        from game.game_actions.data_structs.game_action import PassPriorityAction
        player = StubPlayer()
        dm = HumanDecisionMaker()

        # Chceme první session vrátit None a druhou vrátit akci.
        # Nejjednodušší: monkeypatch session.run() na druhém volání.
        call_count = 0
        original_init = ActionBuilderSession.__init__

        sessions = []

        class MockSession:
            def __init__(self_, state, player):
                nonlocal call_count
                call_count += 1
                self_._count = call_count

            def run(self_):
                if self_._count == 1:
                    return None
                from game.game_actions.data_structs.game_action import PassPriorityAction
                return PassPriorityAction(player=player)

        with patch("game.human_input.ActionBuilderSession", MockSession):
            action = dm.get_action(StubState(), player)

        assert isinstance(action, PassPriorityAction)
        assert call_count == 2