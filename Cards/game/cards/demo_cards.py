"""All demo card rules are defined here once, before a game starts."""

from helper.query_system.query import EqQuery, DifferenceQuery
from game.enums import CardType, ZoneType
from game.game_state import CardDefinition
from game.game_state.registers.card_register import IK_ZONE, IK_TYPE, IK_TAPPED
from game.game_state.registers.player_register import IK_ALIVE
from game.game_actions.data_structs.ability import AbilityDefinition, SubAbilityDefinition
from game.game_actions.data_structs.action_node import EffectActionNode, ImmutableEffectToSlotMap
from game.game_actions.card_effects import DamagePlayerEffect, MoveSourceEffect, TapSourceEffect
from game.game_actions.query_effects import MoveMatchingCardsEffect
from game.target.target_spec import QueryTargetSpec, PlayerQueryTargetSpec
from game.target.target_resolver import TargetSlot, TargetResolver
from game.target.target_selector import SingleTargetSelector

PLAYER_TARGET = TargetSlot(
    key="target",
    target_resolver=TargetResolver(
        PlayerQueryTargetSpec(EqQuery(IK_ALIVE, True)), SingleTargetSelector()
    ),
    distinct_from=frozenset(),
)
PUT_ON_STACK = MoveSourceEffect("put_on_stack", ZoneType.STACK)
TO_GRAVEYARD = MoveSourceEffect("to_graveyard", ZoneType.GRAVEYARD)
TO_BATTLEFIELD = MoveSourceEffect("to_battlefield", ZoneType.BATTLEFIELD)
BOLT_DAMAGE = DamagePlayerEffect("bolt_damage", 3, "target")
ADEPT_DAMAGE = DamagePlayerEffect("adept_damage", 2, "target")
TAP_SOURCE = TapSourceEffect("tap_source")
TAPPED_CREATURES = (
    EqQuery(IK_ZONE, ZoneType.BATTLEFIELD)
    & EqQuery(IK_TYPE, CardType.CREATURE)
    & EqQuery(IK_TAPPED, True)
)
RECALL_EFFECT = MoveMatchingCardsEffect(
    "recall_tapped", QueryTargetSpec(TAPPED_CREATURES), ZoneType.HAND
)


LIGHTNING_BOLT_CAST = AbilityDefinition(
    key="cast:Lightning Bolt",
    is_spell=True,
    allowed_zones=frozenset({ZoneType.HAND}),
    cost_subdefs=(
        SubAbilityDefinition(
            action_node=EffectActionNode(ImmutableEffectToSlotMap({PUT_ON_STACK.key: frozenset()})),
            effects=frozenset({PUT_ON_STACK}),
        ),
    ),
    action_subdefs=(
        SubAbilityDefinition(
            action_node=EffectActionNode(
                ImmutableEffectToSlotMap(
                    {
                        BOLT_DAMAGE.key: frozenset({"target"}),
                        TO_GRAVEYARD.key: frozenset(),
                    }
                )
            ),
            effects=frozenset({BOLT_DAMAGE, TO_GRAVEYARD}),
            slots=frozenset({PLAYER_TARGET}),
        ),
    ),
)

EMBER_ADEPT_CAST = AbilityDefinition(
    key="cast:Ember Adept",
    is_spell=True,
    sorcery_speed=True,
    allowed_zones=frozenset({ZoneType.HAND}),
    cost_subdefs=LIGHTNING_BOLT_CAST.cost_subdefs,
    action_subdefs=(
        SubAbilityDefinition(
            action_node=EffectActionNode(
                ImmutableEffectToSlotMap({TO_BATTLEFIELD.key: frozenset()})
            ),
            effects=frozenset({TO_BATTLEFIELD}),
        ),
    ),
)

EMBER_ADEPT_TAP = AbilityDefinition(
    key="tap_damage",
    allowed_zones=frozenset({ZoneType.BATTLEFIELD}),
    cost_subdefs=(
        SubAbilityDefinition(
            action_node=EffectActionNode(ImmutableEffectToSlotMap({TAP_SOURCE.key: frozenset()})),
            effects=frozenset({TAP_SOURCE}),
        ),
    ),
    action_subdefs=(
        SubAbilityDefinition(
            action_node=EffectActionNode(
                ImmutableEffectToSlotMap({ADEPT_DAMAGE.key: frozenset({"target"})})
            ),
            effects=frozenset({ADEPT_DAMAGE}),
            slots=frozenset({PLAYER_TARGET}),
        ),
    ),
)

TIDAL_RECALL_CAST = AbilityDefinition(
    key="cast:Tidal Recall",
    is_spell=True,
    allowed_zones=frozenset({ZoneType.HAND}),
    cost_subdefs=LIGHTNING_BOLT_CAST.cost_subdefs,
    action_subdefs=(
        SubAbilityDefinition(
            action_node=EffectActionNode(
                ImmutableEffectToSlotMap(
                    {
                        RECALL_EFFECT.key: frozenset(),
                        TO_GRAVEYARD.key: frozenset(),
                    }
                )
            ),
            effects=frozenset({RECALL_EFFECT, TO_GRAVEYARD}),
        ),
    ),
)

LIGHTNING_BOLT = CardDefinition(
    "Lightning Bolt",
    types=frozenset({CardType.INSTANT}),
    abilities=frozenset({LIGHTNING_BOLT_CAST}),
)
EMBER_ADEPT = CardDefinition(
    "Ember Adept",
    types=frozenset({CardType.CREATURE}),
    power=2,
    toughness=2,
    keywords=frozenset({"haste"}),
    abilities=frozenset({EMBER_ADEPT_CAST, EMBER_ADEPT_TAP}),
)
RECALL_TAPPED = CardDefinition(
    "Tidal Recall", types=frozenset({CardType.INSTANT}), abilities=frozenset({TIDAL_RECALL_CAST})
)


# Expanded examples use the same definition objects, target resolvers and effects.
from dataclasses import replace
from game.enums import CardSubtype
from game.game_state.registers.card_register import IK_CONTROLLER, IK_SUBTYPE, IK_KEY
from game.game_state.continuous_rules import StaticContinuousRule
from game.game_state.modifier import AddIntModifier
from game.stat_type import STAT_POWER, STAT_TOUGHNESS
from game.game_actions.data_structs.ability import TriggerAbilityDefinition
from game.game_actions.data_structs.action_node import OrActionNode
from game.game_actions.advanced_effects import (
    DrawEffect,
    SourceTappedCondition,
    InstallShieldEffect,
    DamageCreaturesEffect,
)
from game.target.target_selector import MinMaxTegetSelector


def _allied_beasts(source, controller, state):
    return DifferenceQuery(
        EqQuery(IK_ZONE, ZoneType.BATTLEFIELD)
        & EqQuery(IK_TYPE, CardType.CREATURE)
        & EqQuery(IK_SUBTYPE, CardSubtype.BEAST)
        & EqQuery(IK_CONTROLLER, controller),
        (EqQuery(IK_KEY, source.key),),
    )


BEAST_MASTER = CardDefinition(
    "Beast Master",
    types=frozenset({CardType.CREATURE}),
    power=2,
    toughness=2,
    abilities=frozenset({replace(EMBER_ADEPT_CAST, key="cast_master")}),
    continuous_effects=(
        StaticContinuousRule(
            "beast_bonus",
            QueryTargetSpec(_allied_beasts),
            {STAT_POWER: [AddIntModifier(1)], STAT_TOUGHNESS: [AddIntModifier(1)]},
        ),
    ),
)
BEAST = CardDefinition(
    "Forest Beast",
    types=frozenset({CardType.CREATURE}),
    subtypes=frozenset({CardSubtype.BEAST}),
    power=2,
    toughness=2,
    abilities=frozenset({replace(EMBER_ADEPT_CAST, key="cast_beast")}),
)

DRAW = DrawEffect("draw")
WATCHER_TRIGGER = TriggerAbilityDefinition(
    key="when_tapped_draw",
    condition=SourceTappedCondition(),
    action_subdefs=(
        SubAbilityDefinition(
            action_node=EffectActionNode(ImmutableEffectToSlotMap({DRAW.key: frozenset()})),
            effects=frozenset({DRAW}),
        ),
    ),
)
WATCHER = CardDefinition(
    "Watchful Adept",
    types=frozenset({CardType.CREATURE}),
    power=2,
    toughness=2,
    keywords=frozenset({"haste"}),
    abilities=frozenset({replace(EMBER_ADEPT_CAST, key="cast_watcher"), EMBER_ADEPT_TAP}),
    triggers=frozenset({WATCHER_TRIGGER}),
)

SHIELD = InstallShieldEffect("install_shield")
GUARDIAN_CAST = replace(
    EMBER_ADEPT_CAST,
    key="cast_guardian",
    action_subdefs=(
        SubAbilityDefinition(
            action_node=EffectActionNode(
                ImmutableEffectToSlotMap({TO_BATTLEFIELD.key: frozenset(), SHIELD.key: frozenset()})
            ),
            effects=frozenset({TO_BATTLEFIELD, SHIELD}),
        ),
    ),
)
GUARDIAN = CardDefinition(
    "Guardian",
    types=frozenset({CardType.CREATURE}),
    power=1,
    toughness=3,
    abilities=frozenset({GUARDIAN_CAST}),
)

CREATURE_TARGETS = TargetSlot(
    "target",
    TargetResolver(
        QueryTargetSpec(
            EqQuery(IK_ZONE, ZoneType.BATTLEFIELD) & EqQuery(IK_TYPE, CardType.CREATURE)
        ),
        MinMaxTegetSelector(1, 2, False),
    ),
    distinct_from=frozenset(),
)
SCORCH = DamageCreaturesEffect("scorch", 2)
FORKED_FLAME = CardDefinition(
    "Forked Flame",
    types=frozenset({CardType.INSTANT}),
    abilities=frozenset(
        {
            replace(
                LIGHTNING_BOLT_CAST,
                key="cast_flame",
                action_subdefs=(
                    SubAbilityDefinition(
                        action_node=EffectActionNode(
                            ImmutableEffectToSlotMap(
                                {SCORCH.key: frozenset({"target"}), TO_GRAVEYARD.key: frozenset()}
                            )
                        ),
                        effects=frozenset({SCORCH, TO_GRAVEYARD}),
                        slots=frozenset({CREATURE_TARGETS}),
                    ),
                ),
            )
        }
    ),
)

CHOICE_CAST = replace(
    LIGHTNING_BOLT_CAST,
    key="cast_choice",
    action_subdefs=(
        SubAbilityDefinition(
            action_node=OrActionNode(
                (
                    EffectActionNode(
                        ImmutableEffectToSlotMap(
                            {DRAW.key: frozenset(), TO_GRAVEYARD.key: frozenset()}
                        )
                    ),
                    EffectActionNode(
                        ImmutableEffectToSlotMap(
                            {BOLT_DAMAGE.key: frozenset({"target"}), TO_GRAVEYARD.key: frozenset()}
                        )
                    ),
                )
            ),
            effects=frozenset({DRAW, BOLT_DAMAGE, TO_GRAVEYARD}),
            slots=frozenset({PLAYER_TARGET}),
        ),
    ),
)
CHOICE = CardDefinition(
    "Study or Strike", types=frozenset({CardType.INSTANT}), abilities=frozenset({CHOICE_CAST})
)

EXPANDED_CARDS = {
    "master1": BEAST_MASTER,
    "beast1": BEAST,
    "watcher1": WATCHER,
    "guardian1": GUARDIAN,
    "flame1": FORKED_FLAME,
    "choice1": CHOICE,
}

from game.enums import ManaType
from game.game_actions.mana_effects import AddManaEffect
from game.game_actions.data_structs.action_node import AndActionNode, ManaActionNode
from game.mana.mana_value import ImmutableManaRequirement

ADD_RED = AddManaEffect("add_red", ManaType.RED, 2)
STONE_MANA = replace(
    EMBER_ADEPT_TAP,
    key="add_red",
    uses_stack=False,
    action_subdefs=(
        SubAbilityDefinition(
            action_node=EffectActionNode(ImmutableEffectToSlotMap({ADD_RED.key: frozenset()})),
            effects=frozenset({ADD_RED}),
        ),
    ),
)
MANA_STONE = CardDefinition(
    "Ruby Stone",
    types=frozenset({CardType.ARTIFACT}),
    abilities=frozenset({replace(EMBER_ADEPT_CAST, key="cast_stone"), STONE_MANA}),
)
PAID_BOLT = CardDefinition(
    "Ember Blast",
    types=frozenset({CardType.INSTANT}),
    abilities=frozenset(
        {
            replace(
                LIGHTNING_BOLT_CAST,
                key="cast_blast",
                cost_subdefs=(
                    SubAbilityDefinition(
                        action_node=AndActionNode(
                            (
                                ManaActionNode(
                                    ImmutableManaRequirement(
                                        {frozenset({ManaType.RED}): 1, frozenset(ManaType): 1}
                                    )
                                ),
                                EffectActionNode(
                                    ImmutableEffectToSlotMap({PUT_ON_STACK.key: frozenset()})
                                ),
                            )
                        ),
                        effects=frozenset({PUT_ON_STACK}),
                    ),
                ),
            )
        }
    ),
)
EXPANDED_CARDS.update({"stone1": MANA_STONE, "blast1": PAID_BOLT})

from game.game_actions.advanced_effects import TemporaryBuffEffect

GROW = TemporaryBuffEffect("grow")
GROWTH = CardDefinition(
    "Wild Growth",
    types=frozenset({CardType.INSTANT}),
    abilities=frozenset(
        {
            replace(
                LIGHTNING_BOLT_CAST,
                key="cast_growth",
                action_subdefs=(
                    SubAbilityDefinition(
                        action_node=EffectActionNode(
                            ImmutableEffectToSlotMap(
                                {GROW.key: frozenset({"target"}), TO_GRAVEYARD.key: frozenset()}
                            )
                        ),
                        effects=frozenset({GROW, TO_GRAVEYARD}),
                        slots=frozenset({CREATURE_TARGETS}),
                    ),
                ),
            )
        }
    ),
)
EXPANDED_CARDS["growth1"] = GROWTH

from game.game_actions.advanced_effects import SacrificeEffect

SACRIFICE = SacrificeEffect("sacrifice")
SACRIFICE_TARGET = TargetSlot(
    "sacrifice",
    TargetResolver(
        QueryTargetSpec(
            lambda source, controller, state: EqQuery(IK_ZONE, ZoneType.BATTLEFIELD)
            & EqQuery(IK_TYPE, CardType.CREATURE)
            & EqQuery(IK_CONTROLLER, controller)
        ),
        SingleTargetSelector(),
    ),
    distinct_from=frozenset(),
)
ALTAR_BLAST = replace(
    EMBER_ADEPT_TAP,
    key="sacrifice_blast",
    cost_subdefs=(
        SubAbilityDefinition(
            action_node=EffectActionNode(
                ImmutableEffectToSlotMap({SACRIFICE.key: frozenset({"sacrifice"})})
            ),
            effects=frozenset({SACRIFICE}),
            slots=frozenset({SACRIFICE_TARGET}),
        ),
    ),
)
ALTAR = CardDefinition(
    "Ember Altar",
    types=frozenset({CardType.ARTIFACT}),
    abilities=frozenset({replace(EMBER_ADEPT_CAST, key="cast_altar"), ALTAR_BLAST}),
)
EXPANDED_CARDS["altar1"] = ALTAR

# Teaching definitions for permanent lifecycle rules (not Oracle card data).
from immutabledict import immutabledict
from game.game_actions.permanent_effects import CreateTokenEffect, attachment_ability

SOLDIER_TOKEN = CardDefinition(
    "Soldier",
    types=frozenset({CardType.CREATURE}),
    subtypes=frozenset({CardSubtype.SOLDIER}),
    power=1,
    toughness=1,
)
RECRUIT = CreateTokenEffect("recruit", SOLDIER_TOKEN)
MARSHAL_RECRUIT = AbilityDefinition(
    key="recruit",
    loyalty_cost=-1,
    action_subdefs=(
        SubAbilityDefinition(
            action_node=EffectActionNode(ImmutableEffectToSlotMap({RECRUIT.key: frozenset()})),
            effects=frozenset({RECRUIT}),
        ),
    ),
)
MARSHAL = CardDefinition(
    "Training Marshal",
    types=frozenset({CardType.PLANESWALKER}),
    loyalty=3,
    legendary=True,
    abilities=frozenset({replace(EMBER_ADEPT_CAST, key="cast_marshal"), MARSHAL_RECRUIT}),
)
TRAINING_BONUS = immutabledict(
    {STAT_POWER: (AddIntModifier(1),), STAT_TOUGHNESS: (AddIntModifier(1),)}
)
TRAINING_BLADE = CardDefinition(
    "Training Blade",
    types=frozenset({CardType.ARTIFACT}),
    subtypes=frozenset({CardSubtype.EQUIPMENT}),
    attach_mods=TRAINING_BONUS,
    abilities=frozenset(
        {
            replace(EMBER_ADEPT_CAST, key="cast_blade"),
            attachment_ability(equip=True, mana_cost="{1}"),
        }
    ),
)
TRAINING_AURA = CardDefinition(
    "Training Aura",
    types=frozenset({CardType.ENCHANTMENT}),
    subtypes=frozenset({CardSubtype.AURA}),
    attach_mods=TRAINING_BONUS,
    abilities=frozenset({attachment_ability()}),
)
PLAGUE_SCOUT = CardDefinition(
    "Plague Scout",
    types=frozenset({CardType.CREATURE}),
    power=1,
    toughness=1,
    keywords=frozenset({"infect"}),
    abilities=frozenset({replace(EMBER_ADEPT_CAST, key="cast_scout")}),
)
EXPANDED_CARDS.update(
    {"marshal1": MARSHAL, "blade1": TRAINING_BLADE, "aura1": TRAINING_AURA, "scout1": PLAGUE_SCOUT}
)
