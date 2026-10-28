"""Reviewed contextual identities; never reflect raw event/operation graphs."""
from dataclasses import asdict

from game.ai.search_actions import UnsupportedPlayerAction
from game.ai.observation import PUBLIC_ZONES
from game.enums import ZoneType
from game.game_actions.data_structs.ability import TriggerAbility, TriggeredManaAbility
from game.game_actions.data_structs.game_action import ResolutionContext
from game.game_actions.resolution.event_bus import CardLastKnownInformation, GameEvent
from game.game_actions.resolution.event_definitions import EVENT_DEFINITIONS
from game.game_actions.generation.decision_abstraction.order_frames import OrderNextRequest
from game.game_actions.resolution.replacement_effects import ReplacementEffect
from game.game_actions.generation.decision_abstraction.auxiliary import (
    TriggerOrderRequest, ReplacementRequest, ReplacementOrderRequest, ReplacementAcceptanceRequest,
)
from game.game_actions.generation.decision_abstraction.requests import AbilityDecisionRequest, DeclareAttackersRequest
from game.operations.card_operations import (
    MoveCardOperation, DamagePlayerOperation, DamageCreatureOperation, DrawCardOperation, TapCardOperation,
)
from game.game_actions.advanced_effects import DrawOperation
from game.game_actions.life_effects import GainLifeOperation
from game.operations.player_operations import LosePlayerOperation
from game.rules.permanents import TokenEntryOperation, CeaseTokenOperation


class SearchContextBuilder:
    """!
    @brief Project reviewed resolution, trigger and replacement contexts.
    """
    def __init__(self, builder):
        self.builder, self.request = builder, builder.request

    @property
    def view(self):
        """!
        @brief Expose the current actor-visible facts required by this adapter.
        """
        return self.builder.view

    def known_source(self, card):
        """!
        @brief Encode a source only after verifying its identity is visible.
        """
        if card is None:
            return None
        if not self.view.knows_identity(card):
            raise UnsupportedPlayerAction("Context source identity is not visible")
        return self.builder.encode(card)

    def lki(self, snapshot):
        """!
        @brief Project last-known information only when its captured identity is observable.
        """
        if snapshot is None:
            return None
        if type(snapshot) is not CardLastKnownInformation:
            raise UnsupportedPlayerAction("Unknown last-known information type")
        public = snapshot.zone in PUBLIC_ZONES
        own_hand = snapshot.zone == ZoneType.HAND and snapshot.card.owner is self.request.player
        if snapshot.face_down or not (public or own_hand):
            raise UnsupportedPlayerAction("Last-known identity was hidden at capture")
        return {"card": snapshot.card.command_id, "zone_revision": snapshot.zone_revision,
                "zone": snapshot.zone.name, "name": snapshot.name,
                "controller": self.builder.encode(snapshot.controller),
                **{key: self.builder.encode(getattr(snapshot, key)) for key in
                   ("types", "colors", "subtypes", "keywords", "power", "toughness", "counters")},
                "abilities": [a.key for a in snapshot.abilities],
                "triggers": [a.key for a in snapshot.triggers]}

    def event(self, event):
        """!
        @brief Project reviewed event fields without reading another player private projection.
        """
        if event is None:
            return None
        if type(event) is not GameEvent:
            raise UnsupportedPlayerAction("Unknown trigger event type")
        definition = EVENT_DEFINITIONS.get(event.key)
        if definition is None:
            raise UnsupportedPlayerAction(f"No visible trigger-event adapter for {event.key}")
        if definition.frozen_zone_projection:
            projection = dict(event.player_events or ()).get(self.request.player.idx)
            if projection is None:
                raise UnsupportedPlayerAction("Trigger event needs frozen viewer projection")
            # Only this viewer's projection, never the raw payload/card/LKI or
            # projections belonging to other viewers. No recapture at a later zone.
            return {"kind": event.key, "observed": asdict(projection)}
        fields = definition.public_fields
        if event.controller is self.request.player:
            fields += definition.private_fields
        payload = {key: self.builder.encode(event.payload[key]) for key in fields if key in event.payload}
        if definition.ability_field is not None:
            from game.game_actions.data_structs.ability import AbilityDefinition
            ability = event.payload.get(definition.ability_field)
            if ability is not None and not isinstance(ability, AbilityDefinition):
                raise UnsupportedPlayerAction("Event ability needs an immutable definition")
            payload[definition.ability_field] = ability.key if ability is not None else None
        return {"kind": event.key, "source": self.known_source(event.source),
                "controller": self.builder.encode(event.controller),
                "payload": payload}

    def trigger_parts(self, source, controller, definition, event, revision, snapshot, registration):
        """!
        @brief Describe trigger provenance with explicit incarnation and registration.
        """
        if registration is not None:
            raise UnsupportedPlayerAction("Runtime trigger registration needs its own identity adapter")
        old = self.lki(snapshot)
        source_ref = ({"card": old["card"], "zone_revision": revision, "name": old["name"]}
                      if old is not None else self.known_source(source))
        return {"source": source_ref, "controller": self.builder.encode(controller),
                "definition": definition.key if definition is not None else None,
                "source_revision": revision, "last_known": old, "event": self.event(event)}

    def trigger(self, ability):
        """!
        @brief Encode a supported trigger and its visible source/event evidence.
        """
        if type(ability) not in (TriggerAbility, TriggeredManaAbility):
            raise UnsupportedPlayerAction("Unknown trigger ability type")
        return self.trigger_parts(ability.source, ability.controller, ability.definition, ability.event,
                                  ability.source_revision, ability.source_last_known,
                                  getattr(ability, "registration", None))

    def replacement(self, effect):
        """!
        @brief Encode a supported replacement and its explicit runtime budget.
        """
        if type(effect) is not ReplacementEffect:
            raise UnsupportedPlayerAction("Unknown replacement effect type")
        return {"key": effect.key, "source": self.known_source(effect.source),
                "source_revision": effect.source_revision,
                "priority": effect.definition.priority, "optional": effect.definition.optional,
                "remaining_uses": effect.remaining_uses, "remaining_budget": effect.remaining_budget}

    def resolution(self, context):
        """!
        @brief Encode pending resolution context without executing it.
        """
        if type(context) is not ResolutionContext:
            raise UnsupportedPlayerAction("Unknown operation resolution context")
        if context.trigger_registration is not None:
            raise UnsupportedPlayerAction("Runtime trigger registration needs its own identity adapter")
        old = self.lki(context.source_last_known)
        return {"source": ({"card": old["card"], "zone_revision": old["zone_revision"], "name": old["name"]}
                           if old is not None else self.known_source(context.source)),
                "controller": self.builder.encode(context.controller), "action_key": context.action_key,
                "source_revision": context.source_revision, "last_known": old,
                "ability": context.ability.key if context.ability else None,
                "subability": context.subability.key if context.subability else None,
                "uses_stack": context.uses_stack, "is_cost": context.is_cost,
                "targets": self.builder.encode(context.targets), "parameters": self.builder.encode(context.param_ctx),
                "effects": self.builder.encode(context.effects), "trigger_event": self.event(context.trigger_event)}

    def operation(self, operation):
        """!
        @brief Encode a reviewed operation through its explicit visibility adapter.
        """
        kind = type(operation)
        if kind in (MoveCardOperation, TokenEntryOperation):
            fields = ("card", "destination", "entry_controller", "entry_tapped", "entry_counters", "entry_prepared")
            if kind is TokenEntryOperation:
                fields += ("creation_index",)
            ignored = {"entry_copy", "_entry_bindings", "_entry_view"}
            if operation.entry_copy is not None:
                raise UnsupportedPlayerAction("Copied entry operation needs a visible copy adapter")
        elif kind in (DamagePlayerOperation, DamageCreatureOperation):
            fields = ("target", "amount", "combat", "unpreventable", "lifelink", "deathtouch",
                      "infect", "wither", "damage_controller", "source_revision")
            ignored = {"_defer_lifelink"}
        elif kind in (DrawCardOperation, DrawOperation):
            fields, ignored = (), set()
        elif kind is GainLifeOperation:
            fields, ignored = ("amount",), set()
        elif kind is TapCardOperation:
            fields, ignored = ("card", "tap_symbol"), set()
        elif kind is CeaseTokenOperation:
            fields, ignored = ("card",), set()
        elif kind is LosePlayerOperation:
            fields, ignored = ("player", "reason"), set()
        else:
            raise UnsupportedPlayerAction(f"No visible operation adapter for {kind.__name__}")
        if set(vars(operation)) - set(fields) - ignored - {"context"}:
            raise UnsupportedPlayerAction("Operation has unreviewed instance fields")
        result = {"kind": kind.__name__, "context": self.resolution(operation.context)}
        for name in fields:
            if kind is TokenEntryOperation and name == "card":
                card = operation.card
                if not card.is_token or card._game_state is not None or card.state.face_down:
                    raise UnsupportedPlayerAction("Token entry requires a fresh face-up token")
                # Public creation instruction, not an identity read from a
                # hidden zone. Do not grant visibility to arbitrary runtime cards.
                definition = card.definition
                result[name] = {"pending_token": operation.creation_index,
                    "owner": self.builder.encode(card.owner), "name": definition.name,
                    "types": self.builder.encode(definition.types),
                    "subtypes": self.builder.encode(definition.subtypes),
                    "power": definition.power, "toughness": definition.toughness,
                    "keywords": self.builder.encode(definition.keywords),
                    "colors": self.builder.encode(definition.colors), "legendary": definition.legendary,
                    "enters_tapped": definition.enters_tapped, "loyalty": definition.loyalty,
                    "text": definition.oracle_text,
                    "abilities": [self.view.ability(a) for a in sorted(definition.abilities, key=lambda a: a.key)],
                    "triggers": [self.view.ability(a) for a in sorted(definition.triggers, key=lambda a: a.key)],
                    "continuous_effects": self.view.describe(definition.continuous_effects),
                    "replacement_effects": self.view.describe(definition.replacement_effects)}
            else:
                result[name] = self.builder.encode(getattr(operation, name))
        if kind in (DamagePlayerOperation, DamageCreatureOperation):
            result["defer_lifelink"] = bool(getattr(operation, "_defer_lifelink", False))
        return result

    def request_context(self):
        """!
        @brief Project the explicit context needed to bind this request kind.
        """
        request = self.request
        from game.game_actions.generation.decision_abstraction.combat_frames import CombatNextRequest
        if type(request) is CombatNextRequest:
            frame = request.frame
            context = {'combat_version': 1, 'combat_kind': frame.kind,
                    'creatures': self.builder.encode(frame.creatures),
                    'targets': self.builder.encode(frame.targets),
                    'prefix': self.builder.encode(frame.prefix),
                    'creature': self.builder.encode(frame.creatures[len(frame.prefix)]),
                    'remaining_count': frame.remaining_count}
            if frame.kind == 'attackers':
                from game.ai.search_actions import PlayerActionBuilder
                builder = PlayerActionBuilder(DeclareAttackersRequest(request.state, request.player))
                builder.__dict__['view'] = self.view
                context.update(builder.context.request_context())
            return context
        from game.game_actions.generation.decision_abstraction.ability_frames import AbilityConstructionRequest, context_key
        if type(request) is AbilityConstructionRequest:
            frame = request._frame
            return {"construction_version": 2, "stage": frame.stage,
                    'cost_mode_prefix': self.builder.encode(frame.cost_mode_prefix),
                    'effect_mode_prefix': self.builder.encode(frame.effect_mode_prefix),
                    "source": self.builder.encode(frame.ability.source),
                    "parameters": self.builder.encode(context_key(frame.context)) if frame.context is not None else None,
                    "cost_targets": self.builder.encode(frame.cost_binding),
                    "effect_targets": self.builder.encode(frame.effect_binding),
                    "group_prefix": self.builder.encode((frame.group_prefix[0].slot.key,
                        frame.group_prefix[0].runtime_key, frame.group_prefix[1])) if frame.group_prefix else None,
                    "cost_mode": self.builder.encode(frame.cost_template.effects) if frame.cost_template else None,
                    "effect_mode": self.builder.encode(frame.effect_template.effects) if frame.effect_template else None}
        if type(request) is OrderNextRequest:
            frame = request.frame
            encode = self.trigger if frame.kind == "TriggerOrderRequest" else self.operation
            return {"version": frame.version, "order_kind": frame.kind,
                    "candidates": [encode(item) for item in frame.candidates],
                    "prefix": list(frame.prefix), "remaining_count": len(frame.remaining)}
        if isinstance(request, DeclareAttackersRequest):
            # Shared rules own blocking eligibility. The policy estimates trades
            # from this public pair relation, without reimplementing flying etc.
            state = request.state
            if any(c.get_zone() == ZoneType.BATTLEFIELD and not self.view.knows_identity(c)
                   for c in self.view.public):
                # The current observation has no face-down characteristic
                # adapter. Do not disclose printed keywords through pair tests.
                return {"possible_blocks": None}
            attackers = tuple(state.combat.legal_attackers(request.player))
            return {"possible_blocks": [
                {"attacker": self.builder.encode(a), "blocker": self.builder.encode(b)}
                for player in request.participants if player is not request.player
                for b in state.combat.potential_blockers(player) for a in attackers
                if state.combat.blocker_error(b, a, player, check_assignment=False) is None]}
        if isinstance(request, TriggerOrderRequest):
            return {"triggers": [self.trigger(a) for a in request.candidates]}
        if isinstance(request, AbilityDecisionRequest) and isinstance(request.ability, TriggerAbility):
            return {"trigger": self.trigger(request.ability)}
        if isinstance(request, ReplacementOrderRequest):
            return {"operations": [self.operation(op) for op in request.candidates]}
        if isinstance(request, ReplacementRequest):
            return {"operation": self.operation(request.operation),
                    "effects": [self.replacement(e) for e in request.candidates]}
        if isinstance(request, ReplacementAcceptanceRequest):
            return {"operation": self.operation(request.operation), "effect": self.replacement(request.effect)}
        return None
