"""A narrow public constraint on an opponent's untouched opening permutation."""
from dataclasses import dataclass


def observed_mulligans(information):
    """!
    @brief Public final counts, only when an untouched first-upkeep view proves them.
    """
    hand_size = None
    setup = {"StartingPlayerRequest", "MulliganRequest", "MulliganBottomRequest"}
    for entry in information.recall.entries:
        if entry.kind == "event":
            return {}
        if entry.kind != "observation":
            continue
        data = entry.describe()
        view = data["view"]
        if data["request_kind"] == "MulliganRequest" and view["decision_context"]["mulligans_taken"] == 0:
            hand_size = len(view["hand"])
        if data["request_kind"] in setup:
            continue
        if (hand_size is None or data["request_kind"] != "PriorityDecisionRequest"
                or view.get("turn") != 1 or view.get("phase") != "UPKEEP"
                or any(view.get(k) for k in ("battlefield", "graveyard", "exile", "stack", "pending_abilities"))):
            return {}
        counts = {p["id"]: hand_size - p["hand_size"] for p in view["players"]}
        return counts if all(0 <= n <= hand_size for n in counts.values()) else {}
    return {}


@dataclass(frozen=True)
class OpeningRevealEvidence:
    """!
    @brief Known opening-hand evidence reconstructed from supported recall.
    @var opponent Opponent retained by this record.
    @var card_id Card id retained by this record.
    @var deadline Deadline retained by this record.
    """
    opponent: int
    card_id: str
    deadline: int

    @classmethod
    def from_information(cls, information, *, hand_size):
        """!
        @brief First public hand exit, before either player has changed the opening.

        Only a zero-mulligan opponent observed before the first draw is covered.
        Their revealed card was among the initial hand plus intervening native
        draws. Its actual position is unknown: never assume it was in hand.
        Stop at any earlier non-pass own choice, library change or public move.
        This deliberately does not interpret later plays or manipulated decks.
        """
        observer = information.current.player
        if observer not in (0, 1):
            return None
        opponent, started, draws, pending = 1 - observer, False, 0, None
        setup = {"StartingPlayerRequest", "MulliganRequest", "MulliganBottomRequest"}
        for entry in information.recall.entries:
            data = entry.describe()
            if not started:
                if entry.kind == "event":
                    return None
                if entry.kind != "observation" or data["request_kind"] in setup:
                    continue
                view = data["view"]
                if (data["request_kind"] != "PriorityDecisionRequest"
                        or view.get("turn") != 1 or view.get("phase") != "UPKEEP"
                        or any(view.get(k) for k in ("battlefield", "graveyard", "exile", "stack", "pending_abilities"))):
                    return None
                players = {p["id"]: p for p in view["players"]}
                if players[opponent]["hand_size"] != hand_size:
                    return None
                started = True
                continue
            if pending is not None:
                if (entry.kind != "event" or data.get("kind") != "card_drawn"
                        or any(data.get(k) != pending.get(k) for k in
                               ("card_id", "owner", "player", "turn", "phase", "name", "from_zone", "to_zone"))):
                    return None
                draws += data["owner"] == opponent
                pending = None
                continue
            if entry.kind == "action":
                if data.get("kind") != "PassPriorityAction":
                    return None
            elif entry.kind == "observation":
                if data["request_kind"] != "PriorityDecisionRequest":
                    return None
                if any(data["view"].get(k) for k in ("battlefield", "graveyard", "exile", "stack", "pending_abilities")):
                    return None
            elif entry.kind == "event":
                if data.get("kind") != "card_moved":
                    return None
                if data.get("from_zone") == "DECK" and data.get("to_zone") == "HAND" and data.get("owner") in (0, 1):
                    pending = data
                    continue
                if (data.get("owner") == opponent and data.get("from_zone") == "HAND"
                        and data.get("to_zone") in {"BATTLEFIELD", "STACK"}
                        and type(data.get("card_id")) is str and data.get("name") is not None):
                    return cls(opponent, data["card_id"], hand_size + draws)
                return None
        return None

    def constraints(self):
        """!
        @brief Return explicitly supported knowledge constraints for world sampling.
        """
        result = [(), ()]
        result[self.opponent] = ((self.card_id, self.deadline),)
        return tuple(result)
