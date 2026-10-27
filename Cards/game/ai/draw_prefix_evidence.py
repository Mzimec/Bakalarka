"""Conservative evidence about the untouched own post-setup library prefix.

Supported native rules publish library zone changes; scry asks the owner before
reordering. Uninstrumented custom library mutations do not satisfy this contract.
Never infer opponent draws, force future outcomes, or continue past an uncertain
library change. The rest of the history remains subject to strict rejection.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class DrawPrefixEvidence:
    """!
    @brief Immutable supported draw evidence derived from player recall.
    @var observer Observer retained by this record.
    @var card_ids Card ids retained by this record.
    @var stop_reason Reason the bounded search loop stopped.
    """
    observer: int
    card_ids: tuple[str, ...]
    stop_reason: str

    @classmethod
    def from_information(cls, information, setup):
        """!
        @brief Extract supported evidence from one player information record.
        """
        observer = setup.observer
        final_deal = set(setup.hands[-1])
        drawn, pending = [], None
        # Requests which neither inspect nor reorder a library. Unknown request
        # kinds are a boundary rather than an implicit permission to proceed.
        safe_requests = {"StartingPlayerRequest", "MulliganRequest", "MulliganBottomRequest",
                         "PriorityDecisionRequest", "DeclareAttackersRequest", "DeclareBlockersRequest",
                         "CombatDamageRequest", "DiscardRequest", "AbilityDecisionRequest",
                         "ManaGenerationRequest", "TriggerOrderRequest", "LegendRequest",
                         "ReplacementRequest", "ReplacementOrderRequest", "ReplacementAcceptanceRequest", "OrderNextRequest"}
        # These reviewed requests only select/prepare actions. Their execution
        # may later change a library, but native zone/shuffle events or a Scry
        # request then stop the prefix at that actual boundary. Do not stop at
        # a harmless trigger or payment choice before subsequent known draws.
        def finish(reason):
            return cls(observer, tuple(drawn), reason)
        for entry in information.recall.entries:
            data = entry.describe()
            if pending is not None:
                # Native DrawCardOperation emits movement then draw. Pair them
                # once; a move to hand alone could be a search, not a top draw.
                if (entry.kind != "event" or data.get("kind") != "card_drawn"
                        or any(data.get(key) != pending.get(key) for key in
                               ("card_id", "owner", "player", "turn", "phase", "name", "from_zone", "to_zone"))):
                    return finish("unpaired_library_move")
                drawn.append(pending["card_id"])
                pending = None
                continue
            if entry.kind == "observation":
                if data["request_kind"] not in safe_requests:
                    return finish("inspection_or_unreviewed_request")
            elif entry.kind == "event":
                owner, kind = data.get("owner"), data.get("kind")
                if owner not in (observer, None):
                    continue
                if kind == "library_shuffled":
                    return finish("library_shuffle")
                if kind == "card_moved" and "DECK" in (data.get("from_zone"), data.get("to_zone")):
                    identity = data.get("card_id")
                    if (owner != observer or data.get("from_zone") != "DECK" or data.get("to_zone") != "HAND"
                            or type(identity) is not str or data.get("name") is None):
                        return finish("library_change")
                    if identity in final_deal or identity in drawn:
                        # Known mulligan bottoms aren't part of the random
                        # residual permutation; strict replay checks their order.
                        return finish("known_or_repeated_card")
                    pending = data
                elif kind == "card_drawn":
                    return finish("unpaired_draw")
        return finish("unpaired_library_move" if pending is not None else "end_of_recall")

    def apply(self, state):
        """!
        @brief Condition a FRESH sampled runtime after normal setup; no extra RNG.

        The library is bottom-first. Keep London mulligan bottoms fixed, pin only the
        observed top prefix, and preserve the relative order of all other random cards.
        For a uniform residual permutation this is exact prefix conditioning.
        """
        if not self.card_ids:
            return
        player = state.players[self.observer]
        library = tuple(player.deck.values())
        count = state.mulligans_taken[self.observer]
        bottom, random_part = library[:count], library[count:]
        available = {card.command_id: card for card in random_part}
        if len(set(self.card_ids)) != len(self.card_ids) or any(ref not in available for ref in self.card_ids):
            raise ValueError("Draw prefix must contain distinct cards in the random post-setup library")
        chosen = tuple(available[ref] for ref in self.card_ids)
        ids = set(self.card_ids)
        ordered = (*bottom, *(c for c in random_part if c.command_id not in ids), *reversed(chosen))
        player.deck.clear()
        player.deck.update((card.key, card) for card in ordered)
