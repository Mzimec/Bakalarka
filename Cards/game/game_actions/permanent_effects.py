"""Reusable effects and ability factories for tokens, Auras and Equipment."""

from .data_structs.effect import Effect
from .data_structs.ability import AbilityDefinition, SubAbilityDefinition
from .data_structs.action_node import EffectActionNode, ImmutableEffectToSlotMap
from .card_effects import MoveSourceEffect
from ..enums import ZoneType, CardSubtype
from game.rules.permanents import CreateTokenOperation, AttachOperation, attachment_legal
from ..target.target_spec import TargetSpec
from ..target.target_resolver import TargetSlot, TargetResolver
from ..target.target_selector import SingleTargetSelector


class CreateTokenEffect(Effect):
    """!
    @brief Declarative effect that creates one or more tokens.

    @var definition
        Card definition used for each created token.
    @var count
        Number of tokens created by the effect.
    """

    def __init__(self, key, definition, count=1):
        super().__init__(key)
        self.definition = definition
        self.count = count

    def to_operations(self, state, context):
        """!
        @brief Generate the token-creation operation for this effect.

        @param state Current game state.
        @param context Bound resolution context.
        """
        yield CreateTokenOperation(
            context,
            self.definition,
            self.count,
        )

    def get_info(self):
        """!
        @brief Return a human-readable description of this effect.
        """
        return f"Create {self.count} {self.definition.name} token(s)."


class AttachmentTargetSpec(TargetSpec):
    """!
    @brief Select legal hosts for Aura or Equipment attachment.

    Equipment additionally restricts targeting to permanents controlled by the
    activating player, while Aura legality is delegated to the shared
    attachment rules.

    @var equip
        Whether candidates are being generated for an equip activation.
    """

    def __init__(self, *, equip=False):
        self.equip = equip

    def generate_candidates(self, source, controller, state, reserved=None):
        """!
        @brief Yield legal attachment hosts from the battlefield.

        @param source Aura or Equipment being attached.
        @param controller Controller performing the attachment.
        @param state Current game state.
        @param reserved Optional collection of objects unavailable to this slot.
        @return Generator of legal host permanents.
        """
        from helper.query_system.query import EqQuery
        from game.game_state.registers.card_register import IK_ZONE, IK_CONTROLLER
        query = EqQuery(IK_ZONE, ZoneType.BATTLEFIELD)
        if self.equip:
            query &= EqQuery(IK_CONTROLLER, controller)
        for card in state.query_cards(query):
            if reserved and card in reserved:
                continue

            # Equip targets only permanents controlled by the activating player.
            # This restriction applies to targeting, not to an attachment that
            # already exists after control later changes.
            if self.equip and card.get_controller(state) is not controller:
                continue

            # Aura attachment is evaluated as an entering attachment, while
            # Equipment is already on the battlefield when its ability resolves.
            if attachment_legal(
                state,
                source,
                card,
                entering=not self.equip,
            ):
                yield card


class AttachSourceEffect(Effect):
    """!
    @brief Attach this effect's source to hosts selected through a target slot.

    @var slot_key
        Target slot containing attachment hosts.
    @var entering
        Whether the attachment is established as part of entering the battlefield.
    """

    def __init__(self, key, slot_key="attach", *, entering=False):
        super().__init__(key)
        self.slot_key = slot_key
        self.entering = entering

    def to_operations(self, state, context):
        """!
        @brief Generate attachment operations for the selected hosts.

        @param state Current game state.
        @param context Bound resolution context containing selected targets.
        """
        for option in context.targets.get(self.slot_key, {}).values():
            for host in option:
                yield AttachOperation(
                    context,
                    context.source,
                    host,
                    entering=self.entering,
                )

    def get_info(self):
        """!
        @brief Return a human-readable description of this effect.
        """
        return "Attach this permanent to the selected host."


class _AttachmentValidation:
    # Shared attachment validation; concrete classes provide spell/activation semantics.
    """!
    @brief Ability definition restricted to the matching attachment subtype.

    Spell instances represent Aura casts, while activated instances represent
    Equipment equip abilities.
    """

    def validation_error(self, source, controller, state):
        """!
        @brief Validate the base ability and required card subtype.

        @param source Card providing the ability.
        @param controller Player controlling or casting the ability.
        @param state Current game state.
        @return Validation message on failure, otherwise `None`.
        """
        error = super().validation_error(
            source,
            controller,
            state,
        )

        if error:
            return error

        subtype = self.required_subtype

        if subtype not in source.get_subtypes(state):
            return f"This ability requires {subtype.name}."

        return None


from .data_structs.ability import CastSpellAbilityDefinition, ActivatedAbilityDefinition


class AuraCastAbilityDefinition(_AttachmentValidation, CastSpellAbilityDefinition):
    required_subtype = CardSubtype.AURA


class EquipAbilityDefinition(_AttachmentValidation, ActivatedAbilityDefinition):
    required_subtype = CardSubtype.EQUIPMENT


def attachment_ability(*, equip=False, mana_cost=None, key=None):
    """!
    @brief Build either an Aura casting ability or an Equipment equip ability.

    Equip targets a legal permanent controlled by the activating player and is
    available only from the battlefield at sorcery speed. Aura casting targets
    a legal enchant host while the Aura is in hand and moves the source onto
    the stack as part of its casting cost.

    The Aura's printed mana cost is supplied by `CardDefinition` automatically.
    `mana_cost` therefore represents the equip activation cost, or an
    additional Aura casting cost.

    Structural attachment restrictions continue to apply after resolution.
    Equip's controller restriction is only a targeting restriction and does
    not force an already attached Equipment to detach if control later changes.

    @param equip Whether to create an Equipment ability instead of an Aura spell.
    @param mana_cost Equip activation cost or additional Aura casting cost.
    @param key Optional explicit ability key.
    @return Configured `AuraCastAbilityDefinition` or `EquipAbilityDefinition`.
    """
    slot = TargetSlot(
        "attach",
        TargetResolver(
            AttachmentTargetSpec(equip=equip),
            SingleTargetSelector(),
        ),
        distinct_from=frozenset(),
    )

    effect = AttachSourceEffect(
        "attach_source",
        entering=not equip,
    )

    action = SubAbilityDefinition(
        action_node=EffectActionNode(
            ImmutableEffectToSlotMap(
                {
                    effect.key: frozenset({slot.key}),
                }
            )
        ),
        effects=frozenset({effect}),
        slots=frozenset({slot}),
    )

    if equip:
        # Equip is an activated ability whose only explicit cost here is mana.
        cost = SubAbilityDefinition(
            mana_cost=mana_cost,
        )
    else:
        # Casting an Aura moves the source from hand to the stack before its
        # attachment effect is later resolved against the chosen host.
        move = MoveSourceEffect(
            "aura_to_stack",
            ZoneType.STACK,
        )

        cost = SubAbilityDefinition(
            mana_cost=mana_cost,
            action_node=EffectActionNode(
                ImmutableEffectToSlotMap(
                    {
                        move.key: frozenset(),
                    }
                )
            ),
            effects=frozenset({move}),
        )

    definition_type = EquipAbilityDefinition if equip else AuraCastAbilityDefinition
    return definition_type(
        key=key or ("equip" if equip else "cast:aura"),
        sorcery_speed=equip,
        allowed_zones=frozenset(
            {
                ZoneType.BATTLEFIELD
                if equip
                else ZoneType.HAND
            }
        ),
        cost_subdefs=(cost,),
        action_subdefs=(action,),
    )