"""Library positions justified by one player's detached recall.

Lists are in future draw order; None denotes an unknown copy. Native draws
publish movement followed by draw. Scry and London bottom are private choices,
and native setup redraws are recognized from the observer's mulligan choice.
Unknown library mutations fail closed until an observed shuffle clears them.
"""
from dataclasses import dataclass

from game.ai.runtime_clone import UnsupportedSnapshot


@dataclass(frozen=True)
class LibraryPositions:
    """!
    @brief Track supported known library positions and incarnation revisions.
    @var slots Slots retained by this record.
    @var names Names retained by this record.
    @var revisions Revisions retained by this record.
    """
    slots: tuple[tuple[str | None, ...], ...]
    names: tuple[tuple[str, str], ...]
    revisions: tuple[tuple[str, int], ...] = ()

    @classmethod
    def from_recall(cls, recall, observer, library_sizes):
        """!
        @brief Reconstruct supported evidence from immutable player recall entries.
        """
        libraries = [None, None]
        blocked, names, revisions = set(), {}, {}
        pending_move = inspection = None
        pending_request = None
        hand_ids = set()

        def resize(owner, size):
            current = libraries[owner]
            if current is not None and len(current) != size and any(current):
                blocked.add(owner)
            if current is None or len(current) != size:
                libraries[owner] = [None] * size

        def card_refs(cards):
            refs = []
            for card in cards:
                ref, name = card.get("card", card.get("id")), card.get("name")
                if not isinstance(ref, str) or not isinstance(name, str):
                    blocked.add(observer)
                    return []
                if ref in names and names[ref] != name:
                    blocked.add(observer)
                names[ref] = name
                revision = card.get("zone_revision")
                if type(revision) is int:
                    revisions[ref] = revision
                refs.append(ref)
            return refs

        def remove(move, *, top_draw=False):
            owner, ref = move.get("owner"), move.get("card_id")
            if owner not in (0, 1) or libraries[owner] is None:
                return
            cards = libraries[owner]
            if not cards:
                blocked.add(owner)
            elif top_draw:
                if cards[0] is not None and ref is not None and cards[0] != ref:
                    blocked.add(owner)
                cards.pop(0)
            elif ref is not None and ref in cards:
                cards.remove(ref)
            else:
                if any(cards):
                    blocked.add(owner)
                cards.pop(0)

        for entry in recall.entries:
            data = entry.describe()
            if pending_move is not None:
                paired = (entry.kind == "event" and data.get("kind") == "card_drawn"
                          and all(data.get(k) == pending_move.get(k) for k in
                                  ("owner", "card_id", "turn", "phase", "name")))
                remove(pending_move, top_draw=paired)
                pending_move = None
                if paired:
                    continue
            if entry.kind == "observation":
                view = data.get("view", {})
                for player in view.get("players", ()):
                    if "library_size" in player:
                        resize(player["id"], player["library_size"])
                pending_request = data["request_kind"]
                inspection = None
                hand_ids = {c["id"] for c in view.get("hand", ())}
                if pending_request == "ScryRequest":
                    revealed = view.get("decision_context", {}).get("revealed_cards")
                    if revealed is None or libraries[observer] is None:
                        blocked.add(observer)
                    else:
                        inspection = card_refs(revealed)
                        cards = libraries[observer]
                        if len(inspection) > len(cards):
                            blocked.add(observer)
                        else:
                            for index, ref in enumerate(inspection):
                                if cards[index] not in (None, ref) or ref in cards[len(inspection):]:
                                    blocked.add(observer)
                                cards[index] = ref
                elif pending_request == "MulliganBottomRequest" and libraries[observer] is None:
                    blocked.add(observer)
            elif entry.kind == "action":
                kind = data.get("kind")
                if kind == "MulliganOption" and data.get("take_mulligan"):
                    # Native setup returns the hand, shuffles and redeals before
                    # the next private bottom request; it emits no draw events.
                    libraries[observer] = None
                    blocked.discard(observer)
                elif kind == "ScryOption":
                    top, bottom = card_refs(data["top"]), card_refs(data["bottom"])
                    if (pending_request != "ScryRequest" or inspection is None
                            or sorted(top + bottom) != sorted(inspection)
                            or len(set(top + bottom)) != len(inspection)):
                        blocked.add(observer)
                    else:
                        libraries[observer] = top + libraries[observer][len(inspection):] + bottom
                elif kind == "MulliganBottomOption":
                    bottom = card_refs(data["cards"])
                    if (pending_request != "MulliganBottomRequest" or libraries[observer] is None
                            or len(set(bottom)) != len(bottom) or not set(bottom) <= hand_ids):
                        blocked.add(observer)
                    else:
                        libraries[observer].extend(bottom)
                        for ref in bottom:
                            if ref in revisions:
                                revisions[ref] += 1  # Native hand -> library move.
                pending_request = inspection = None
            elif entry.kind == "event":
                owner, kind = data.get("owner"), data.get("kind")
                if owner not in (0, 1):
                    continue
                if kind == "library_shuffled":
                    libraries[owner] = None
                    blocked.discard(owner)
                elif kind == "card_moved":
                    if data.get("from_zone") == "DECK":
                        pending_move = data
                    if data.get("to_zone") == "DECK":
                        # No destination-position contract for arbitrary returns.
                        blocked.add(owner)
                elif kind == "card_drawn":
                    remove(data, top_draw=True)
        if pending_move is not None:
            remove(pending_move)
        for owner, size in enumerate(library_sizes):
            resize(owner, size)
            known = [ref for ref in libraries[owner] if ref is not None]
            if len(known) != len(set(known)):
                blocked.add(owner)
        if blocked:
            raise UnsupportedSnapshot("Known library ordering requires a position adapter for this mutation")
        return cls(tuple(tuple(cards) for cards in libraries), tuple(sorted(names.items())),
                   tuple(sorted(revisions.items())))
