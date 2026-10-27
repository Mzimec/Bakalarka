"""Untruncated per-player recall within a supported simulation's lifetime."""
from dataclasses import dataclass
from functools import cached_property
from hashlib import sha256
import json

from game.ai.search_actions import PlayerAction, canonical_json
from game.ai.search_observation import ActingPlayerObservation
from game.ai.structured_values import StructuredJSON, RecallDigest, payload, decode, structural_key


@dataclass(frozen=True, eq=False)
class RecallEntry:
    """!
    @brief One immutable player observation, action or projected event in recall order.
    @var kind Explicit record/action kind used by this protocol.
    @var _json Immutable structured facts or compatible canonical JSON text.
    """
    kind: str
    _json: str | StructuredJSON

    def describe(self):
        """!
        @brief Return a detached mutable export while preserving immutable internal facts.
        """
        return decode(self._json)

    def readonly(self):
        """!
        @brief Read immutable structured facts without deeply allocating a mutable export.
        """
        return self.structured.value.readonly

    @cached_property
    def structured(self):
        """!
        @brief Expose immutable facts, creating them lazily for compatible text input.
        """
        return payload(self._json)

    def __hash__(self):
        return hash((self.kind, self.structured.value))

    def __eq__(self, other):
        return isinstance(other, RecallEntry) and self.kind == other.kind and self.structured.value == other.structured.value


@dataclass(frozen=True)
class PlayerRecall:
    """!
    @brief Immutable ordered recall with an incrementally chained identity digest.
    @var entries Ordered immutable recall facts, or configured entry capacity for a settings record.
    @var digest Typed structural digest of the immutable facts.
    """
    entries: tuple[RecallEntry, ...] = ()
    digest: str | RecallDigest = ""

    def append(self, kind, payload, *, store=None):
        """!
        @brief Append an immutable recall fact and update the chained information identity.
        """
        entry = RecallEntry(kind, StructuredJSON(payload, store))
        digest = RecallDigest(self.digest, kind, entry._json)
        return PlayerRecall((*self.entries, entry), digest)


@dataclass(frozen=True)
class PlayerInformation:
    """!
    @brief Safe policy input: one actor's view, recall and offered visible actions.

    Recall covers projected engine events and this player's decision observations
    and actions since the scenario began. It cannot invent unimplemented reveal
    events or history before scenario construction. Keys are scoped to a compatible
    scenario recipe/catalog with stable visible card IDs, not arbitrary saved games.
    @var current Current actor observation, excluding other private views.
    @var recall Immutable observations, actions and projected events for this actor.
    @var actions Complete visible actions admitted at the current width.
    @var candidate_model Candidate-generation contract scoping compatible information keys.
    @var menu_complete Whether native enumeration exhausted the legal menu.
    @var candidate_width Number of candidates currently admitted by the frontier.
    """
    current: ActingPlayerObservation
    recall: PlayerRecall
    actions: tuple[PlayerAction, ...]
    candidate_model: str | None = None
    menu_complete: bool = True
    candidate_width: int = 0

    @cached_property
    def fast_key(self):
        """!
        @brief Internal v3 identity; public v2 JSON keys remain an export contract.
        """
        digest = self.recall.digest
        recall_key = digest.fast if isinstance(digest, RecallDigest) else digest
        menu = (('frontier', self.candidate_model) if self.candidate_model is not None else
                sorted((a.kind, a.structured.value.digest.hex()) for a in self.actions))
        return structural_key(3, self.current.player, self.current.request_kind,
            self.current.structured, recall_key, menu)

    @cached_property
    def knowledge_key(self):
        """!
        @brief Version 2: player facts and recall, independent of candidate reduction.
        """
        return sha256(canonical_json((2, self.current.key, self.recall.digest)).encode()).hexdigest()

    @cached_property
    def menu_digest(self):
        """!
        @brief Digest of the concrete offered candidates, not a claim of legality completeness.
        """
        return sha256(canonical_json(sorted(a.key for a in self.actions)).encode()).hexdigest()

    @cached_property
    def key(self):
        # Tree/replay binding still requires the same candidate space. Do not
        # merge incompatible menus merely because knowledge agrees.
        """!
        @brief Return the compatible export identity without private simulator metadata.
        """
        identity = ((2, self.knowledge_key, self.menu_digest) if self.candidate_model is None else
                    (3, self.knowledge_key, self.menu_digest, self.candidate_model, self.menu_complete, self.candidate_width))
        return sha256(canonical_json(identity).encode()).hexdigest()
