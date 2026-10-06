from dataclasses import replace

import pytest

from game.enums import ManaType
from game.game_actions.data_structs.action_node import EffectActionNode, ImmutableEffectToSlotMap
from game.game_actions.mana_effects import AddManaEffect
from game.mana import source_template
from game.rules.lands import mana_ability


def definition_with(*effects):
    base = mana_ability(ManaType.BLUE)
    part = replace(base.action_subdefs[0], effects=frozenset(effects),
                   action_node=EffectActionNode(ImmutableEffectToSlotMap(
                       {effect.key: frozenset() for effect in effects})))
    return replace(base, action_subdefs=(part,))


def test_shared_template_preserves_multioutput_bundle_and_is_immutable():
    definition = definition_with(AddManaEffect("blue", ManaType.BLUE, 2),
                                 AddManaEffect("extra", ManaType.BLUE, 1),
                                 AddManaEffect("red", ManaType.RED, 1))
    template = source_template.static_mana_template(definition)
    assert source_template.static_mana_template(definition) is template
    assert {next(iter(symbol.mana)): count for symbol, count in template.produces.items()} == {
        ManaType.BLUE: 3, ManaType.RED: 1}
    symbol = next(iter(template.produces))
    with pytest.raises(TypeError):
        template.produces[symbol] = 99


def test_mutable_effect_fields_invalidate_template_without_mutating_prior_output():
    effect = AddManaEffect("output", ManaType.BLUE, 1)
    definition = definition_with(effect)
    before = source_template.static_mana_template(definition)
    effect.amount = 3
    effect.mana = ManaType.RED
    after = source_template.static_mana_template(definition)
    assert after is not before
    assert sum(before.produces.values()) == 1
    assert sum(after.produces.values()) == 3
    assert next(iter(after.produces)).mana == frozenset({ManaType.RED})
    effect._key = "missing"
    assert source_template.static_mana_template(definition) is None
    effect._key, effect.mana, effect.amount = "output", ManaType.BLUE, 1
    assert source_template.static_mana_template(definition).produces == before.produces


@pytest.mark.parametrize("amount", [-1, 0, True, 1.5])
def test_invalid_or_empty_output_defers_to_original_path(amount):
    effect = AddManaEffect("output", ManaType.BLUE, 1)
    definition = definition_with(effect)
    assert source_template.static_mana_template(definition) is not None
    effect.amount = amount
    assert source_template.static_mana_template(definition) is None


def test_dynamic_custom_and_overridden_effects_are_never_templates(monkeypatch):
    class Dynamic(AddManaEffect):
        def get_amount(self, state, context):
            return context.controller.health

    assert source_template.static_mana_template(definition_with(Dynamic("dynamic", ManaType.BLUE, 1))) is None
    effect = AddManaEffect("output", ManaType.BLUE, 1)
    definition = definition_with(effect)
    assert source_template.static_mana_template(definition) is not None
    effect.get_amount = lambda state, context: 5
    assert source_template.static_mana_template(definition) is None
    del effect.get_amount
    monkeypatch.setattr(AddManaEffect, "get_amount", lambda *args: 6)
    assert source_template.static_mana_template(definition) is None


def test_changed_definition_and_unused_effect_keys_are_rechecked():
    used = AddManaEffect("output", ManaType.BLUE, 1)
    unused = AddManaEffect("unused", ManaType.RED, 2)
    definition = definition_with(used)
    part = replace(definition.action_subdefs[0], effects=frozenset({used, unused}))
    definition = replace(definition, action_subdefs=(part,))
    template = source_template.static_mana_template(definition)
    unused.amount = 3
    assert source_template.static_mana_template(definition) is not template
    assert sum(source_template.static_mana_template(definition).produces.values()) == 1
    replacement = definition_with(AddManaEffect("new", ManaType.WHITE, 4))
    assert sum(source_template.static_mana_template(replacement).produces.values()) == 4


def test_template_cache_is_bounded_and_retains_definitions(monkeypatch):
    monkeypatch.setattr(source_template, "_TEMPLATES", {})
    monkeypatch.setattr(source_template, "_LIMIT", 2)
    definitions = [definition_with(AddManaEffect(str(i), ManaType.BLUE, 1)) for i in range(3)]
    for definition in definitions:
        assert source_template.static_mana_template(definition).definition is definition
    assert len(source_template._TEMPLATES) == 2
    assert id(definitions[0]) not in source_template._TEMPLATES


def test_discovery_shares_templates_between_cards_but_observes_live_effect_changes(monkeypatch):
    from game.enums import ZoneType
    from game.game_loop.minimal_game import ScriptedController
    from game.game_state import State, Player, Card, CardDefinition
    from game.mana import source_discovery

    state = State([Player([], ScriptedController()) for _ in range(2)])
    player = state.players[0]
    effect = AddManaEffect("output", ManaType.BLUE, 1)
    definition = CardDefinition("Mana artifact", abilities=frozenset({definition_with(effect)}))
    for _ in range(2):
        player.add_card(Card(definition, player), ZoneType.BATTLEFIELD)
    monkeypatch.setattr(source_discovery, "single_sequence",
                        lambda *args: pytest.fail("Static mana source was compiled again"))
    first = state.get_mana_sources(player)
    assert len(first) == 2 and first[0].source is not first[1].source
    assert first[0].produces is first[1].produces
    assert state.get_mana_sources(player)[0].produces is first[0].produces
    effect.amount = 3
    updated = state.get_mana_sources(player)
    assert sum(updated[0].produces.values()) == 3
    assert sum(first[0].produces.values()) == 1
