"""Rule facts for the current turn, separate from the diagnostic event archive.

Live games retain the complete mutable archive. Sampled worlds retain only the
facts queried by native rules; accessing an uncaptured raw archive fails closed
instead of returning incomplete events or inventing hidden historical LKI.
"""


class TurnHistoryUnavailable(RuntimeError):
    """An extension needs history not represented in this sampled world."""


class TurnHistory:
    def __init__(self, events=None, *, retain_archive=True):
        self._events = ([] if events is None else events) if retain_archive else None
        self._damage = {}
        self._unhashable_damage = []
        self._life_losers = []

    @property
    def events(self):
        if self._events is None:
            raise TurnHistoryUnavailable("Raw turn-event history is unavailable in a sampled world")
        return self._events

    @property
    def retains_archive(self):
        return self._events is not None

    def reset(self):
        if self._events is not None:
            self._events = []
        self._damage.clear()
        self._unhashable_damage.clear()
        self._life_losers.clear()

    def record(self, event):
        if self._events is not None:
            self._events.append(event)
        else:
            self._index(event)

    def for_simulation(self):
        """Read native event facts, never an irrelevant event's private payload.

        Unknown event subclasses keep the ordinary archive/copy path. Live raw
        list mutation remains compatible: the projection is rebuilt from that
        list, not from a potentially stale secondary index.
        """
        from game.game_actions.resolution.event_bus import GameEvent
        if self._events is None:
            return self
        result = TurnHistory(retain_archive=False)
        for event in self._events:
            if type(event) is not GameEvent:
                return None
            try:
                result._index(event)
            except TurnHistoryUnavailable:
                return None
        return result

    def _index(self, event):
        from game.game_actions.resolution.event_bus import GameEvent
        if type(event) is not GameEvent:
            raise TurnHistoryUnavailable("Custom event history requires its own fact adapter")
        if event.key not in {"damage_dealt", "life_lost", "life_paid"}:
            return
        payload = event.payload
        amount = payload.get("amount", 0)
        if type(amount) not in (int, float, bool):
            raise TurnHistoryUnavailable("Custom event amount requires its own fact adapter")
        if not amount > 0:
            return
        target = payload.get("target_object")
        if event.key == "damage_dealt":
            record = (event.source, payload.get("source_revision"), target, payload.get("target_revision"))
            try:
                self._damage.setdefault(event.source, []).append(record)
            except TypeError:
                self._unhashable_damage.append(record)
            life_loss = payload.get("life_loss", 0)
            if type(life_loss) not in (int, float, bool):
                raise TurnHistoryUnavailable("Custom life loss requires its own fact adapter")
            if not life_loss > 0:
                return
        from game.game_state.player import Player
        for player in (target, event.controller if event.key == "life_paid" else None):
            if isinstance(player, Player) and not any(player is old for old in self._life_losers):
                self._life_losers.append(player)

    def lost_life(self, player):
        if self._events is not None:
            # Preserve exact live semantics, including custom events and direct
            # edits of the legacy archive. Simulation uses the indexed branch.
            return any(event.key in {"damage_dealt", "life_lost", "life_paid"}
                and event.payload.get("amount", 0) > 0
                and (event.key != "damage_dealt" or event.payload.get("life_loss", 0) > 0)
                and (event.payload.get("target_object") is player
                     or (event.key == "life_paid" and event.controller is player))
                for event in self._events)
        return any(player is old for old in self._life_losers)

    def dealt_damage(self, source, source_revision, target, target_revision):
        if self._events is not None:
            return any(event.key == "damage_dealt" and event.source is source
                and event.payload.get("source_revision") == source_revision
                and event.payload.get("target_object") is target
                and event.payload.get("amount", 0) > 0
                and event.payload.get("target_revision") == target_revision
                for event in self._events)
        try:
            records = self._damage.get(source, ())
        except TypeError:
            records = self._unhashable_damage
        return any(s is source and sr == source_revision and t is target and tr == target_revision
                   for s, sr, t, tr in records)
