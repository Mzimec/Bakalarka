"""Player-visible action identities for compatible sampled scenario recipes.

These are not raw replay descriptors. Unsupported/hidden inputs fail closed;
binding always selects a freshly generated legal option in the destination.
"""
from collections.abc import Mapping
from dataclasses import dataclass, fields
from enum import Enum
from functools import cached_property
from hashlib import sha256
import json
from game.ai.structured_values import StructuredJSON, RecallDigest, payload, decode, text

from game.ai.observation import PlayerObservationBuilder
from game.ai.visible_effects import effect_features
from game.game_state import Card, Player
from game.game_actions.data_structs.game_action import (
    AbilityAction, ManaAbilityAction, AbilityExecutionPlan, PassPriorityAction, ConcedeAction,
)
from game.game_actions.generation.decision_abstraction.options import (
    DeclareAttackersOption, DeclareBlockersOption, MulliganOption, MulliganBottomOption,
    DiscardOption, AbilityResolutionOption,
)
from game.game_actions.generation.decision_abstraction.auxiliary import (
    StartingPlayerOption, LegendOption, OptionalEffectOption, ReplacementAcceptanceOption,
    ScryOption, ScryRequest, CombatDamageOption,
    TriggerOrderOption, ReplacementOption, ReplacementOrderOption,
)
from game.mana.mana_solver import ManaSolverResult
from game.abilities.parameter_context import ParameterContext
from game.rules.lands import LandPlayAction
from game.game_actions.data_structs.ability import EffectSequence, TriggerAbility, TriggeredManaAbility
from game.game_actions.data_structs.operation import Operation
from game.game_actions.resolution.replacement_effects import ReplacementEffect
from game.game_actions.generation.decision_abstraction.order_frames import OrderNextOption
from game.game_actions.generation.decision_abstraction.priority_frames import PrioritySourceOption
from game.game_actions.generation.decision_abstraction.ability_frames import AbilityConstructionOption


def canonical_json(value):
    """!
    @brief Serialize the compatible player-facing JSON export deterministically.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False,
                      default=lambda item: item.text if isinstance(item, (StructuredJSON, RecallDigest)) else _unsupported_json(item))


def _unsupported_json(value):
    raise TypeError(f'Not JSON data: {type(value).__name__}')


class UnsupportedPlayerAction(TypeError):
    """!
    @brief A choice needs a reviewed visibility-aware adapter before search sharing.
    """


@dataclass(frozen=True, eq=False)
class PlayerAction:
    """!
    @brief Immutable visible action identity, separate from native replay bindings.
    @var kind Explicit record/action kind used by this protocol.
    @var _json Immutable structured facts or compatible canonical JSON text.
    """
    kind: str
    _json: str | StructuredJSON

    @cached_property
    def structured(self):
        """!
        @brief Expose immutable facts, creating them lazily for compatible text input.
        """
        return payload(self._json)

    @property
    def sort_key(self):
        """!
        @brief Return the canonical complete-action tie-break independent of menu order.
        """
        return self.kind, self.structured.value.order

    def __hash__(self):
        return hash((self.kind, self.structured.value))

    def __eq__(self, other):
        return (isinstance(other, PlayerAction) and self.kind == other.kind
                and self.structured.value == other.structured.value)

    @cached_property
    def key(self):
        """!
        @brief Return the compatible export identity without private simulator metadata.
        """
        return sha256(canonical_json((self.kind, self._json)).encode()).hexdigest()

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


from game.game_actions.generation.decision_abstraction.combat_frames import CombatNextOption

_SIMPLE_OPTIONS = (
    CombatNextOption,
    OrderNextOption,
    DeclareAttackersOption, DeclareBlockersOption, MulliganOption, MulliganBottomOption,
    DiscardOption, AbilityResolutionOption, StartingPlayerOption, LegendOption,
    OptionalEffectOption, ReplacementAcceptanceOption, ScryOption, CombatDamageOption,
    PassPriorityAction, ConcedeAction,
    TriggerOrderOption, ReplacementOption, ReplacementOrderOption,
)


class PlayerActionBuilder:
    """!
    @brief Encode reviewed native options through an explicit visibility boundary.
    """
    def __init__(self, request, *, store=None, structured=True):
        self.request = request
        self.store, self.structured = store, structured
        from game.ai.search_context import SearchContextBuilder
        self.context = SearchContextBuilder(self)

    @cached_property
    def view(self):
        # Pass/keep/empty-combat choices refer only to a player or primitives.
        # Do not scan cards/refresh effects unless an adapter needs visibility.
        """!
        @brief Expose the current actor-visible facts required by this adapter.
        """
        view = PlayerObservationBuilder(self.request.state, self.request.player) if self.request.state is not None else None
        if isinstance(self.request, ScryRequest) and view is not None:
            view.visible.update(self.request.candidates)
        return view

    def action(self, option):
        """!
        @brief Encode one reviewed native option as a complete visible action.
        """
        data = self.encode(option)
        return PlayerAction(type(option).__name__, StructuredJSON(data, self.store) if self.structured else canonical_json(data))

    def encode(self, value):
        """!
        @brief Convert supported visible state or action data into detached features.
        """
        if value is None or type(value) in (str, int, bool, float):
            return value
        if isinstance(value, Enum):
            return [type(value).__module__, type(value).__qualname__, value.name]
        if isinstance(value, Player):
            if value not in self.request.participants:
                raise UnsupportedPlayerAction("Player outside this decision")
            return {"player": value.idx}
        if isinstance(value, Card):
            if self.view is None or value not in self.view.visible:
                raise UnsupportedPlayerAction("Action refers to an unobservable card")
            return {**self.view.reference(value),
                    "name": value.name if self.view.knows_identity(value) else None}
        if type(value) is LandPlayAction:
            if self.view is None or not self.view.knows_identity(value.card):
                raise UnsupportedPlayerAction("Land identity is hidden")
            return {"kind": type(value).__name__, "card": self.encode(value.card),
                    "player": self.encode(value.player), "source_revision": value.source_revision,
                    "permission": value.permission.key if value.permission is not None else None}
        if type(value) is PrioritySourceOption:
            return {"kind": "PrioritySourceOption", "source": self.encode(value.ability.source),
                    "ability_key": value.ability.definition.key,
                    "witness": self.encode(value.witness),
                    **({"construction_version": 2} if value._construction is not None else {})}
        if type(value) is AbilityConstructionOption:
            return {"kind": "AbilityConstructionOption", "stage": value.stage,
                    "choice": self.encode(value.choice), "witness": self.encode(value.witness)}
        if type(value) in (AbilityAction, ManaAbilityAction):
            triggered = value.trigger_source_revision is not None or value.trigger_event is not None
            if not triggered and (self.view is None or not self.view.knows_identity(value.source)):
                raise UnsupportedPlayerAction("Ability source identity is hidden")
            trigger = self.context.trigger_parts(value.source, value.controller, value.ability, value.trigger_event,
                       value.trigger_source_revision, value.trigger_source_last_known, value.trigger_registration) if triggered else None
            if not triggered and (value.trigger_registration is not None or value.trigger_source_last_known is not None):
                raise UnsupportedPlayerAction("Incomplete trigger context")
            result = {"kind": type(value).__name__, "source": trigger["source"] if triggered else self.encode(value.source),
                    "action_key": value.action_key, "controller": self.encode(value.controller),
                    "uses_stack": value.uses_stack,
                    "source_revision": value.trigger_source_revision if triggered else value.source_revision,
                    "ability_key": value.ability.key if value.ability is not None else None,
                    "cost": self.encode(value.cost_generator), "effect": self.encode(value.action_generator)}
            if triggered:
                result["trigger"] = trigger
            return result
        if type(value) is AbilityExecutionPlan:
            # Authored rule keys are meaningful within the fixed trusted catalog.
            # Do not reflect effect instances, callbacks or arbitrary operation data.
            return {"effects": self.encode(value.effects),
                    "targets": self.encode(value.binding), "parameters": self.encode(value.param_context),
                    "mana": self.encode(value.mana_solver_result)}
        if type(value) is EffectSequence:
            return [{"rule": [type(b.effect).__module__, type(b.effect).__qualname__, b.effect.key],
                                 "features": effect_features(b.effect),
                                 "slots": sorted([self.encode((s.slot.key, s.runtime_key)) for s in b.slots],
                                                 key=canonical_json)} for b in value.sequence]
        if type(value) in (TriggerAbility, TriggeredManaAbility):
            return self.context.trigger(value)
        if type(value) is ReplacementEffect:
            return self.context.replacement(value)
        if isinstance(value, Operation):
            return self.context.operation(value)
        if type(value) is ManaSolverResult:
            # future_state is a runtime binding, not player information.
            return {"mana_plan": self.encode(value.mana_plan), "payment": self.encode(value.payment),
                    "life_payment": value.life_payment}
        if type(value) is ParameterContext:
            return {"x_variables": self.encode(value.x_variables)}
        if type(value) in _SIMPLE_OPTIONS:
            return {"kind": type(value).__name__,
                    **{f.name: self.encode(getattr(value, f.name)) for f in fields(value)}}
        if isinstance(value, Mapping):
            return {"mapping": sorted([[self.encode(k), self.encode(v)] for k, v in value.items()],
                                      key=canonical_json)}
        if type(value) in (tuple, list):
            return [self.encode(v) for v in value]
        if type(value) in (set, frozenset):
            return {"set": sorted([self.encode(v) for v in value], key=canonical_json)}
        raise UnsupportedPlayerAction(f"No player-action adapter for {type(value).__name__}")
