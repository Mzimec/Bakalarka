from __future__ import annotations

class OperationExecutor:

    def execute(self, state, operation):
        return operation.execute(state)
