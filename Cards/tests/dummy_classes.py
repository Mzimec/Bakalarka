from game.game_actions import Effect
from game.operations import Operation
from game.target import TargetSpec

class DummyState:
    pass

class DummyPlayer:
    pass

class DummyCard:
    def __init__(self, card_id: str, owner=None):
        self.id = card_id
        self.owner = owner

class DummyEffect(Effect):
    def __init__(self, key: str):
        super().__init__(key, key)
        self.generated = []
        self.executed = []

    def to_operations(self, state, context):
        self.generated.append({
            "state": state,
            "context": context,
        })
        return [DummyOperation(self, context)]


class DummyOperation(Operation):
    def __init__(self, effect, context):
        super().__init__(context)
        self.effect = effect

    def execute(self, state):
        self.effect.executed.append({
            "state": state,
            "context": self.context,
        })
        return []

class StaticTargetSpec(TargetSpec):
    def __init__(self, candidates):
        self.candidates = candidates

    def get_candidates(self, source, state):
        return self.candidates
