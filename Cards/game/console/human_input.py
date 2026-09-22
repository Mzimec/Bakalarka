"""Public human-controller adapters using the maintained console and command builder.

ActionBuilderSession(state, player).run() and HumanDecisionMaker remain entry
points. Removed private step classes belonged to the abandoned prototype; use
CommandSession for incremental selections, cancellation, and backtracking.
"""

from game.console.demo_game import ConsoleDecisionMaker


def _resolve_command(raw):
    aliases = {"p": "play", "a": "activate", "": "pass", "b": "block"}
    name = raw.strip().lower()
    name = aliases.get(name, name)
    return (
        name if name in {"play", "activate", "pass", "attack", "block", "concede", "quit"} else None
    )


class ActionBuilderSession:
    def __init__(self, state, player, read=None, write=None):
        self.state, self.player = state, player
        self.console = ConsoleDecisionMaker(read=read, write=write)

    def run(self):
        from game.ai.decision_maker import PriorityDecisionRequest
        return self.console.decide(PriorityDecisionRequest(self.state, self.player)).value


class HumanDecisionMaker(ConsoleDecisionMaker):
    """!
    @brief Human decisions share casting, mana, combat and trigger handling with the CLI.
    """
