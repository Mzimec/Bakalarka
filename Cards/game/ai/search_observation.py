"""Player-facing search input, separate from privileged simulator addresses."""
from dataclasses import dataclass
from functools import cached_property
from hashlib import sha256
import json
from game.ai.structured_values import StructuredJSON, payload, decode


@dataclass(frozen=True, eq=False)
class ActingPlayerObservation:
    """!
    @brief A detached current view, without other players' views or replay metadata.

    The key identifies this observation and available memory, not a history.
    Use SimulationNode.player_information() with track_information=True for recall
    and supported visible-action identities. See docs/AI_SEARCH.md for limits.
    @var player Index of the player owning these facts.
    @var request_kind Native request kind described by this record.
    @var _json Immutable structured facts or compatible canonical JSON text.
    """
    player: int
    request_kind: str
    _json: str | StructuredJSON

    @cached_property
    def structured(self):
        """!
        @brief Expose immutable facts, creating them lazily for compatible text input.
        """
        return payload(self._json)

    def __hash__(self):
        return hash((self.player, self.request_kind, self.structured.value))

    def __eq__(self, other):
        return (isinstance(other, ActingPlayerObservation) and self.player == other.player
                and self.request_kind == other.request_kind and self.structured.value == other.structured.value)

    @classmethod
    def from_node(cls, node):
        """!
        @brief Extract acting-player facts without importing other players replay data.
        """
        if node.decision is None:
            raise ValueError("An acting-player view requires a decision node")
        player = node.decision.player
        view = node.observation(player)
        if view.get("self") != player:
            raise ValueError("Observation does not belong to the acting player")
        return cls(player, node.decision.kind, json.dumps(view, sort_keys=True, separators=(",", ":")))

    def observation(self):
        """!
        @brief Return a detached player-specific view including that player memory.
        """
        return decode(self._json)

    def readonly(self):
        """!
        @brief Internal pure-reader input; observation() remains a detached export.
        """
        return self.structured.value.readonly

    @cached_property
    def key(self):
        # Deliberately exclude full-world observation/menu digests, replay
        # history, physical action descriptors, other players and sample seeds.
        """!
        @brief Return the compatible export identity without private simulator metadata.
        """
        from game.ai.structured_values import text
        payload = json.dumps((self.player, self.request_kind, text(self._json)), separators=(",", ":"))
        return sha256(payload.encode()).hexdigest()
