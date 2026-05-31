from dataclasses import dataclass, field
from typing import List, Any

from game.game_loop.game_loop import (
    GameLoop,
    TurnPhase,
    PrecombatMainPhaseController,
    EndStepPhaseController,
)

from game.human_input import HumanDecisionMaker

# =========================================================
# STACK
# =========================================================

class Stack:

    def __init__(self):
        self.objects = []

    def push(self, obj):
        print(f"[STACK] push -> {obj}")
        self.objects.append(obj)

    def pop(self):
        return self.objects.pop()

    def is_empty(self):
        return len(self.objects) == 0

    def resolve_top(self):
        obj = self.pop()

        print(f"[STACK] resolving -> {obj}")

        obj.resolve()


# =========================================================
# BASIC GAME OBJECTS
# =========================================================

@dataclass
class Player:

    name: str
    health: int
    controller: Any

    hand: list = field(default_factory=list)
    battlefield: list = field(default_factory=list)

    def __repr__(self):
        return f"{self.name} ({self.health} hp)"


@dataclass
class State:

    players: list[Player]
    active_player: Player

    stack: Stack

    turn: Any

    def switch_active_player(self):

        idx = self.players.index(self.active_player)
        idx += 1

        if idx >= len(self.players):
            idx = 0

        self.active_player = self.players[idx]

        print(f"\n=== ACTIVE PLAYER: {self.active_player.name} ===")


# =========================================================
# TURN
# =========================================================

@dataclass
class Turn:
    phase: TurnPhase = TurnPhase.PRECOMBAT_MAIN

    order = [
        TurnPhase.PRECOMBAT_MAIN,
        TurnPhase.END_STEP,
    ]

    def advance_phase(self):

        idx = self.order.index(self.phase)
        idx += 1

        if idx >= len(self.order):
            self.phase = self.order[0]
            return True

        self.phase = self.order[idx]
        return False


# =========================================================
# ACTIONS
# =========================================================

class GameAction:
    pass


@dataclass
class PassPriorityAction(GameAction):
    player: Player


@dataclass
class PlaySpellAction(GameAction):

    player: Player
    card: Any
    target: Any

    def __repr__(self):
        return f"PlaySpell({self.card.name})"

    def resolve(self):

        self.card.resolve(self.player, self.target)


# =========================================================
# CARDS
# =========================================================

class Card:
    name: str

    def get_actions(self, state, player):
        return []


class LightningBolt(Card):

    name = "Lightning Bolt"

    def get_actions(self, state, player):

        actions = []

        for target in state.players:

            actions.append(
                PlaySpellAction(
                    player=player,
                    card=self,
                    target=target
                )
            )

        return actions

    def resolve(self, caster, target):

        target.health -= 3

        print(
            f"{caster.name} deals 3 damage "
            f"to {target.name}!"
        )

        print(f"{target.name} is now at {target.health} hp")


class GrizzlyBears(Card):

    name = "Grizzly Bears"

    def get_actions(self, state, player):

        return [
            PlaySpellAction(
                player=player,
                card=self,
                target=None
            )
        ]

    def resolve(self, caster, target):

        caster.battlefield.append(self)

        print(f"{caster.name} summons Grizzly Bears")


# =========================================================
# ACTION PROCESSOR
# =========================================================

class ActionProcessor:

    def process(self, state, action):

        if isinstance(action, PlaySpellAction):

            print(
                f"{action.player.name} casts "
                f"{action.card.name}"
            )

            state.stack.push(action)


# =========================================================
# PRIORITY WINDOW
# =========================================================

class PriorityWindow:

    def __init__(self, state, processor):

        self.state = state
        self.processor = processor

        self.players = self._order_players(
            state.players,
            state.active_player
        )

        self.current_index = 0

        self.passed_players = set()

    @property
    def current_player(self):
        return self.players[self.current_index]

    def run(self):

        print("\n=== PRIORITY WINDOW OPENED ===")

        while True:

            player = self.current_player

            print(f"\nPriority -> {player.name}")

            action = player.controller.get_action(
                self.state,
                player
            )

            if isinstance(action, PassPriorityAction):

                print(f"{player.name} passes")

                self.passed_players.add(player)

                self._advance_priority()

            else:

                self.processor.process(
                    self.state,
                    action
                )

                self.passed_players.clear()

                self.current_index = 0

            if (
                len(self.passed_players) == len(self.players)
                and
                not self.state.stack.is_empty()
            ):

                self.state.stack.resolve_top()

                self.passed_players.clear()

                self.current_index = 0

            elif (
                len(self.passed_players) == len(self.players)
                and
                self.state.stack.is_empty()
            ):

                print("=== PRIORITY WINDOW CLOSED ===")
                return

    def _advance_priority(self):

        self.current_index += 1

        if self.current_index >= len(self.players):
            self.current_index = 0

    def _order_players(self, players, active):

        i = players.index(active)

        return players[i:] + players[:i]


# =========================================================
# SIMPLE HUMAN INPUT
# =========================================================

class ConsoleDecisionMaker:

    def get_action(self, state, player):

        while True:

            print("\nActions:")

            actions = []

            idx = 0

            for card in player.hand:

                for action in card.get_actions(state, player):

                    actions.append(action)

                    print(f"[{idx}] {action}")

                    idx += 1

            print("[pass] Pass priority")

            raw = input("> ").strip()

            if raw == "pass":
                return PassPriorityAction(player)

            try:

                i = int(raw)

                if 0 <= i < len(actions):
                    return actions[i]

            except:
                pass

            print("Invalid input")


# =========================================================
# GAME LOOP
# =========================================================

def main():

    p1 = Player(
        name="Alice",
        health=20,
        controller=ConsoleDecisionMaker()
    )

    p2 = Player(
        name="Bob",
        health=20,
        controller=ConsoleDecisionMaker()
    )

    p1.hand = [
        LightningBolt(),
        GrizzlyBears(),
    ]

    p2.hand = [
        LightningBolt(),
        GrizzlyBears(),
    ]

    state = State(
        players=[p1, p2],
        active_player=p1,
        stack=Stack(),
        turn=Turn(),
    )

    processor = ActionProcessor()

    while True:

        print(
            f"\n========== {state.turn.phase.name} =========="
        )

        PriorityWindow(
            state,
            processor
        ).run()

        if any(p.health <= 0 for p in state.players):

            loser = next(
                p for p in state.players
                if p.health <= 0
            )

            print(f"{loser.name} loses!")

            break

        new_turn = state.turn.advance_phase()

        if new_turn:
            state.switch_active_player()


if __name__ == "__main__":
    main()