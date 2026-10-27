"""Viewer-filtered evidence and player memory; no hidden-state inference here."""
from dataclasses import dataclass, asdict, fields

from game.game_state import Card


PUBLIC_ZONES = frozenset({"BATTLEFIELD", "GRAVEYARD", "EXILE", "STACK"})


@dataclass(frozen=True)
class PlayerEvent:
    """!
    @brief Viewer-filtered scalar event evidence without hidden runtime references.
    @var kind Explicit record/action kind used by this protocol.
    @var turn Turn retained by this record.
    @var phase Phase retained by this record.
    @var player Index of the player owning these facts.
    @var owner Owner retained by this record.
    @var card_id Card id retained by this record.
    @var name Name retained by this record.
    @var from_zone From zone retained by this record.
    @var to_zone To zone retained by this record.
    """
    kind: str
    turn: int
    phase: str
    player: int | None = None
    owner: int | None = None
    card_id: str | None = None
    name: str | None = None
    from_zone: str | None = None
    to_zone: str | None = None


_EVENT_FIELDS = tuple(field.name for field in fields(PlayerEvent))
_EVENT_SCALARS = frozenset((str, int, float, bool, type(None)))


def event_view(event):
    """!
    @brief Detach native scalar events without recursive dataclass deepcopy.

    Nonstandard/nested values retain asdict's deep isolation semantics. No
    result is cached: edits to the returned dictionary cannot affect memory.
    """
    if type(event) is PlayerEvent:
        values = vars(event)
        if tuple(values) == _EVENT_FIELDS and all(type(v) in _EVENT_SCALARS for v in values.values()):
            return values.copy()
    return asdict(event)


def project_player_event(event, state, viewer):
    """!
    @brief Trusted boundary: never forward a raw payload, LKI, or runtime reference.

    Only explicitly understood events are exposed. A hidden draw has no identity
    even though the engine event contains its Card. Movement endpoints, rather
    than the card's final location after an atomic batch, determine disclosure.
    """
    from game.game_actions.resolution.event_definitions import EVENT_DEFINITIONS
    definition = EVENT_DEFINITIONS.get(event.key)
    if definition is None or not definition.frozen_zone_projection:
        return None
    player = event.controller.idx if event.controller is not None else None
    if event.key == "library_shuffled":
        return PlayerEvent(event.key, state.turn.number, state.turn.phase.name, player, player)
    card = event.source
    if not isinstance(card, Card):
        return None
    origin = event.payload.get("from") if event.key == "card_moved" else "DECK"
    destination = event.payload.get("to") if event.key == "card_moved" else "HAND"
    lki = event.payload.get("last_known")
    known_origin = (origin in PUBLIC_ZONES or (viewer is card.owner and origin == "HAND"))
    known_origin = known_origin and not (lki.face_down if lki is not None else card.state.face_down)
    known_destination = (destination in PUBLIC_ZONES or (viewer is card.owner and destination == "HAND"))
    known_destination = known_destination and not card.state.face_down
    visible = known_origin or known_destination
    name = (lki.name if lki is not None and origin in PUBLIC_ZONES and known_origin else card.name) if visible else None
    return PlayerEvent(event.key, state.turn.number, state.turn.phase.name, player, card.owner.idx,
                       card.command_id if visible else None, name, origin, destination)


class PlayerKnowledge:
    """!
    @brief Evidence retained across decisions, reset explicitly for a new game.

    Locations are conservative facts, not guesses about identities in unknown
    draws. Observed names are not asserted to be original deck composition (copies
    and tokens can change that inference).
    """
    def __init__(self):
        self._game_token = None
        self.player = None
        self.events = []
        self.seen = {}
        self.locations = {}

    def bind(self, game_token, player):
        """!
        @brief Bind memory to a game token and player, resetting it when ownership changes.
        """
        if self._game_token is not game_token or self.player != player:
            self._game_token, self.player = game_token, player
            self.events.clear()
            self.seen.clear()
            self.locations.clear()

    def observe(self, event):
        """!
        @brief Accumulate supported projected event or decision evidence.
        """
        if type(event) is not PlayerEvent:
            raise TypeError("Knowledge accepts only projected PlayerEvent values.")
        self.events.append(event)
        if event.kind == "library_shuffled":
            self._forget_zone(event.owner, "DECK")
        elif event.card_id is None:
            # An unknown card left this zone; exact membership is now uncertain.
            # Adding an unknown drawn card does not erase known cards in hand.
            if event.from_zone in {"HAND", "DECK"}:
                self._forget_zone(event.owner, event.from_zone)
        else:
            self._remember(event.card_id, event.name, event.owner, event.to_zone, "event")

    def _forget_zone(self, owner, zone):
        self.locations = {key: row for key, row in self.locations.items()
                          if not (row["owner"] == owner and row["zone"] == zone)}

    def _remember(self, card_id, name, owner, zone, provenance):
        if name is None:
            return
        self.seen[(card_id, name)] = {"card_id": card_id, "name": name, "owner": owner}
        self.locations[card_id] = {"card_id": card_id, "name": name, "owner": owner,
                                   "zone": zone, "provenance": provenance}

    def observe_snapshot(self, cards):
        """!
        @brief Accept only already visible card rows supplied by the view builder.
        """
        rows = list(cards)
        current = {row["id"] for row in rows}
        # A snapshot can miss transitions if a game was constructed/loaded directly.
        self.locations = {key: row for key, row in self.locations.items()
                          if key in current or (row["zone"] not in PUBLIC_ZONES
                                                and not (row["owner"] == self.player and row["zone"] == "HAND"))}
        for row in rows:
            if row.get("identity_visible", True):
                self._remember(row["id"], row["name"], row["owner"], row["zone"], "snapshot")

    def view(self, recent_events=64):
        """!
        @brief Export observed identities, known locations and bounded recent event evidence.
        """
        return {"observed_cards": [dict(row) for row in self.seen.values()],
                "known_locations": [dict(row) for row in self.locations.values()],
                "recent_events": [event_view(event) for event in self.events[-recent_events:]] if recent_events else [],
                "event_count": len(self.events),
                "coverage": "Zone changes and draws published since this agent joined; no general reveal/scry adapter yet."}
