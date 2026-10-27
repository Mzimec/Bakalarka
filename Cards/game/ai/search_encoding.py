"""Versioned, identity-free numerical inputs for future policy/value models.

Only detached PlayerInformation enters this module. Runtime IDs bind graph
edges locally; names, oracle text, authored rule keys and hashes are never
numerical features. Variable-length rows avoid padding/truncation in the engine;
a model adapter can batch them with masks. This is a lossy model representation,
not an information-set identity or a replacement for the rules observation.
"""
from dataclasses import dataclass
from math import isfinite

from game.ai.search_recall import PlayerInformation

SCHEMA_VERSION = 1
ZONES = ("hand", "battlefield", "stack", "graveyard", "exile")
TYPES = ("ARTIFACT", "CREATURE", "ENCHANTMENT", "LAND", "INSTANT", "SORCERY", "PLANESWALKER")
COLORS = ("WHITE", "BLUE", "BLACK", "RED", "GREEN", "COLORLESS")
KEYWORDS = ("flying", "reach", "first strike", "double strike", "deathtouch", "lifelink",
            "trample", "vigilance", "haste", "defender", "indestructible", "hexproof",
            "can't attack", "can't block")
PHASES = ("UNTAP", "UPKEEP", "DRAW", "PRECOMBAT_MAIN", "BEGIN_COMBAT", "DECLARE_ATTACKERS",
          "AFTER_ATTACKERS", "DECLARE_BLOCKERS", "AFTER_BLOCKERS", "FIRST_COMBAT_DAMAGE",
          "SECOND_COMBAT_DAMAGE", "END_COMBAT", "POSTCOMBAT_MAIN", "END_STEP", "CLEANUP")
EFFECTS = ("damage", "destroy", "counter", "destroy_creatures", "draw", "gain_life",
           "move_source", "tap_source", "attach", "unknown")
KINDS = ("AbilityAction", "ManaAbilityAction", "LandPlayAction", "PassPriorityAction", "ConcedeAction",
         "DeclareAttackersOption", "DeclareBlockersOption", "MulliganOption", "MulliganBottomOption",
         "DiscardOption", "AbilityResolutionOption", "StartingPlayerOption", "LegendOption",
         "OptionalEffectOption", "ReplacementAcceptanceOption", "ScryOption", "CombatDamageOption",
         "TriggerOrderOption", "ReplacementOption", "ReplacementOrderOption")
CARD_FEATURES = ("identity_visible", "own_controller", "own_owner", "tapped", "face_down",
    "token", "legendary", "summoning_sick", "damage", "power", "power_known", "toughness",
    "toughness_known", "mana_value", "chosen_x", "counter_total", "abilities", "triggers",
    "static_abilities", "unknown_keywords", *ZONES, *TYPES, *COLORS, *KEYWORDS)
PLAYER_FEATURES = ("own", "life", "poison", "hand_size", "library_size", "lost", "lands_played",
                   "land_plays", *COLORS)
GLOBAL_FEATURES = ("turn", "own_active", "own_priority", "combat_active", "pending_count",
                   "continuous_count", "replacement_count", "known_locations", *PHASES, "unknown_phase")
ACTION_FEATURES = (*KINDS, "unknown_kind", "uses_stack", "life_payment", "mana_activations",
                   *COLORS, "generic_payment", "unknown_payment", "accept", "accept_known",
                   "take_mulligan", "mulligan_known")
EFFECT_FEATURES = ("cost", *EFFECTS, "amount", *ZONES, "other_destination")


def _number(value):
    if value is None:
        return 0.0
    if type(value) not in (int, float, bool) or not isfinite(value):
        raise ValueError("Encoder requires finite numeric features")
    return float(value)


def _hot(value, names):
    return tuple(float(value == n) for n in names)


def _features(value):
    if isinstance(value, dict):
        if "features" in value:
            yield value["features"]
        for key, child in value.items():
            if key != "features":
                yield from _features(child)
    elif isinstance(value, list):
        for child in value:
            yield from _features(child)


def _card(card, zone, own):
    visible = card.get("identity_visible", True)
    # Refuse accidental face-down identity features even in malformed input.
    identity = card if visible else {}
    keywords = identity.get("keywords", ())
    numbers = (visible, card.get("controller") == own, card.get("owner") == own,
        card.get("tapped", False), card.get("face_down", False), identity.get("token", False),
        identity.get("legendary", False), card.get("summoning_sick", False), card.get("damage_marked", 0),
        identity.get("power"), identity.get("power") is not None, identity.get("toughness"),
        identity.get("toughness") is not None, identity.get("mana_value", 0), card.get("chosen_x", 0),
        sum(card.get("counters", {}).values()), len(identity.get("abilities", ())),
        len(identity.get("triggers", ())), len(identity.get("static_abilities", ()) or ()),
        sum(k not in KEYWORDS for k in keywords))
    return (tuple(map(_number, numbers)) + _hot(zone, ZONES)
        + tuple(float(t in identity.get("types", ())) for t in TYPES)
        + tuple(float(c in identity.get("colors", ())) for c in COLORS)
        + tuple(float(k in keywords) for k in KEYWORDS))


@dataclass(frozen=True)
class EncodedState:
    """!
    @brief Variable-length numeric state features and explicit visible relationships.
    @var schema_version Schema version retained by this record.
    @var globals Globals retained by this record.
    @var players Players retained by this record.
    @var cards Cards retained by this record.
    @var edges Owned mutable completed action counters.
    @var card_effects Card effects retained by this record.
    """
    schema_version: int
    globals: tuple[float, ...]
    players: tuple[tuple[float, ...], ...]
    cards: tuple[tuple[float, ...], ...]
    # kind: 0 attached-to, 1 blocks, 2 attacks. Players follow card rows.
    edges: tuple[tuple[int, int, int], ...]
    card_effects: tuple[tuple[int, tuple[float, ...]], ...]


@dataclass(frozen=True)
class EncodedAction:
    """!
    @brief Numeric features and bindings for one offered complete action.
    @var schema_version Schema version retained by this record.
    @var features Features retained by this record.
    @var source Source retained by this record.
    @var effects Effects retained by this record.
    @var bindings Bindings retained by this record.
    @var parameters Parameters retained by this record.
    @var selections Selections retained by this record.
    @var effect_slots Effect slots retained by this record.
    @var mana_sources Mana sources retained by this record.
    """
    schema_version: int
    features: tuple[float, ...]
    source: int  # card row, -1 means absent/unavailable
    effects: tuple[tuple[float, ...], ...]
    # role (cost=0, effect=1, option=2), group, source row, target row, multiplicity.
    # A unary target has source=-1. Players follow the state card rows.
    bindings: tuple[tuple[int, int, int, int, float], ...]
    parameters: tuple[tuple[int, int, float], ...]
    # Ordered lists retain the distinction between top and bottom in scry.
    selections: tuple[tuple[int, ...], ...]
    # Each effect row links to slot ordinals in bindings (scoped by cost/effect).
    effect_slots: tuple[tuple[int, ...], ...]
    mana_sources: tuple[int, ...]


def _effect(feature, cost=False):
    kind = feature.get("kind", "unknown")
    if kind not in EFFECTS:
        kind = "unknown"
    destination = str(feature.get("destination", "")).lower()
    if destination == "deck":
        destination = "library"
    return (float(cost), *_hot(kind, EFFECTS), _number(feature.get("amount", 0)),
            *_hot(destination, ZONES), float(bool(destination) and destination not in ZONES))


class StateEncoder:
    """!
    @brief Encode one acting-player view without accessing a sampled native world.
    """
    def encode(self, information):
        """!
        @brief Encode an actor view without accessing a sampled native state.
        """
        return EncodingContext(information).state


class ActionEncoder:
    """!
    @brief Encode offered actions using a shared state/reference context.
    """
    def encode_menu(self, information):
        """!
        @brief Encode a visible state and its offered actions with shared reference rows.
        """
        context = EncodingContext(information)
        return context.state, tuple(context.action(a) for a in information.actions)


class EncodingContext:
    """!
    @brief Decode/index a view once for the complete action menu; no global cache.
    """
    def __init__(self, information):
        if not isinstance(information, PlayerInformation):
            raise TypeError("Encode detached PlayerInformation, never runtime State")
        self.information = information
        self.actions = frozenset(information.actions)
        view = information.current.observation()
        own = information.current.player
        if view.get("self") != own:
            raise ValueError("Observation belongs to another player")
        cards = [(c, z) for z in ZONES for c in view.get(z, ())]
        self.cards = {c["id"]: (i, c.get("zone_revision")) for i, (c, _) in enumerate(cards)}
        players = sorted(view.get("players", ()), key=lambda p: p["id"] != own)
        self.players = {p["id"]: len(cards) + i for i, p in enumerate(players)}
        combat = view.get("combat") or {}
        phase = view.get("phase")
        global_values = (view.get("turn", 0), view.get("active") == own, view.get("priority") == own,
            combat.get("active", False), len(view.get("pending_abilities", ())),
            len(view.get("continuous_effects", ())), len(view.get("replacement_effects", ())),
            len((view.get("knowledge") or {}).get("known_locations", ())))
        edges = []
        for i, (c, _) in enumerate(cards):
            attached = c.get("attached_to")
            if attached is not None:
                target = self.reference(attached if isinstance(attached, dict) else {"card": attached})
                if target >= 0:
                    edges.append((0, i, target))
        for blocker, attacker in combat.get("blockers", {}).items():
            if blocker in self.cards and attacker in self.cards:
                edges.append((1, self.cards[blocker][0], self.cards[attacker][0]))
        for attacker, defender in combat.get("defenders", {}).items():
            target = self.reference(defender)
            if attacker in self.cards and target >= 0:
                edges.append((2, self.cards[attacker][0], target))
        self.state = EncodedState(SCHEMA_VERSION,
            (*map(_number, global_values), *_hot(phase, PHASES), float(phase not in PHASES)),
            tuple(tuple(map(_number, (p["id"] == own, p.get("life", 0), p.get("poison_counters", 0),
                p.get("hand_size", 0), p.get("library_size", 0), p.get("has_lost", False),
                p.get("lands_played_this_turn", 0), p.get("land_plays_per_turn", 0),
                *(p.get("mana", {}).get(c, 0) for c in COLORS)))) for p in players),
            tuple(_card(c, z, own) for c, z in cards), tuple(edges),
            tuple((i, _effect(f)) for i, (c, _) in enumerate(cards) if c.get("identity_visible", True)
                for f in _features(c.get("abilities", ())) ))

    def reference(self, value):
        """!
        @brief Encode a visible card or player reference as numeric relation fields.
        """
        if not isinstance(value, dict):
            return -1
        if type(value.get("player")) is int:
            return self.players.get(value["player"], -1)
        row = self.cards.get(value.get("card")) if isinstance(value.get("card"), str) else None
        if row is not None and value.get("zone_revision", row[1]) == row[1]:
            return row[0]
        return -1

    def action(self, action):
        """!
        @brief Encode one reviewed native option as a complete visible action.
        """
        if action not in self.actions:
            raise ValueError("Action is not in the offered menu")
        data = action.describe()
        cost, effect = data.get("cost") or {}, data.get("effect") or {}
        mana = cost.get("mana") or {}
        paid = {c: 0.0 for c in COLORS}
        generic = unknown = 0.0
        for token, amount in (mana.get("payment") or {}).get("mapping", ()):
            name = token[-1] if isinstance(token, list) and len(token) == 3 else None
            if name in paid:
                paid[name] += _number(amount)
            elif name == "GENERIC":
                generic += _number(amount)
            else:
                unknown += _number(amount)
        bindings, parameters = [], []
        group = 0
        def walk(value, role, slot=None):
            nonlocal group
            if isinstance(value, dict):
                if "mapping" in value:
                    for key, child in value["mapping"]:
                        index = group if slot is None else slot
                        group += 1
                        source, target = self.reference(key), self.reference(child)
                        if source >= 0:
                            count = _number(child) if type(child) in (int, float) else 1.0
                            bindings.append((role, index, source if target >= 0 else -1,
                                             target if target >= 0 else source, count))
                        else:
                            walk(child, role, slot)
                elif self.reference(value) >= 0:
                    bindings.append((role, group if slot is None else slot, -1, self.reference(value), 1.0))
                    group += 1
                else:
                    for child in value.values():
                        walk(child, role, slot)
            elif isinstance(value, list):
                for child in value:
                    walk(child, role, slot)
        effects, effect_slots = [], []
        for role, part in enumerate((cost, effect)):
            slots = {key: index for index, (key, _) in enumerate(part.get("targets", {}).get("mapping", ()))}
            for key, targets in part.get("targets", {}).get("mapping", ()):
                walk(targets, role, slots[key])
            for binding in part.get("effects", ()):
                feature = binding.get("features", {"kind": "unknown"})
                effects.append(_effect(feature, role == 0))
                effect_slots.append(tuple(sorted({slots[s[0]] for s in binding.get("slots", ()) if s[0] in slots})))
            for index, (_, number) in enumerate((part.get("parameters", {}).get("x_variables") or {}).get("mapping", ())):
                parameters.append((role, index, _number(number)))
        if action.kind not in {"AbilityAction", "ManaAbilityAction", "LandPlayAction"}:
            walk(data, 2)
        return EncodedAction(SCHEMA_VERSION,
            (*_hot(action.kind, KINDS), float(action.kind not in KINDS), float(data.get("uses_stack", False)),
             _number(mana.get("life_payment", 0)), float(len(mana.get("mana_plan", ()))),
             *(paid[c] for c in COLORS), generic, unknown,
             float(data.get("accept", False)), float("accept" in data),
             float(data.get("take_mulligan", False)), float("take_mulligan" in data)),
            self.reference(data.get("source", data.get("card"))),
            tuple(effects),
            tuple(bindings), tuple(parameters),
            tuple(tuple(self.reference(v) for v in data.get(key, ())) for key in ("cards", "top", "bottom")),
            tuple(effect_slots), tuple(self.reference(a.get("source")) for a in mana.get("mana_plan", ())))
