"""Extract complete own setup deals from detached, untruncated player recall."""
from dataclasses import dataclass


@dataclass(frozen=True)
class SetupDealEvidence:
    """!
    @brief Ordered physical IDs, first deal then each observed mulligan redraw.

    Redraws are observed at MulliganBottomRequest, BEFORE bottoming. The next
    MulliganRequest contains a smaller hand and is not another complete deal.
    Nothing here predicts keep/bottom decisions or inspects the opponent's hand.
    @var observer Observer retained by this record.
    @var hands Hands retained by this record.
    """
    observer: int
    hands: tuple[tuple[str, ...], ...]

    @classmethod
    def from_information(cls, information, *, hand_size):
        """!
        @brief Extract supported evidence from one player information record.
        """
        observer = information.current.player
        if type(observer) is not int or observer not in (0, 1):
            raise ValueError("Game origin supports two players")
        hands = []
        for entry in information.recall.entries:
            if entry.kind != "observation":
                continue
            payload = entry.describe()
            kind = payload["request_kind"]
            if kind not in {"StartingPlayerRequest", "MulliganRequest", "MulliganBottomRequest"}:
                # Setup is a contiguous prefix; don't interpret later evidence
                # or an imported mid-game history as an initial deal.
                break
            if kind == "StartingPlayerRequest":
                continue
            view = payload["view"]
            context = view["decision_context"]
            if kind == "MulliganRequest":
                if context["mulligans_taken"] != 0:
                    if not hands:
                        return None
                    continue
                if hands:
                    raise ValueError("Repeated initial deal in setup recall")
            else:
                if not hands:
                    return None
                count = context["count"]
                if type(count) is not int or count != len(hands) or count > hand_size:
                    raise ValueError("Mulligan deal evidence must contain consecutive rounds")
            hand = view["hand"]
            if (view["self"] != observer or len(hand) != hand_size
                    or any(c["owner"] != observer or c["zone"] != "HAND"
                           or not c["identity_visible"] for c in hand)):
                raise ValueError("Setup deal evidence is incompatible with the game origin")
            ids = tuple(c["id"] for c in hand)
            if any(type(ref) is not str for ref in ids) or len(set(ids)) != len(ids):
                raise ValueError("Setup deal evidence must contain distinct card IDs")
            hands.append(ids)
        return cls(observer, tuple(hands)) if hands else None

    def constraints(self):
        """!
        @brief Return explicitly supported knowledge constraints for world sampling.
        """
        initial, redraws = [None, None], [(), ()]
        initial[self.observer] = self.hands[0]
        redraws[self.observer] = self.hands[1:]
        return {"opening_hand_ids": tuple(initial), "mulligan_hand_ids": tuple(redraws)}
