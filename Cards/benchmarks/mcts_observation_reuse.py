"""Previous event projection for an equal-work observation ablation in RAM."""
from contextlib import contextmanager
from dataclasses import asdict
from unittest.mock import patch


@contextmanager
def observation_reference():
    import game.ai.knowledge as knowledge
    import game.ai.observation as observation
    with patch.object(knowledge, 'event_view', asdict), patch.object(
            observation, 'native_characteristics', lambda card, state, reader: reader()):
        yield
