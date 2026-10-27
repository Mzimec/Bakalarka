"""Native player state operations, shared by rules and visible search adapters."""
from game.game_actions.data_structs.operation import Operation


class LosePlayerOperation(Operation):
    def __init__(self, player, reason):
        from game.game_actions.data_structs.game_action import ResolutionContext
        super().__init__(ResolutionContext(controller=player))
        self.player, self.reason = player, reason

    def lki_cards(self, state):
        return () if type(self) is LosePlayerOperation and set(vars(self)) == {"context", "player", "reason"} else None

    def execute(self, state):
        from game.game_actions.resolution.event_bus import GameEvent
        self.player.has_lost = True
        self.player.loss_reason = self.reason
        if hasattr(state, "player_register"):
            state.player_register.mark_changed(self.player)
        return [GameEvent("player_lost", None, self.player, {"reason": self.reason})]
