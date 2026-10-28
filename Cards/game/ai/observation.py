"""Player-visible snapshots, independent of candidate ranking and selectors.

Never serialize State/Card.__dict__: runtime graphs contain hidden cards and
library order. References and rule parameters pass through one visibility gate.
"""
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
import json

from game.enums import ZoneType
from game.game_state import Card, Player
from game.mana.mana_value import ManaValueBase, format_mana_cost
from game.game_actions.data_structs.effect import Effect
from game.stat_type import STAT_COLORS, STAT_KEYWORDS, STAT_STATIC_ABILITIES
from game.ai.visible_effects import effect_features, restriction_keywords
from game.ai.observation_characteristics import native_characteristics
from game.ai.observation_projection import VisibilityRead, DescriptionProjection, ProjectedCardRow


PUBLIC_ZONES = (ZoneType.BATTLEFIELD, ZoneType.GRAVEYARD, ZoneType.EXILE, ZoneType.STACK)


class PlayerObservationBuilder:
    """!
    @brief Project native state into one player-visible snapshot.
    """
    schema_version = 2

    def __init__(self, state, player, *, shared_characteristics=None, projection_store=None):
        if player not in state.players:
            raise ValueError("The observing player must belong to this state.")
        self.state, self.player = state, player
        state.refresh_continuous_effects()
        self.public = tuple(state.get_cards(from_zones=PUBLIC_ZONES))
        self.visible = set(self.public) | set(player.hand.values())
        self._ability_descriptions = None
        self._descriptions = None
        # Private synchronous cache, scoped to one multi-player projection.
        # Raw characteristics plus frozen rows with explicit visibility proofs.
        # This cache must not survive mutation or resumption of the engine.
        self._shared_characteristics = shared_characteristics
        self._projection_store = projection_store
        self._row_reads = None

    def _gate(self, kind, value, result):
        if self._row_reads is not None:
            self._row_reads.append(VisibilityRead(kind, value, result))
        return result

    def is_visible(self, obj):
        """!
        @brief Record viewer-dependent membership for safe row sharing.
        """
        return self._gate('is_visible', obj, obj in self.visible)

    def reference(self, obj):
        """!
        @brief Resolve a visible native reference into a portable player-facing identity.
        """
        if isinstance(obj, Player):
            return {"player": obj.idx}
        if isinstance(obj, Card):
            result = {"card": obj.command_id, "zone_revision": obj.zone_revision} if obj in self.visible else None
            return self._gate('reference', obj, result)
        return None

    def knows_identity(self, card):
        # There is not yet an engine visibility grant for inspecting face-down
        # cards. Conservatively expose their public shell, never the definition.
        """!
        @brief Record whether this viewer may inspect a card identity.
        """
        return self._gate('knows_identity', card, card in self.visible and not card.state.face_down)

    def describe(self, value, depth=0):
        """!
        @brief Copy declarative rule data; opaque code is explicitly not explained.
        """
        if value is None or type(value) in (str, int, float, bool):
            return value
        if isinstance(value, Enum):
            return value.name
        cache = self._descriptions
        if cache is None:
            return self._describe(value, depth)
        # Depth is part of the key because nested rule data has a deliberate
        # truncation limit. Keep values alive so temporary ids cannot be reused.
        key = (id(value), depth)
        if (entry := cache.get(key)) is not None:
            if all(read.matches(self) for read in entry.reads):
                return entry.value
        previous = self._row_reads
        if previous is None:
            self._row_reads = []
        start = len(self._row_reads)
        try:
            result = self._describe(value, depth)
            reads = tuple(self._row_reads[start:])
        finally:
            self._row_reads = previous
        cache[key] = DescriptionProjection(value, result, reads)
        return result

    def _describe(self, value, depth):
        from game.game_actions.resolution.event_bus import GameEvent, CardLastKnownInformation
        if isinstance(value, CardLastKnownInformation) and not self.knows_last_known(value):
            return {"kind": "CardLastKnownInformation", "identity_visible": False}
        if isinstance(value, GameEvent):
            # Raw payloads and per-player projections can contain other players'
            # private draw identities; never reflect the event dataclass here.
            return {"kind": "GameEvent", "key": value.key,
                    "source": self.reference(value.source), "controller": self.reference(value.controller),
                    "payload": {"description_unavailable": True}}
        if isinstance(value, (Card, Player)):
            return self.reference(value)
        if isinstance(value, ManaValueBase):
            return format_mana_cost(value)
        if isinstance(value, Effect):
            text = value.get_info()
            return {"kind": type(value).__name__, "key": value.key, "text": text,
                    "features": effect_features(value),
                    "description_unavailable": not bool(text)}
        if depth >= 10:
            return {"kind": type(value).__name__, "description_unavailable": True}
        if isinstance(value, Mapping):
            if any(isinstance(k, (Card, Player)) for k in value):
                return [{"object": self.describe(k, depth + 1), "value": self.describe(v, depth + 1)}
                        for k, v in value.items()]
            return {str(getattr(k, "name", k)) if isinstance(k, (str, int, Enum)) or hasattr(k, "name")
                    else type(k).__name__: self.describe(v, depth + 1) for k, v in value.items()}
        if isinstance(value, (tuple, list, set, frozenset)):
            result = [self.describe(v, depth + 1) for v in value]
            return sorted(result, key=lambda item: json.dumps(item, sort_keys=True)) if isinstance(value, (set, frozenset)) else result
        if is_dataclass(value) and not isinstance(value, type):
            return {"kind": type(value).__name__, **{
                f.name: self.describe(getattr(value, f.name), depth + 1)
                for f in fields(value) if not f.name.startswith("_")
            }}
        return {"kind": type(value).__name__, "description_unavailable": True}

    def knows_last_known(self, snapshot):
        """!
        @brief Record whether a captured identity is observable in its historical zone.
        """
        result = (not snapshot.face_down and (snapshot.zone in PUBLIC_ZONES
                or (snapshot.zone == ZoneType.HAND and snapshot.card.owner is self.player)))
        return self._gate('knows_last_known', snapshot, result)

    def ability(self, definition):
        # Definitions are shared by copies of a card. Reuse only during one
        # synchronous joint projection; never across state changes. Recheck
        # visibility dependencies for each observer. Hold objects as well as IDs.
        """!
        @brief Describe or score a complete visible ability and explicit parameters.
        """
        cache = self._ability_descriptions
        if cache is not None and (entry := cache.get(id(definition))) is not None:
            if all(read.matches(self) for read in entry.reads):
                return entry.value
        previous = self._row_reads
        if previous is None:
            self._row_reads = []
        start = len(self._row_reads)
        try:
            result = self._ability(definition)
            reads = tuple(self._row_reads[start:])
        finally:
            self._row_reads = previous
        if cache is not None:
            cache[id(definition)] = DescriptionProjection(definition, result, reads)
        return result

    def _ability(self, definition):
        """! @brief Read a rule description inside its transitive visibility proof. """
        result = {"key": definition.key, "kind": type(definition).__name__,
                "uses_stack": definition.uses_stack, "mana_ability": definition.is_mana_ability,
                "sorcery_speed": definition.sorcery_speed,
                "allowed_zones": sorted(z.name for z in definition.allowed_zones),
                "loyalty_cost": definition.loyalty_cost,
                "costs": self.describe(definition.cost_subdefs),
                "effects": self.describe(definition.action_subdefs),
                "parameters": self.describe(definition.subdefs)}
        return result

    def card(self, card):
        """!
        @brief Project a visible card, sharing its row only after every permission check.
        """
        if card not in self.visible:
            raise ValueError("Cannot describe a hidden card.")
        if self._projection_store is not None:
            return self._compact_card(card)
        return self._card(card)

    def _compact_card(self, card):
        """!
        @brief Share a frozen row only within this synchronous joint projection.

        Validate every viewer-dependent gate, including cached descriptions.
        Never key a persistent row by card ID: runtime state/effects can change.
        The shared dict dies before the owned engine resumes.
        """
        shared = self._shared_characteristics
        key = ('visible_card_row', id(card))
        cached = shared.get(key) if shared is not None else None
        if cached is not None and cached.matches(card, self):
            return cached.value
        previous, self._row_reads = self._row_reads, []
        try:
            row = self._projection_store.freeze(self._card(card))
            reads = tuple(self._row_reads)
        finally:
            self._row_reads = previous
        if shared is not None:
            shared[key] = ProjectedCardRow(card, row, reads)
        return row

    def _card(self, card):
        state, runtime = self.state, card.state
        result = {"id": card.command_id, "zone_revision": card.zone_revision,
                  "owner": card.owner.idx, "controller": card.get_controller(state).idx,
                  "zone": card.get_zone().name, "face_down": runtime.face_down,
                  "tapped": card.is_tapped, "counters": self.describe(runtime.counters),
                  "damage_marked": runtime.damage_marked,
                  "damage_by_deathtouch": runtime.damage_by_deathtouch,
                  "attached_to": self.reference(card.attached_to),
                  "attachments": [self.reference(c) for c in runtime.attached.values() if self.is_visible(c)]}
        if not self.knows_identity(card):
            return {**result, "name": None, "identity_visible": False}
        cache = self._shared_characteristics
        entry = cache.get(id(card)) if cache is not None else None
        if entry is None or entry[0] is not card:
            values = self._identity_values(card)
            if cache is not None:
                cache[id(card)] = (card, values)
        else:
            values = entry[1]
        result.update({**values,
            "types": list(values["types"]), "subtypes": list(values["subtypes"]),
            "keywords": list(values["keywords"]), "colors": self.describe(values["colors"]),
            "abilities": [self.ability(a) for a in values["abilities"]],
            "triggers": [self.ability(a) for a in values["triggers"]],
            "static_abilities": self.describe(values["static_abilities"]),
            "attachment_restrictions": list(values["attachment_restrictions"]),
            "skip_untap": self.describe(values["skip_untap"]),
        })
        return result

    def _identity_values(self, card):
        state, runtime = self.state, card.state
        return {
            "name": card.name, "identity_visible": True, "token": card.is_token,
            "legendary": card.definition.legendary,
            **native_characteristics(card, state, lambda: self._base_characteristics(card)),
            "text": card.definition.oracle_text,
            "cost": (mana := self._mana_values(card))[0], "casting_cost": mana[1],
            "mana_value": mana[2], "chosen_x": runtime.mana_x,
            "damage_marked": runtime.damage_marked,
            "damage_by_deathtouch": runtime.damage_by_deathtouch,
            "summoning_sick": card.get_zone() == ZoneType.BATTLEFIELD and card.is_summoning_sick(state),
            "skip_untap": runtime.skip_untap,
            "loyalty_activated_this_turn": card.loyalty_activated_turn == state.turn.number,
            "abilities": tuple(card.get_ability_defs(state).values()),
            "triggers": tuple(card.get_trigger_defs(state).values()),
            "static_abilities": card.get_stat(STAT_STATIC_ABILITIES, state),
            "attachment_restrictions": tuple(restriction_keywords(card.modifiers_to_attach(state))),
        }

    def _base_characteristics(self, card):
        return {'types': tuple(sorted(v.name for v in card.get_types(self.state))),
                'subtypes': tuple(sorted(v.name for v in card.get_subtypes(self.state))),
                'colors': card.get_stat(STAT_COLORS, self.state),
                'keywords': tuple(sorted(card.get_stat(STAT_KEYWORDS, self.state) or ())),
                'power': card.get_power(self.state), 'toughness': card.get_toughness(self.state)}

    def _mana_values(self, card):
        # Kept separate so the optional benchmark can substitute a projection
        # cache without enabling its unproven overhead in normal play.
        return (format_mana_cost(card.get_mana_cost(self.state)),
                format_mana_cost(card.get_casting_cost(self.state)), card.get_mana_value(self.state))

    def stack_items(self):
        """!
        @brief Describe stack order, visible targets and permitted last-known source facts.
        """
        result = []
        # Keep legacy pending_abilities order, and make resolution order explicit.
        for index, item in enumerate(self.state.stack.items):
            context = item.action_resolution.context
            source = context.source
            known = source is None or self.knows_identity(source)
            same_incarnation = context.matches_source_incarnation(
                1 if context.ability is not None and context.ability.is_spell else 0)
            lki = context.source_last_known
            last_known = ({key: self.describe(getattr(lki, key)) for key in (
                "name", "zone", "zone_revision", "controller", "power", "toughness",
                "types", "subtypes", "keywords", "counters",
            )} if lki is not None and self.knows_last_known(lki) else None)
            result.append({
                "position_from_bottom": index, "resolves_next": index == len(self.state.stack.items) - 1,
                "ability": context.action_key if known else None,
                "controller": context.controller.idx if context.controller is not None else None,
                "source": self.reference(source), "source_revision": context.source_revision,
                "source_text": source.definition.oracle_text if source is not None and known and same_incarnation else None,
                "source_last_known": last_known,
                "targets": self.describe(context.targets),
                "parameters": self.describe(context.param_ctx),
                "effects": self.describe(context.effects) if known else None,
            })
        return result

    def ongoing_effects(self):
        """!
        @brief Describe observable continuous effects in deterministic presentation order.
        """
        result = []
        for effect in self.state._cont_effect_manager.query():
            source = effect.definition.source
            if source is not None and not self.knows_identity(source):
                continue
            result.append({"source": self.reference(source),
                           "static_ability": effect.definition.static_ability_key,
                           "duration": self.describe(effect.duration),
                           "created_at": self.describe(effect.definition.created_at),
                           "affected": sorted([ref for obj in effect.state.currently_affected
                                               if (ref := self.reference(obj)) is not None],
                                              key=lambda ref: json.dumps(ref, sort_keys=True)),
                           "modifiers": self.describe(effect.modifiers)})
        # Register slots are allocation details (holes are reused). A rebuilt
        # sampled register may enumerate the SAME effects in a different order.
        # Canonicalize the visible collection, never the engine's layer order.
        return sorted(result, key=lambda row: json.dumps(row, sort_keys=True))

    def replacements(self):
        """!
        @brief Read static budgets without creating/resetting replacement instances.
        """
        instances = getattr(self.state, "_replacement_instances", {})
        result = []
        for card in (*self.public, *self.player.hand.values()):
            if not self.knows_identity(card):
                continue
            enabled = card.get_stat(STAT_STATIC_ABILITIES, self.state) or ()
            for index, definition in enumerate(card.definition.replacement_effects):
                if card.get_zone() not in definition.active_zones or definition.key not in enabled:
                    continue
                bound = instances.get((id(card), card.zone_revision, index))
                result.append({"source": self.reference(card), "definition": self.describe(definition),
                               "remaining_uses": bound.remaining_uses if bound is not None else definition.uses,
                               "remaining_budget": bound.remaining_budget if bound is not None else definition.budget})
        result.extend(self.describe(rule) for rule in self.state.replacement_rules
                      if getattr(rule, "source", None) is None or self.knows_identity(rule.source))
        return result

    def observe_knowledge(self):
        """!
        @brief Reconcile visible identities without serializing rules or full cards.

        Replay needs the same historical memory at every decision, even when it does
        not need a model-facing observation for that already selected decision.
        """
        knowledge = getattr(self.player.controller, "knowledge", None)
        if knowledge is not None:
            knowledge.bind(self.state.knowledge_token, self.player.idx)
            knowledge.observe_snapshot([
                {"id": card.command_id, "name": card.name if self.knows_identity(card) else None,
                 "owner": card.owner.idx, "zone": card.get_zone().name,
                 "identity_visible": self.knows_identity(card)}
                for card in (*self.player.hand.values(), *(card for zone in PUBLIC_ZONES
                              for card in self.public if card.get_zone() == zone))
            ])
        return knowledge

    def build(self, *, update_knowledge=True):
        """!
        @brief Construct the requested owned continuation or detached projection.
        """
        shared = self._shared_characteristics if self._projection_store is not None else None
        # Share rule traversal only within the same synchronous joint projection.
        # Every cached result rechecks transitive visibility gates, including
        # negative references. Frozen outputs cannot mutate this working cache.
        self._ability_descriptions = {} if shared is None else shared.setdefault('ability_descriptions', {})
        self._descriptions = {} if shared is None else shared.setdefault('descriptions', {})
        try:
            return self._build(update_knowledge=update_knowledge)
        finally:
            self._ability_descriptions = None
            self._descriptions = None

    def _build(self, *, update_knowledge):
        state, player = self.state, self.player
        by_zone = {zone: [self.card(c) for c in self.public if c.get_zone() == zone] for zone in PUBLIC_ZONES}
        hand = [self.card(c) for c in player.hand.values()]
        knowledge = (self.observe_knowledge() if update_knowledge
                     else getattr(self.player.controller, "knowledge", None))
        combat = state.combat
        return {
            "schema_version": self.schema_version,
            "turn": state.turn.number, "phase": state.turn.phase.name,
            "self": player.idx, "active": state.active_player.idx,
            "priority": state.priority.current_player.idx if state.priority.current_player is not None else None,
            "passed_players": sorted(p.idx for p in state.priority.passed_players),
            "players": [{"id": p.idx, "name": p.name, "life": p.health,
                         "poison_counters": p.poison_counters, "has_lost": p.has_lost,
                         "hand_size": len(p.hand), "library_size": len(p.deck),
                         "maximum_hand_size": p.maximum_hand_size,
                         "lands_played_this_turn": p.lands_played_this_turn,
                         "land_plays_per_turn": p.land_plays_per_turn,
                         "mana": {mana.name: amount for mana, amount in p.mana_pool.items()}}
                        for p in state.players],
            "hand": hand,
            "knowledge": knowledge.view() if knowledge is not None else None,
            "battlefield": by_zone[ZoneType.BATTLEFIELD], "graveyard": by_zone[ZoneType.GRAVEYARD],
            "exile": by_zone[ZoneType.EXILE], "stack": by_zone[ZoneType.STACK],
            "pending_abilities": self.stack_items(),
            "continuous_effects": self.ongoing_effects(),
            "replacement_effects": self.replacements(),
            "combat": {
                "active": combat.active,
                "attackers": [c.command_id for c in combat.attackers],
                "defenders": {c.command_id: self.reference(d) for c, d in combat.attackers.items()},
                "blockers": {b.command_id: a.command_id for b, a in combat.blockers.items()},
                "blocked": sorted(c.command_id for c in combat.blocked),
                "attackers_declared": combat._attackers_declared,
                "first_strike_step": combat._first_strike_step,
                "first_damage_done": combat._first_damage_done,
                "regular_damage_done": combat._regular_damage_done,
            },
            "information_limits": [
                "Opponent hands and all library identities/order are hidden.",
                "Face-down identities are masked; inspection permissions are not yet modeled.",
                "Knowledge covers published zone changes/draws; general reveal and scry permissions are not yet modeled.",
                "description_unavailable marks rule code without a declarative description; do not infer its meaning.",
            ],
        }


def observation(state, player, *, update_knowledge=True, shared_characteristics=None, projection_store=None):
    """!
    @brief Return a detached player-specific view including that player memory.
    """
    return PlayerObservationBuilder(state, player, shared_characteristics=shared_characteristics,
        projection_store=projection_store).build(update_knowledge=update_knowledge)


def decision_observation(request, *, shared_characteristics=None, projection_store=None):
    """!
    @brief Player view plus this request's explicit context/inspection permission.

    Scry grants temporary access to the requested library cards, not the rest of
    the library or an opponent's view. Persistent scry knowledge needs a separate
    history adapter; this function only describes the current decision.
    """
    from game.game_actions.generation.decision_abstraction.auxiliary import ScryRequest
    builder = PlayerObservationBuilder(request.state, request.player, shared_characteristics=shared_characteristics,
                                       projection_store=projection_store)
    result = builder.build()
    if isinstance(request, ScryRequest):
        builder.visible.update(request.candidates)
    result["decision_context"] = {"kind": type(request).__name__, **{
        f.name: builder.describe(getattr(request, f.name))
        for f in fields(request) if f.name not in {"state", "player", "choices"} and not f.name.startswith("_")
    }}
    if isinstance(request, ScryRequest):
        result["decision_context"]["revealed_cards"] = [builder.card(card) for card in request.candidates]
    return result


def card_view(card, state):
    """!
    @brief Compatibility helper; visibility is evaluated for the active player.
    """
    return PlayerObservationBuilder(state, state.active_player).card(card)
