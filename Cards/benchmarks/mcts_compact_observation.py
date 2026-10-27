"""Previous deep-decoding readers for an equal-work compact-view ablation.

No sources or captured worlds are written. All patches are scoped and restored.
The engine projections, frozen facts, budgets and keys stay identical.
"""
from contextlib import contextmanager, ExitStack
from unittest.mock import patch


@contextmanager
def compact_reference():
    from game.ai.search_actions import PlayerAction
    from game.ai.search_observation import ActingPlayerObservation
    from game.ai.search_recall import RecallEntry
    from game.ai.search_simulator import SimulationNode
    from game.ai.observation import PlayerObservationBuilder
    initialize = PlayerObservationBuilder.__init__

    def legacy_builder(self, *args, **kwargs):
        kwargs['projection_store'] = None
        initialize(self, *args, **kwargs)
    with ExitStack() as patches:
        patches.enter_context(patch.object(PlayerObservationBuilder, '__init__', legacy_builder))
        for cls, old in ((PlayerAction, 'describe'), (RecallEntry, 'describe'),
                         (ActingPlayerObservation, 'observation'),
                         (SimulationNode, 'observation')):
            patches.enter_context(patch.object(cls,
                'observation_readonly' if cls is SimulationNode else 'readonly', getattr(cls, old)))
        yield
