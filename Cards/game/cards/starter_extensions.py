"""Executable blue, black and green ANB card definitions, built from reference data."""

from __future__ import annotations

from dataclasses import replace
from functools import lru_cache
from game.paths import DATA_DIR
import json

from immutabledict import immutabledict

from game.enums import (
    CardType as T,
    CardSubtype as ST,
    ManaType as M,
    ZoneType as Z,
    TurnPhase as P,
)
from game.game_state import CardDefinition
from game.game_state.continuous_rules import StaticContinuousRule
from game.game_state.modifier import AddIntModifier, AddSetModifier, AddCollectionModifier
from game.stat_type import (
    STAT_POWER as POWER,
    STAT_TOUGHNESS as TOUGHNESS,
    STAT_KEYWORDS as KEYWORDS,
    STAT_GENERIC_COST_REDUCTION as REDUCTION,
    STAT_TRIGGERS as TRIGGERS,
)
from game.game_actions.data_structs.ability import TriggerAbilityDefinition
from game.game_actions.mana_effects import AddManaEffect
from game.game_actions.permanent_effects import attachment_ability
from game.game_actions.triggers.trigger_condition import (
    EntersBattlefieldCondition,
    StepCondition,
    DiesCondition,
)
from game.game_actions.card_effects import TapSourceEffect
from game.operations.card_operations import (
    MoveCardOperation,
    DrawCardOperation,
    TapCardOperation,
    DamagePlayerOperation,
)
from game.target.target_spec import TargetSpec
from game.rules.lands import mana_ability
from game.cards.starter_support import (
    RuleEffect,
    EventCondition,
    FormulaModifier,
    ScryOperation,
    UntapOperation,
    SkipUntapOperation,
    LoseLifeOperation,
    ChooseMoveOperation,
    FightOperation,
    targets,
    is_death,
    lost_life_this_turn,
)
from game.cards.starter_cards import (
    _creature,
    _cast_ability,
    _activated_ability,
    _effect_subdef,
    _slot,
    _trigger,
    PredicateTargetSpec,
    PutCounterEffect,
    PutCounterOperation,
    DrawCardsEffect,
    GainLifeEffect,
    ReturnSourceEffect,
    TemporaryModifierEffect,
    TemporaryModifierOperation,
    DamageTargetEffect,
    AttackCondition,
)


def own_creatures(state, player):
    return tuple(
        card
        for card in state.get_cards(from_zones=[Z.BATTLEFIELD])
        if card.get_controller(state) is player and card.is_type(state, T.CREATURE)
    )


def count_lands(state, player, subtype=None):
    return sum(
        card.is_type(state, T.LAND) and (subtype is None or subtype in card.get_subtypes(state))
        for card in state.get_cards(from_zones=[Z.BATTLEFIELD])
        if card.get_controller(state) is player
    )


class PlayerSpec(TargetSpec):
    def __init__(self, *, opponent=False):
        self.opponent = opponent

    def generate_candidates(self, source, controller, state, reserved=None):
        """!
        @brief Yield candidate objects permitted by this target specification.
        """
        yield from (
            p
            for p in state.active_players
            if (not self.opponent or p is not controller) and (not reserved or p not in reserved)
        )


class HandSpec(TargetSpec):
    def generate_candidates(self, source, controller, state, reserved=None):
        """!
        @brief Yield candidate objects permitted by this target specification.
        """
        yield from (
            card
            for card in controller.hand.values()
            if card is not source and (not reserved or card not in reserved)
        )


class SelfSpec(TargetSpec):
    def __init__(self, condition=lambda s, c: True):
        self.condition = condition

    def generate_candidates(self, source, controller, state, reserved=None):
        """!
        @brief Yield candidate objects permitted by this target specification.
        """
        if self.condition(state, source):
            yield source


class FlyingSpellSpec(TargetSpec):
    def generate_candidates(self, source, controller, state, reserved=None):
        """!
        @brief Yield candidate objects permitted by this target specification.
        """
        yield from (
            card
            for card in controller.hand.values()
            if card.is_type(state, T.CREATURE) and card.has_keyword(state, "flying")
        )


CREATURE = PredicateTargetSpec(lambda card, source, player, state: card.is_type(state, T.CREATURE))
OWN = PredicateTargetSpec(
    lambda card, source, player, state: card.is_type(state, T.CREATURE)
    and card.get_controller(state) is player
)
OTHER = PredicateTargetSpec(
    lambda card, source, player, state: card.is_type(state, T.CREATURE)
    and card.get_controller(state) is not player
)


def temporary(key, power=0, toughness=0, keywords=(), *, getter=None):
    modifiers = {POWER: (AddIntModifier(power),), TOUGHNESS: (AddIntModifier(toughness),)}
    if keywords:
        modifiers[KEYWORDS] = (AddSetModifier(frozenset(keywords)),)
    return TemporaryModifierEffect(key, modifiers, target_getter=getter)


def self_trigger(key, effects, slots=()):
    return _trigger(key, EntersBattlefieldCondition(source_only=True), effects, slots)


def optional(effect):
    def generate(state, context):
        """!
        @brief Yield choices supported by this generation strategy.
        """
        choose = getattr(context.controller.decision_maker, "choose_optional_effect", None)
        if choose is None or choose(state, context, effect.get_info()):
            yield from effect.to_operations(state, context)

    return RuleEffect(
        effect.key, generate, slot_key=getattr(effect, "slot_key", "target"), info=effect.get_info()
    )


def return_targets(state, context):
    for card in targets(context):
        yield MoveCardOperation(context, card, Z.HAND)


def destroy_targets(state, context):
    for card in targets(context):
        if not card.has_keyword(state, "indestructible"):
            yield MoveCardOperation(context, card, Z.GRAVEYARD)


def draw_target(state, context, amount):
    for player in targets(context):
        for _ in range(amount):
            yield DrawCardOperation(replace(context, controller=player))


def mill(state, context):
    for card in tuple(reversed(tuple(context.controller.deck.values())))[:3]:
        yield MoveCardOperation(context, card, Z.GRAVEYARD)


def world_return(state, context):
    for card in tuple(context.controller.graveyard.values()):
        if card.is_type(state, T.LAND):
            operation = MoveCardOperation(context, card, Z.BATTLEFIELD)
            operation.entry_tapped = True
            yield operation


def sleep(state, context):
    for player in targets(context):
        for card in own_creatures(state, player):
            yield TapCardOperation(context, card, tap_symbol=False)
            yield SkipUntapOperation(context, card, player)


def bad_deal(state, context):
    for _ in range(2):
        yield DrawCardOperation(context)
    for player in state.active_players:
        if player is not context.controller:
            yield ChooseMoveOperation(context, player, 2)
    for player in state.active_players:
        yield LoseLifeOperation(context, player, 2)


def opportunist(state, context):
    for player in state.active_players:
        if player is not context.controller:
            yield LoseLifeOperation(context, player, 2)
    yield from GainLifeEffect("opportunist_life", 2).to_operations(state, context)


def fracture(state, context):
    count = 1 + sum(
        card.name == "Compound Fracture" for card in context.controller.graveyard.values()
    )
    yield from temporary("fracture", -count, -count).to_operations(state, context)


def brontodon(state, context):
    count = count_lands(state, context.controller)
    yield from temporary("brontodon", count, count).to_operations(state, context)


def rakshasa_condition(state, trigger):
    event, source = trigger.event, trigger.source
    return (
        event.key == "card_moved"
        and event.source is source
        and event.payload.get("from") == "STACK"
        and event.payload.get("to") == "BATTLEFIELD"
        and getattr(source, "cast_origin", None) == Z.HAND
        and source.zone_revision == getattr(source, "cast_stack_revision", -2) + 1
    )


def rakshasa_damage(state, context):
    for player in targets(context):
        yield DamagePlayerOperation(
            context, player, count_lands(state, context.controller, ST.SWAMP)
        )


def sengir_condition(state, trigger):
    if not is_death(trigger.event) or getattr(state, "_history_turn", None) != state.turn.number:
        return False
    dying = trigger.event.source
    revision = trigger.event.payload["last_known"].zone_revision
    return any(
        event.key == "damage_dealt"
        and event.source is trigger.source
        and event.payload.get("source_revision") == trigger.source_revision
        and event.payload.get("target_object") is dying
        and event.payload.get("amount", 0) > 0
        and event.payload.get("target_revision") == revision
        for event in getattr(state, "turn_events", ())
    )


class CaryatidManaEffect(AddManaEffect):
    def get_info(self):
        """!
        @brief Describe both possible outputs of the conditional mana ability.
        """
        return f"Add one {self.mana.name} mana, or two if you control a creature with power 4 or greater."

    def get_amount(self, state, context):
        return (
            2
            if any(card.get_power(state) >= 4 for card in own_creatures(state, context.controller))
            else 1
        )


def aura(card, modifiers, triggers=()):
    return replace(
        card,
        attach_mods=immutabledict(modifiers),
        abilities=frozenset({attachment_ability()}),
        triggers=frozenset(triggers),
    )


@lru_cache(maxsize=1)
def extension_catalog():
    """!
    @brief Build all fifty remaining starter definitions with executable rules.
    @return A name-keyed catalog; source metadata is vendored for offline use.
    """
    reference = json.loads((DATA_DIR / "starter_card_reference.json").read_text())
    cards = {}
    for row in reference:
        main, _, subtypes = row["type_line"].partition(" \u2014 ")
        types = frozenset(T[name.upper()] for name in main.split())
        subtypes = frozenset(ST[name.upper()] for name in subtypes.split())
        color = {"U": M.BLUE, "B": M.BLACK, "G": M.GREEN}[row["colors"][0]]
        keywords = frozenset(word.lower() for word in row["keywords"])
        power = int(row["power"]) if row["power"] and row["power"] != "*" else 0
        toughness = int(row["toughness"]) if row["toughness"] and row["toughness"] != "*" else 0
        cards[row["name"]] = CardDefinition(
            row["name"],
            mana_cost=row["mana_cost"],
            colors=frozenset({color}),
            color_identity={color},
            types=types,
            subtypes=subtypes,
            power=power if T.CREATURE in types else None,
            toughness=toughness if T.CREATURE in types else None,
            keywords=keywords,
            abilities=frozenset(
                {
                    _cast_ability(
                        row["name"],
                        row["mana_cost"],
                        permanent=bool(types & {T.CREATURE, T.ENCHANTMENT}),
                    )
                }
            ),
        )

    def update(name, **kwargs):
        cards[name] = replace(cards[name], **kwargs)

    def add_ability(name, ability):
        update(name, abilities=cards[name].abilities | {ability})

    def spell(name, effects, slots=()):
        update(
            name,
            abilities=frozenset(
                {_cast_ability(name, cards[name].mana_cost, effects=effects, slots=slots)}
            ),
        )

    def triggers(name, *definitions):
        update(name, triggers=frozenset(definitions))

    def scry_effect(amount):
        return RuleEffect("scry", lambda s, c: (ScryOperation(c, amount),), info=f"Scry {amount}.")

    triggers("Wall of Runes", self_trigger("runes_enter", (scry_effect(1),)))
    triggers("Octoprophet", self_trigger("octoprophet_enter", (scry_effect(2),)))
    triggers(
        "Cloudkin Seer", self_trigger("cloudkin_enter", (DrawCardsEffect("cloudkin_draw", 1),))
    )
    triggers(
        "Waterkin Shaman",
        _trigger(
            "waterkin_enter",
            EventCondition(
                lambda s, tr: tr.event.key == "card_moved"
                and tr.event.payload.get("to") == "BATTLEFIELD"
                and tr.event.controller is tr.controller
                and tr.event.source.is_type(s, T.CREATURE)
                and tr.event.source.has_keyword(s, "flying")
            ),
            (temporary("waterkin_bonus", 1, 1),),
        ),
    )
    update(
        "Warden of Evos Isle",
        continuous_effects=(
            StaticContinuousRule(
                "warden_reduction", FlyingSpellSpec(), {REDUCTION: (AddIntModifier(1),)}
            ),
        ),
    )
    triggers(
        "Soulblade Djinn",
        _trigger(
            "soulblade_cast",
            EventCondition(
                lambda s, tr: tr.event.key == "spell_cast"
                and tr.event.controller is tr.controller
                and not tr.event.source.is_type(s, T.CREATURE)
            ),
            (
                temporary(
                    "soulblade_bonus", 1, 1, getter=lambda s, c: own_creatures(s, c.controller)
                ),
            ),
        ),
    )
    update(
        "Windstorm Drake",
        continuous_effects=(
            StaticContinuousRule(
                "windstorm_bonus",
                PredicateTargetSpec(
                    lambda c, source, p, s: c is not source
                    and c.get_controller(s) is p
                    and c.is_type(s, T.CREATURE)
                    and c.has_keyword(s, "flying")
                ),
                {POWER: (AddIntModifier(1),)},
            ),
        ),
    )
    add_ability(
        "Frilled Sea Serpent",
        _activated_ability(
            "unblockable",
            mana_cost="{5}{U}{U}",
            action_effects=(temporary("serpent_evasion", keywords=("unblockable",)),),
        ),
    )
    triggers(
        "Riddlemaster Sphinx",
        self_trigger(
            "riddlemaster_enter",
            (optional(RuleEffect("returntarget", return_targets)),),
            (_slot(spec=OTHER),),
        ),
    )
    triggers(
        "Windreader Sphinx",
        _trigger(
            "windreader_attack",
            EventCondition(
                lambda s, tr: tr.event.key == "attacker_declared"
                and tr.event.source.has_keyword(s, "flying")
            ),
            (optional(DrawCardsEffect("windreader_draw", 1)),),
        ),
    )
    spell("Unsummon", (RuleEffect("returntarget", return_targets),), (_slot(spec=CREATURE),))
    spell("Glint", (temporary("glint", toughness=3, keywords=("hexproof",)),), (_slot(spec=OWN),))
    spell("Winged Words", (DrawCardsEffect("winged_draw", 2),))
    update(
        "Winged Words",
        continuous_effects=(
            StaticContinuousRule(
                "winged_reduction",
                SelfSpec(
                    lambda s, c: any(
                        unit.has_keyword(s, "flying") for unit in own_creatures(s, c.owner)
                    )
                ),
                {REDUCTION: (AddIntModifier(1),)},
                active_zones=frozenset({Z.HAND}),
            ),
        ),
    )
    spell("Sleep", (RuleEffect("sleep", sleep),), (_slot(spec=PlayerSpec()),))
    spell(
        "Overflowing Insight",
        (RuleEffect("draw_seven", lambda s, c: draw_target(s, c, 7)),),
        (_slot(spec=PlayerSpec()),),
    )
    cards["Waterknot"] = aura(
        cards["Waterknot"],
        {KEYWORDS: (AddSetModifier(frozenset({"doesn't untap"})),)},
        (
            self_trigger(
                "waterknot_enter",
                (
                    RuleEffect(
                        "waterknot_tap",
                        lambda s, c: (
                            (TapCardOperation(c, c.source.attached_to, tap_symbol=False),)
                            if c.source.attached_to
                            else ()
                        ),
                    ),
                ),
            ),
        ),
    )

    add_ability(
        "Sanitarium Skeleton",
        replace(
            _activated_ability(
                "return_skeleton",
                mana_cost="{2}{B}",
                action_effects=(ReturnSourceEffect("skeleton_return"),),
            ),
            allowed_zones=frozenset({Z.GRAVEYARD}),
        ),
    )
    opponent_dies = EventCondition(
        lambda s, tr: is_death(tr.event)
        and tr.event.payload["last_known"].controller is not tr.controller,
        looks_back=True,
    )
    triggers(
        "Malakir Cullblade",
        _trigger("cullblade_death", opponent_dies, (PutCounterEffect("cullblade_counter"),)),
    )
    add_ability(
        "Vampire Opportunist",
        _activated_ability(
            "opportunist", mana_cost="{6}{B}", action_effects=(RuleEffect("drain", opportunist),)
        ),
    )
    discard_slot = _slot("discard", HandSpec())
    discard = RuleEffect(
        "discard",
        lambda s, c: (MoveCardOperation(c, card, Z.GRAVEYARD) for card in targets(c, "discard")),
        slot_key="discard",
    )
    cast = next(iter(cards["Mardu Outrider"].abilities))
    update(
        "Mardu Outrider",
        abilities=frozenset(
            {
                replace(
                    cast,
                    cost_subdefs=(*cast.cost_subdefs, _effect_subdef((discard,), (discard_slot,))),
                )
            }
        ),
    )
    gorger_if = lambda s, e: any(
        lost_life_this_turn(s, p) for p in s.active_players if p is not e.controller
    )
    triggers(
        "Savage Gorger",
        replace(
            _trigger(
                "gorger_end",
                StepCondition(P.END_STEP, controller_turn_only=True),
                (PutCounterEffect("gorger_counter"),),
            ),
            intervening_if=gorger_if,
        ),
    )
    triggers(
        "Skeleton Archer",
        self_trigger("archer_enter", (DamageTargetEffect("archer_damage", 1),), (_slot(),)),
    )
    triggers(
        "Sengir Vampire",
        _trigger(
            "sengir_death",
            EventCondition(sengir_condition, looks_back=True),
            (PutCounterEffect("sengir_counter"),),
        ),
    )
    update("Soulhunter Rakshasa", keywords=cards["Soulhunter Rakshasa"].keywords | {"can't block"})
    triggers(
        "Soulhunter Rakshasa",
        _trigger(
            "rakshasa_enter",
            EventCondition(rakshasa_condition),
            (RuleEffect("rakshasa_damage", rakshasa_damage),),
            (_slot(spec=PlayerSpec(opponent=True)),),
        ),
    )
    formula = FormulaModifier(lambda s, c: count_lands(s, c.get_controller(s), ST.SWAMP))
    update(
        "Nightmare",
        continuous_effects=(
            StaticContinuousRule(
                "nightmare_size",
                SelfSpec(),
                {POWER: (formula,), TOUGHNESS: (formula,)},
                characteristic_defining=True,
                active_zones=frozenset(Z),
            ),
        ),
    )
    triggers(
        "Demon of Loathing",
        _trigger(
            "loathing_damage",
            EventCondition(
                lambda s, tr: tr.event.key == "damage_dealt"
                and tr.event.source is tr.source
                and tr.event.payload.get("combat")
                and tr.event.payload.get("target_object") in s.players
            ),
            (
                RuleEffect(
                    "sacrifice_creature",
                    lambda s, c: (
                        ChooseMoveOperation(
                            c, c.trigger_event.payload["target_object"], 1, sacrifice=True
                        ),
                    ),
                ),
            ),
        ),
    )
    spell("Compound Fracture", (RuleEffect("fracture", fracture),), (_slot(spec=CREATURE),))
    spell(
        "Cruel Cut",
        (RuleEffect("destroy", destroy_targets),),
        (
            _slot(
                spec=PredicateTargetSpec(
                    lambda c, src, p, s: c.is_type(s, T.CREATURE) and c.get_power(s) <= 2
                )
            ),
        ),
    )
    spell("Murder", (RuleEffect("destroy", destroy_targets),), (_slot(spec=CREATURE),))
    spell(
        "Unlikely Aid",
        (temporary("unlikely_aid", 2, keywords=("indestructible",)),),
        (_slot(spec=CREATURE),),
    )
    spell("Bad Deal", (RuleEffect("bad_deal", bad_deal),))
    thirst_trigger = _trigger("thirst_death", opponent_dies, (PutCounterEffect("thirst_counter"),))
    cards["Eternal Thirst"] = aura(
        cards["Eternal Thirst"],
        {
            KEYWORDS: (AddSetModifier(frozenset({"lifelink"})),),
            TRIGGERS: (AddCollectionModifier(frozenset({thirst_trigger})),),
        },
    )

    add_ability(
        "Jungle Delver",
        _activated_ability(
            "delver_counter",
            mana_cost="{3}{G}",
            action_effects=(PutCounterEffect("delver_counter"),),
        ),
    )
    add_ability("Woodland Mystic", mana_ability(M.GREEN))
    for color in (M.WHITE, M.BLUE, M.BLACK, M.RED, M.GREEN):
        base = mana_ability(color)
        add_ability(
            "Ilysian Caryatid",
            replace(
                base,
                action_subdefs=(_effect_subdef((CaryatidManaEffect("caryatid_mana", color),)),),
            ),
        )
    triggers(
        "Baloth Packhunter",
        self_trigger(
            "packhunter_enter",
            (
                PutCounterEffect(
                    "packhunter_counter",
                    amount=2,
                    target_getter=lambda s, c: (
                        unit
                        for unit in own_creatures(s, c.controller)
                        if unit is not c.source and unit.name == "Baloth Packhunter"
                    ),
                ),
            ),
        ),
    )
    update("Prized Unicorn", keywords=cards["Prized Unicorn"].keywords | {"must be blocked by all"})
    triggers(
        "World Shaper",
        _trigger(
            "shaper_attack",
            AttackCondition(source_only=True),
            (optional(RuleEffect("mill_three", mill)),),
        ),
        _trigger(
            "shaper_dies",
            DiesCondition(source_only=True),
            (RuleEffect("return_lands", world_return),),
        ),
    )
    triggers(
        "Affectionate Indrik",
        self_trigger(
            "indrik_enter",
            (
                optional(
                    RuleEffect(
                        "fight",
                        lambda s, c: (FightOperation(c, c.source, target) for target in targets(c)),
                    )
                ),
            ),
            (_slot(spec=OTHER),),
        ),
    )
    triggers(
        "Rampaging Brontodon",
        _trigger(
            "brontodon_attack",
            AttackCondition(source_only=True),
            (RuleEffect("brontodon", brontodon),),
        ),
    )
    from game.enums import CounterType

    spell(
        "Stony Strength",
        (
            RuleEffect(
                "stony_counter",
                lambda s, c: (
                    PutCounterOperation(c, target, CounterType.PLUS_ONE) for target in targets(c)
                ),
            ),
            RuleEffect("untap", lambda s, c: (UntapOperation(c, target) for target in targets(c))),
        ),
        (_slot(spec=OWN),),
    )
    bite = RuleEffect(
        "bite",
        lambda s, c: (
            FightOperation(c, a, b, one_way=True)
            for a in targets(c, "own")
            for b in targets(c, "other")
        ),
        slot_key="own",
    )
    spell("Rabid Bite", (bite,), (_slot("own", OWN), _slot("other", OTHER)))
    # Both slots are read by the bite instruction; retain both in its binding.
    cast = next(iter(cards["Rabid Bite"].abilities))
    from game.game_actions.data_structs.action_node import (
        EffectActionNode,
        ImmutableEffectToSlotMap,
    )

    part = cast.action_subdefs[0]
    options = next(part.action_node.generate_options())
    mapping = dict(options.effects)
    mapping[bite.key] = frozenset({"own", "other"})
    update(
        "Rabid Bite",
        abilities=frozenset(
            {
                replace(
                    cast,
                    action_subdefs=(
                        replace(
                            part, action_node=EffectActionNode(ImmutableEffectToSlotMap(mapping))
                        ),
                    ),
                )
            }
        ),
    )
    triggers(
        "Colossal Majesty",
        replace(
            _trigger(
                "majesty_upkeep",
                StepCondition(P.UPKEEP, controller_turn_only=True),
                (DrawCardsEffect("majesty_draw", 1),),
            ),
            intervening_if=lambda s, e: any(
                unit.get_power(s) >= 4 for unit in own_creatures(s, e.controller)
            ),
        ),
    )
    cards["Epic Proportions"] = aura(
        cards["Epic Proportions"],
        {
            POWER: (AddIntModifier(5),),
            TOUGHNESS: (AddIntModifier(5),),
            KEYWORDS: (AddSetModifier(frozenset({"trample"})),),
        },
    )
    return cards
