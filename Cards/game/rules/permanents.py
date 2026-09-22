"""Permanent lifecycle, attachments and loyalty, using the normal operation pipeline."""

from collections import defaultdict

from game.enums import CardSubtype, CardType, CounterType, ZoneType
from game.game_state.card import Card
from game.game_actions.data_structs.game_action import ResolutionContext
from game.game_actions.data_structs.operation import Operation
from game.game_actions.resolution.event_bus import GameEvent
from game.game_actions.resolution.sba_resolver import StateBasedAction, SBAViolation
from game.operations.card_operations import MoveCardOperation, DamageCreatureOperation

# Damage to an animated planeswalker applies both creature and loyalty results.
DamagePermanentOperation = DamageCreatureOperation


class TokenEntryOperation(MoveCardOperation):
    """!
    @brief Battlefield entry operation for a newly created token.
    """

    def _move(self, state):
        """!
        @brief Insert the new token into its owner's zone collection.
        """
        self.card.owner.add_card(self.card, self.destination, state)

    def execute(self, state):
        """!
        @brief Create the token on the battlefield and mark its move as creation.
        """
        if self.destination != ZoneType.BATTLEFIELD:
            return []  # CR 111.8: an ordinary token cannot be created in another zone.
        from dataclasses import replace

        return [
            (
                replace(event, payload={**event.payload, "from": None, "created_token": True})
                if event.key == "card_moved"
                else event
            )
            for event in super().execute(state)
        ]


class CreateTokenOperation(Operation):
    """!
    @brief Create one or more tokens simultaneously on the battlefield.
    """

    def __init__(self, context, definition, count=1):
        """!
        @param context Resolution context creating the tokens.
        @param definition Card definition shared by the created tokens.
        @param count Number of tokens to create.
        @throws ValueError If count is not a nonnegative integer.
        """
        super().__init__(context)
        if type(count) is not int or count < 0:
            raise ValueError("Token count must be a nonnegative integer.")
        self.definition, self.count = definition, count
        self.created = ()

    def execute(self, state):
        """!
        @brief Prepare replacement effects for all entries, then create the tokens.

        @return Events produced by the resulting battlefield entries.
        """
        player = self.context.controller
        if player not in state.active_players:
            return []

        cards = tuple(Card(self.definition, player, is_token=True) for _ in range(self.count))
        from game.game_actions.resolution.replacement_effects import ReplacementResolver

        entries = tuple(
            TokenEntryOperation(self.context, card, ZoneType.BATTLEFIELD) for card in cards
        )

        # Choose every entry replacement before any member enters. The outer
        # executor captures one simultaneous before/after trigger roster.
        prepared = ReplacementResolver().replace(state, entries)

        events = []
        for operation in prepared:
            events.extend(operation.execute(state))

        # Replacement effects may prevent or otherwise invalidate individual
        # entries, so expose only tokens that actually entered this game state.
        self.created = tuple(card for card in cards if card._game_state is state)
        return events


class CeaseTokenOperation(Operation):
    """!
    @brief Remove a token that exists outside the battlefield.
    """

    def __init__(self, card):
        super().__init__(ResolutionContext(source=card, controller=card.owner))
        self.card = card

    def execute(self, state):
        """!
        @brief Make the token cease to exist without creating another zone change.
        """
        if self.card._game_state is state and self.card.get_zone() != ZoneType.BATTLEFIELD:
            self.card.owner.remove_card(self.card)

        # Ceasing to exist is not another zone change or another death.
        return []


class LoyaltyCostOperation(Operation):
    """!
    @brief Pay a planeswalker-style loyalty activation cost.
    """

    def __init__(self, context, amount):
        super().__init__(context)
        self.amount = amount

    def validation_error(self, state):
        """!
        @brief Return the first reason this loyalty payment is illegal.

        Positive amounts add loyalty and negative amounts remove it.
        """
        source = self.context.source

        if type(self.amount) is not int:
            return "Loyalty cost must be an integer."

        if (
            source.get_zone() != ZoneType.BATTLEFIELD
            or source.get_controller(state) is not self.context.controller
        ):
            return "Activate a loyalty ability of a permanent you control."

        if source.loyalty_activated_turn == state.turn.number:
            return "This permanent has already activated a loyalty ability this turn."

        if source.state.counters.get(CounterType.LOYALTY, 0) + self.amount < 0:
            return "Insufficient loyalty counters."

        return None

    def execute(self, state):
        """!
        @brief Apply the loyalty change and consume this turn's activation.
        """
        error = self.validation_error(state)
        if error:
            raise ValueError(error)

        source = self.context.source
        source.state.counters[CounterType.LOYALTY] = (
            source.state.counters.get(CounterType.LOYALTY, 0) + self.amount
        )
        source.loyalty_activated_turn = state.turn.number

        return [GameEvent("loyalty_paid", source, self.context.controller, {"amount": self.amount})]


def attachment_legal(state, attachment, host, *, entering=False):
    """!
    @brief Check structural Aura/Equipment attachment legality.

    Targeting legality is handled separately by the targeting system. During
    entry, the attachment itself need not already be on the battlefield.

    @param state Current game state.
    @param attachment Aura or Equipment being attached.
    @param host Proposed permanent to attach to.
    @param entering Whether the attachment is currently entering the battlefield.
    @return Whether the attachment may structurally remain attached to the host.
    """
    if not isinstance(host, Card) or host is attachment or host._game_state is not state:
        return False

    if host.get_zone() != ZoneType.BATTLEFIELD:
        return False

    if not entering and attachment.get_zone() != ZoneType.BATTLEFIELD:
        return False

    if CardType.CREATURE in attachment.get_types(state):
        return False

    subtypes = attachment.get_subtypes(state)

    if CardSubtype.EQUIPMENT in subtypes:
        return CardType.CREATURE in host.get_types(state) and not host.has_keyword(
            state, "can't be equipped"
        )

    if CardSubtype.AURA in subtypes:
        restriction = attachment.definition.enchant
        return (
            bool(restriction(state, attachment, host))
            if restriction
            else CardType.CREATURE in host.get_types(state)
        )

    return False


class AttachOperation(Operation):
    """!
    @brief Attach an Aura or Equipment to a battlefield permanent.
    """

    def __init__(self, context, attachment, host, *, entering=False):
        super().__init__(context)
        self.attachment, self.host, self.entering = attachment, host, entering

    def execute(self, state):
        """!
        @brief Resolve attachment entry if needed, then attach to the host.
        """
        card = self.attachment

        if not attachment_legal(state, card, self.host, entering=self.entering):
            # An Aura resolving without a legal object does not enter unattached.
            if self.entering and card.get_zone() == ZoneType.STACK:
                return MoveCardOperation(self.context, card, ZoneType.GRAVEYARD).execute(state)
            return []

        events = []

        if self.entering:
            from game.game_actions.resolution.replacement_effects import ReplacementResolver

            entry = MoveCardOperation(self.context, card, ZoneType.BATTLEFIELD)

            # Entry projection can use this prospective host when evaluating
            # continuous/replacement effects affecting the incoming attachment.
            entry.entry_attachment_host = self.host

            for operation in ReplacementResolver().replace(state, (entry,)):
                events.extend(operation.execute(state))

        if card.get_zone() != ZoneType.BATTLEFIELD:
            return events

        # Recheck after entry replacements because the resulting characteristics
        # of either object may differ from those used before entry.
        if not attachment_legal(state, card, self.host):
            return events

        if card.attached_to is self.host:
            return events

        card.attach(self.host, state)
        events.append(GameEvent("attached", card, card.get_controller(state), {"host": self.host}))
        return events


class DetachOperation(Operation):
    """!
    @brief Detach a permanent from its current host.
    """

    def __init__(self, card):
        super().__init__(ResolutionContext(source=card, controller=card.owner))
        self.card = card

    def execute(self, state):
        """!
        @brief Detach the card and emit an event when an attachment existed.
        """
        host = self.card.attached_to
        self.card.detach()

        return (
            [GameEvent("detached", self.card, self.card.get_controller(state), {"host": host})]
            if host
            else []
        )


class CancelCountersOperation(Operation):
    """!
    @brief Cancel matching +1/+1 and -1/-1 counters from one permanent.
    """

    def __init__(self, card, amount):
        super().__init__(ResolutionContext(source=card, controller=card.owner))
        self.card, self.amount = card, amount

    def execute(self, state):
        """!
        @brief Remove the same amount of both opposing counter types.
        """
        for counter in (CounterType.PLUS_ONE, CounterType.MINUS_ONE):
            self.card.state.counters[counter] = max(
                0, self.card.state.counters.get(counter, 0) - self.amount
            )
        return []


class PermanentStateRule(StateBasedAction):
    """!
    @brief Collect state-based actions related to permanent lifecycle.

    Handles tokens outside the battlefield, zero-loyalty planeswalkers,
    illegal attachments, the legend rule, and +1/+1/-1/-1 counter cancellation.
    """

    def collect(self, state):
        """!
        @brief Collect all currently applicable permanent-related SBA violations.

        No mutation occurs here; resulting operations are executed later by the
        SBA resolver as part of the normal operation pipeline.

        @param state Current game state.
        @return List of state-based action violations.
        """
        violations, legends = [], defaultdict(list)

        from helper.query_system.query import EqQuery
        from game.game_state.registers.card_register import IK_ZONE, IK_IS_TOKEN

        # Preserve one stable candidate order for simultaneous violations.
        candidates = state.query_cards(
            EqQuery(IK_ZONE, ZoneType.BATTLEFIELD) | EqQuery(IK_IS_TOKEN, True)
        ) if hasattr(state, "query_cards") else ()
        for card in candidates:
            context = ResolutionContext(source=card, controller=card.get_controller(state))

            if card.get_zone() != ZoneType.BATTLEFIELD:
                if card.is_token:
                    violations.append(SBAViolation((CeaseTokenOperation(card),)))
                continue

            types, subtypes = card.get_types(state), card.get_subtypes(state)

            dies = (
                CardType.PLANESWALKER in types
                and card.state.counters.get(CounterType.LOYALTY, 0) == 0
            )

            # An illegally attached Aura goes to its owner's graveyard, while
            # other attachment types simply become unattached.
            if CardSubtype.AURA in subtypes and not attachment_legal(state, card, card.attached_to):
                dies = True
            elif card.attached_to is not None and not attachment_legal(
                state, card, card.attached_to
            ):
                violations.append(SBAViolation((DetachOperation(card),)))

            if dies:
                violations.append(
                    SBAViolation((MoveCardOperation(context, card, ZoneType.GRAVEYARD),))
                )

            if card.definition.legendary:
                legends[(card.get_controller(state), card.name)].append(card)

            cancel = min(
                card.state.counters.get(CounterType.PLUS_ONE, 0),
                card.state.counters.get(CounterType.MINUS_ONE, 0),
            )

            if cancel:
                violations.append(SBAViolation((CancelCountersOperation(card, cancel),)))

        if not legends:
            return violations

        # Resolve legend choices in APNAP player order. All permanents except
        # the chosen one are moved simultaneously by one SBA violation.
        players = list(state.players)
        start = players.index(state.active_player)

        for player in players[start:] + players[:start]:
            for (controller, _), cards in legends.items():
                if controller is not player or len(cards) < 2:
                    continue

                from ..ai.decision_maker import LegendRequest
                keep = player.decision_maker.decide(LegendRequest(state, player, tuple(cards))).value.selected

                if keep not in cards:
                    raise ValueError("Choose one of the legendary permanents to keep.")

                violations.append(
                    SBAViolation(
                        tuple(
                            MoveCardOperation(
                                ResolutionContext(source=card, controller=player),
                                card,
                                ZoneType.GRAVEYARD,
                            )
                            for card in cards
                            if card is not keep
                        )
                    )
                )

        return violations