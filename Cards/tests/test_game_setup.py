"""Full pregame setup, ownership, London rounds, library order and first turn."""
from game.ai.decision_maker import DecisionResult, MulliganBottomOption, MulliganBottomRequest, MulliganOption, MulliganRequest, StartingPlayerOption
from pathlib import Path
import pytest

from game.cards.decks import DeckList
from game.game_loop.setup import create_game, SetupConfig, bottom_cards
from game.game_state import CardDefinition
from game.enums import ZoneType, TurnPhase
from game.rules.lands import basic_land
from game.game_loop.minimal_game import ScriptedController
from game.game_loop.game_loop import GameLoop
from game.game_actions.resolution.action_processor import ActionProcessor
from game.game_actions.resolution.resolution_engine import ResolutionEngine
from game.game_actions.resolution.event_bus import EventBus
from game.game_actions.resolution.operation_executor import OperationExecutor


class Controller(ScriptedController):
    def __init__(self, mulligans=0, log=None):
        super().__init__()
        self.mulligans = mulligans
        self.log = log if log is not None else []
        self.bottoms = []

    def decide_mulligan(self, request):
        state = request.state; player = request.player; count = request.mulligans_taken
        self.log.append((player.idx, count, len(player.hand), tuple(len(p.hand) for p in state.players)))
        return DecisionResult(MulliganOption(count < self.mulligans))

    def decide_mulligan_bottom(self, request):
        state = request.state; player = request.player; count = request.count
        selected = tuple(player.hand.values())[:count]
        self.bottoms.append(selected)
        return DecisionResult(MulliganBottomOption(selected))


def make_game(controllers=None, **kwargs):
    deck = DeckList("Lands", (("Forest", 60),))
    return create_game((deck, deck), controllers or (Controller(), Controller()), {"Forest": basic_land("Forest")}, **kwargs)


def test_prepared_game_owns_distinct_cards_has_no_stack_and_real_opening_hands():
    state = make_game(seed=41, starting_player_idx=1)
    assert state.active_player is state.players[1]
    assert state.priority.current_player is state.active_player
    assert state.turn.phase == TurnPhase.UNTAP and state.turn.number == 1
    assert state.stack.is_empty() and state.mulligans_taken == (0, 0)
    assert len({card.key for card in state.get_cards()}) == 120
    for player in state.players:
        assert player.health == 20 and len(player.hand) == 7 and len(player.deck) == 53
        assert all(card.owner is player and card._game_state is state for card in player.get_cards())
        assert all(card.get_zone() == ZoneType.HAND for card in player.hand.values())


def test_seed_reproduces_order_and_does_not_share_runtime_cards():
    a, b, c = (make_game(seed=seed) for seed in (7, 7, 8))
    order = lambda state: tuple((tuple(p.hand), tuple(p.deck)) for p in state.players)
    assert order(a) == order(b) and order(a) != order(c)
    assert a.active_player_idx == b.active_player_idx
    assert next(iter(a.players[0].hand.values())) is not next(iter(b.players[0].hand.values()))


def test_london_mulligan_bottoms_every_round_and_keep_is_final():
    log = []
    controllers = (Controller(2, log), Controller(0, log))
    state = make_game(controllers, seed=1, starting_player_idx=0)
    assert [(p, n, hand) for p, n, hand, _ in log] == [(0, 0, 7), (1, 0, 7), (0, 1, 6), (0, 2, 5)]
    assert state.mulligans_taken == (2, 0)
    assert len(state.players[0].deck) == 55
    library = tuple(reversed(tuple(state.players[0].deck.values())))
    assert library[-2:] == controllers[0].bottoms[-1]


def test_all_players_declare_before_any_redraw_and_starter_declares_first():
    log = []
    state = make_game((Controller(1, log), Controller(1, log)), starting_player_idx=1)
    assert [row[0] for row in log] == [1, 0, 1, 0]
    assert [row[3] for row in log] == [(7, 7), (7, 7), (6, 6), (6, 6)]
    assert state.mulligans_taken == (1, 1)


def test_forced_keep_at_zero_and_all_cards_return_to_library():
    state = make_game((Controller(99), Controller()), seed=3)
    assert not state.players[0].hand and len(state.players[0].deck) == 60
    assert state.mulligans_taken[0] == 7


def test_invalid_bottom_choice_does_not_move_any_selected_card():
    state = make_game()
    player = state.players[0]
    card = next(iter(player.hand.values()))
    before = tuple(player.hand), tuple(player.deck), card.zone_revision
    with pytest.raises(ValueError):
        bottom_cards(player, (card, card))
    assert (tuple(player.hand), tuple(player.deck), card.zone_revision) == before


def test_start_choice_precedes_any_card_creation():
    class ChooseSecond(Controller):
        def decide_starting_player(self, request):
            players = request.candidates
            assert all(not p.get_cards() and p.game_state is None for p in players)
            return DecisionResult(StartingPlayerOption(players[1]))
    assert make_game((ChooseSecond(), ChooseSecond()), seed=4).active_player_idx == 1


def test_setup_then_full_game_loop_skips_only_first_players_first_draw():
    state = make_game(starting_player_idx=0, seed=3)
    bus = EventBus()
    loop = GameLoop(None, ActionProcessor(ResolutionEngine(OperationExecutor(), bus)))
    for _ in range(40):
        loop.step(state)
        if any(e.key == "card_drawn" for e in bus.emitted_events):
            break
    draws = [e for e in bus.emitted_events if e.key == "card_drawn"]
    assert len(draws) == 1 and draws[0].controller is state.players[1]
    assert len(state.players[0].hand) == 7 and len(state.players[1].hand) == 8


def test_arena_import_preserves_names_and_combines_printings():
    deck = DeckList.from_arena("Deck\n24 Forest (ANB) 112\n2 Forest (M21) 313\n4 Serra Angel (ANB) 18\n")
    assert deck.cards == (("Forest", 26), ("Serra Angel", 4)) and deck.size == 30


@pytest.mark.parametrize("text", ["Sideboard\n1 Forest", "0 Forest", "-1 Forest", "Commander", "oops"])
def test_invalid_deck_exports_fail_explicitly(text):
    with pytest.raises(ValueError):
        DeckList.from_arena(text)


def test_missing_definitions_and_copy_limit_fail_before_setup():
    with pytest.raises(ValueError, match="missing card definitions: Missing"):
        DeckList("Unknown", (("Missing", 60),)).resolve({})
    with pytest.raises(ValueError, match="too many copies"):
        DeckList("Invalid", (("Forest", 55), ("Bear", 5))).resolve({"Forest": basic_land("Forest"), "Bear": CardDefinition("Bear")})


@pytest.mark.parametrize("kwargs", [{"opening_hand_size": -1}, {"minimum_deck_size": 3}, {"maximum_copies": 0}, {"starting_life": True}])
def test_invalid_config(kwargs):
    with pytest.raises(ValueError):
        SetupConfig(**kwargs)


def test_all_five_reference_manifests_have_sixty_cards_and_do_not_invent_rules():
    files = sorted((Path(__file__).parents[1] / "data" / "decks" / "arena_anb").glob("*.txt"))
    assert len(files) == 5
    for path in files:
        deck = DeckList.from_arena(path.read_text(), path.stem)
        assert deck.size == 60
        with pytest.raises(ValueError, match="missing card definitions"):
            deck.resolve({name: basic_land(name) for name in ("Plains", "Island", "Swamp", "Mountain", "Forest")})


def test_console_setup_hooks_retry_invalid_selections():
    from game.console.demo_game import ConsoleDecisionMaker
    answers = iter(("bad", "y", "n", "missing", "{card}"))
    state = make_game()
    player = state.players[0]
    card = next(iter(player.hand.values()))
    controller = ConsoleDecisionMaker(read=lambda _: next(answers).replace("{card}", card.command_id), write=lambda _: None)
    assert controller.decide(MulliganRequest(state, player, 0)).value.take_mulligan
    assert not controller.decide(MulliganRequest(state, player, 1)).value.take_mulligan
    assert controller.decide(MulliganBottomRequest(state, player, 1)).value.cards == (card,)
