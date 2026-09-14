"""First executable cards from the supplied Azorius Control deck."""

from dataclasses import replace

from game.cards.starter_cards import _spell, _slot
from game.cards.starter_support import RuleEffect, ScryOperation
from game.enums import CardType, ManaType
from game.game_actions.control_effects import (
    SpellTargetSpec, CounterSpellEffect, DestroyAllCreaturesEffect,
)
from game.operations.card_operations import DrawCardOperation


def opt_operations(state, context):
    """!
    @brief Finish scrying before drawing from the newly ordered library.
    """
    yield ScryOperation(context, 1)
    yield DrawCardOperation(context)


def control_catalog():
    """!
    @brief Return fully implemented control definitions, without placeholders.
    """
    counterspell = _spell(
        "Counterspell", "{U}{U}", CardType.INSTANT,
        (CounterSpellEffect("counterspell"),),
        slots=(_slot(spec=SpellTargetSpec()),), color=ManaType.BLUE,
        oracle_text="Counter target spell.",
    )
    opt = _spell(
        "Opt", "{U}", CardType.INSTANT,
        (RuleEffect("opt", opt_operations, info="Scry 1, then draw a card."),),
        color=ManaType.BLUE, oracle_text="Scry 1.\nDraw a card.",
    )
    verdict = _spell(
        "Supreme Verdict", "{1}{W}{W}{U}", CardType.SORCERY,
        (DestroyAllCreaturesEffect("supreme_verdict"),),
        keywords={"can't be countered"},
        oracle_text="This spell can't be countered.\nDestroy all creatures.",
    )
    verdict = replace(verdict, colors=frozenset({ManaType.WHITE, ManaType.BLUE}),
                      color_identity=frozenset({ManaType.WHITE, ManaType.BLUE}))
    return {card.name: card for card in (counterspell, opt, verdict)}
