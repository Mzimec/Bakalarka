"""Shared live/replay boundary between native choices and player information."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from game.ai.observation import decision_observation
from game.ai.search_actions import PlayerAction, PlayerActionBuilder, UnsupportedPlayerAction, canonical_json
from game.ai.search_descriptors import ChoiceDescriptor
from game.ai.search_observation import ActingPlayerObservation
from game.ai.search_recall import PlayerInformation, PlayerRecall
from game.ai.structured_values import StructuredJSON, ValueStore, decode

if TYPE_CHECKING:
    from game.game_actions.data_structs.decision_option import DecisionOption
    from game.game_actions.generation.decision_abstraction.requests import DecisionRequest


@dataclass(frozen=True)
class ProjectedDecision:
    """!
    @brief Keep native option bindings outside the portable player information.
    @var information Immutable actor view, recall and offered complete actions.
    @var _bindings Visible actions paired with options belonging to this request.
    @var _view Optional normalized actor view retained for internal replay encoding.
    """

    information: PlayerInformation
    _bindings: tuple[tuple[PlayerAction, DecisionOption], ...] = field(repr=False)
    _view: dict | StructuredJSON | None = field(default=None, repr=False, compare=False)

    def bind(self, action: PlayerAction) -> DecisionOption:
        """!
        @brief Resolve an offered visible action to its request-local native option.
        @throws ValueError If the policy returned an action outside this decision.
        """
        for candidate, option in self._bindings:
            if candidate == action:
                return option
        raise ValueError("Policy returned an action not offered at this decision")


class DecisionProjector:
    """!
    @brief Own one native decision's projection into immutable actor information.
    @var request Native request, never included in portable player information.
    @var options Already generated local options; projection does not execute them.
    @var recall Actor memory before observing the current decision.
    @var view Optional existing viewer-filtered snapshot supplied by the simulator.
    @var store Bounded store sharing immutable visible facts.
    @var structured Whether to retain structured facts or compatible JSON text.
    @var retain_view Whether internal replay encoding also needs the normalized view.
    @var candidate_model Candidate-generation identity scoping information keys.
    @var menu_complete Whether native generation exhausted the legal menu.
    @var candidate_width Number of candidates admitted at this boundary.
    """

    def __init__(
        self,
        request: DecisionRequest,
        options: Iterable[DecisionOption],
        recall: PlayerRecall,
        *,
        view: Mapping[str, object] | None = None,
        retain_view: bool | Literal["structured"] = False,
        store: ValueStore | None = None,
        structured: bool = True,
        candidate_model: str | None = None,
        menu_complete: bool = True,
        candidate_width: int = 0,
    ) -> None:
        """!
        @brief Bind one decision, actor recall and the projection/export settings.
        @param request Native decision whose legality remains engine-owned.
        @param options Complete native options, already generated for this request.
        @param recall Actor memory before appending the new observation.
        @param store Optional bounded owner of shared immutable player facts.
        """
        self.request, self.options, self.recall = request, options, recall
        self.view, self.retain_view = view, retain_view
        self.store, self.structured = store, structured
        self.candidate_model = candidate_model
        self.menu_complete, self.candidate_width = menu_complete, candidate_width

    def _visible_view(self) -> dict:
        """!
        @brief Obtain only the actor snapshot and explicitly supplied setup evidence.
        @return A local mapping that can receive this request's visible context.
        """
        if self.view is not None:
            return dict(self.view)
        request = self.request
        if request.state is None:
            return {"self": request.player.idx, "setup": True,
                    "players": [{"id": p.idx, "name": p.name} for p in request.participants]}
        view = decision_observation(request)
        evidence = getattr(request.player.controller, "initial_search_evidence", None)
        if evidence is not None:
            view["initial_search_evidence"] = evidence
        return view

    def _bind_options(self, builder: PlayerActionBuilder) -> tuple[tuple[PlayerAction, DecisionOption], ...]:
        """!
        @brief Canonicalize complete actions while checking local binding uniqueness.
        @throws UnsupportedPlayerAction If distinct options share one visible identity.
        @throws ValueError If the request supplied no options.
        """
        actions: dict[PlayerAction, DecisionOption] = {}
        for option in self.options:
            action = builder.action(option)
            if action in actions:
                if ChoiceDescriptor.from_option(actions[action]) != ChoiceDescriptor.from_option(option):
                    raise UnsupportedPlayerAction("Distinct local actions have the same player identity")
            else:
                actions[action] = option
        if not actions:
            raise ValueError("A projected decision requires at least one option")
        return tuple(sorted(actions.items(), key=lambda pair: pair[0].sort_key))

    def build(self) -> ProjectedDecision:
        """!
        @brief Project visible facts and append the actor's immutable observation recall.
        @return Portable player information with separately owned native bindings.
        """
        request, view = self.request, self._visible_view()
        builder = PlayerActionBuilder(request, store=self.store, structured=self.structured)
        context = builder.context.request_context()
        if context is not None:
            view["decision_context"] = {"kind": type(request).__name__, **context}
        bindings = self._bind_options(builder)
        current = ActingPlayerObservation(
            request.player.idx,
            type(request).__name__,
            StructuredJSON(view, self.store) if self.structured else canonical_json(view),
        )
        normalized_view = current._json if self.structured else current.observation()
        recall = self.recall.append("observation", {
            "contract_version": 2,
            "request_kind": current.request_kind,
            "view": current._json if self.structured else normalized_view,
        }, store=self.store)
        if self.retain_view == "structured":
            retained = normalized_view
        elif self.retain_view:
            retained = decode(normalized_view) if isinstance(normalized_view, StructuredJSON) else normalized_view
        else:
            retained = None
        information = PlayerInformation(
            current, recall, tuple(action for action, _ in bindings),
            self.candidate_model, self.menu_complete, self.candidate_width,
        )
        return ProjectedDecision(information, bindings, retained)
