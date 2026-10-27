from dataclasses import replace
from math import isfinite

import pytest

from game.ai.search_actions import PlayerAction, canonical_json
from game.ai.search_encoding import (ActionEncoder, StateEncoder, EncodingContext, EncodedState,
    EncodedAction, CARD_FEATURES, PLAYER_FEATURES, GLOBAL_FEATURES, ACTION_FEATURES, EFFECT_FEATURES)
from game.ai.action_priors import EncodedActionPrior
from benchmarks.information_set_search import world_factory


def information():
    return world_factory("no_answer")(0).start().player_information()


def test_real_menu_has_fixed_feature_schemas_and_links_to_visible_rows():
    info = information()
    state, actions = ActionEncoder().encode_menu(info)
    assert state == StateEncoder().encode(info)
    assert len(state.globals) == len(GLOBAL_FEATURES)
    assert all(len(row) == len(CARD_FEATURES) for row in state.cards)
    assert all(len(row) == len(PLAYER_FEATURES) for row in state.players)
    assert len(actions) == len(info.actions)
    cast = next(a for a in actions if a.source >= 0)
    assert len(cast.features) == len(ACTION_FEATURES)
    assert all(len(e) == len(EFFECT_FEATURES) for e in cast.effects)
    assert cast.bindings and all(isfinite(v) for v in cast.features)
    assert cast.mana_sources and all(i >= 0 for i in cast.mana_sources)
    assert len(cast.effect_slots) == len(cast.effects)
    damage = next(i for i, e in enumerate(cast.effects) if e[EFFECT_FEATURES.index("damage")])
    assert cast.effect_slots[damage] == (0,)
    assert all(b[1] == 0 for b in cast.bindings if b[0] == 1)
    assert all(-1 <= b[2] < len(state.cards) + len(state.players) and
               0 <= b[3] < len(state.cards) + len(state.players) for b in cast.bindings)


def test_names_text_rule_keys_and_physical_ids_are_not_model_features():
    info = information()
    def rename(value):
        if isinstance(value, dict):
            return {k: ("renamed" if k in {"name", "text", "key", "action_key", "ability_key"}
                        else rename(v)) for k, v in value.items()}
        if isinstance(value, list):
            return [rename(v) for v in value]
        if isinstance(value, str) and value.startswith("c") and value[1:].isdigit():
            return "renamed-" + value
        return value
    view = rename(info.current.observation())
    actions = tuple(replace(a, _json=canonical_json(rename(a.describe()))) for a in info.actions)
    renamed = replace(info, current=replace(info.current, _json=canonical_json(view)), actions=actions)
    assert ActionEncoder().encode_menu(info) == ActionEncoder().encode_menu(renamed)


def test_attachments_combat_and_target_multiplicities_survive_encoding():
    info = information()
    view = info.current.observation()
    cards = view["battlefield"]
    cards[0]["attached_to"] = {"card": cards[1]["id"], "zone_revision": cards[1]["zone_revision"]}
    view["combat"] = {"active": True, "blockers": {cards[0]["id"]: cards[1]["id"]},
        "defenders": {cards[1]["id"]: {"player": 1}}}
    info = replace(info, current=replace(info.current, _json=canonical_json(view)))
    state, actions = ActionEncoder().encode_menu(info)
    assert {e[0] for e in state.edges} == {0, 1, 2}
    assert len({a.bindings for a in actions if a.source >= 0}) >= 2


def test_face_down_identity_and_stale_references_do_not_supply_private_features():
    info = information()
    view = info.current.observation()
    card = view["battlefield"][0]
    card.update(identity_visible=False, power=123, toughness=456, abilities=[{"features": {"kind": "damage", "amount": 999}}])
    context = EncodingContext(replace(info, current=replace(info.current, _json=canonical_json(view))))
    row = context.state.cards[context.cards[card["id"]][0]]
    assert row[CARD_FEATURES.index("power")] == row[CARD_FEATURES.index("abilities")] == 0
    assert context.reference({"card": card["id"], "zone_revision": -1}) == -1
    with pytest.raises(TypeError, match="PlayerInformation"):
        StateEncoder().encode(view)


def test_boolean_options_and_ordered_scry_choices_remain_distinct():
    info = information()
    card = info.current.observation()["hand"][0]
    reference = {"card": card["id"], "zone_revision": card["zone_revision"]}
    actions = tuple(PlayerAction(kind, canonical_json(data)) for kind, data in (
        ("OptionalEffectOption", {"accept": True}), ("OptionalEffectOption", {"accept": False}),
        ("ScryOption", {"top": [reference], "bottom": []}),
        ("ScryOption", {"top": [], "bottom": [reference]})))
    _, encoded = ActionEncoder().encode_menu(replace(info, actions=actions))
    assert encoded[0] != encoded[1] and encoded[2] != encoded[3]


def test_effect_slots_preserve_which_target_receives_damage_and_which_is_destroyed():
    info = information()
    def action(swapped):
        targets = [{"player": 1}, {"player": 0}] if swapped else [{"player": 0}, {"player": 1}]
        return PlayerAction("AbilityAction", canonical_json({"effect": {
            "effects": [{"features": {"kind": "damage", "amount": 2}, "slots": [["a", "a_0"]]},
                        {"features": {"kind": "destroy"}, "slots": [["b", "b_0"]]}],
            "targets": {"mapping": [[slot, {"mapping": [["runtime", {"mapping": [[target, 1]]}]]}]
                                    for slot, target in zip(("a", "b"), targets)]}}}))
    _, encoded = ActionEncoder().encode_menu(replace(info, actions=(action(False), action(True))))
    assert encoded[0].effect_slots == encoded[1].effect_slots == ((0,), (1,))
    assert encoded[0].effects == encoded[1].effects
    assert encoded[0].bindings != encoded[1].bindings


def test_model_prior_only_receives_encoded_inputs_and_keeps_offered_action_alignment():
    info = information()
    def model(state, actions):
        assert type(state) is EncodedState and all(type(a) is EncodedAction for a in actions)
        return [1000 if i == 1 else -1000 for i in range(len(actions))]
    weights = EncodedActionPrior(model, uniform_mix=.1)(info)
    assert sum(weights.values()) == pytest.approx(1)
    assert weights[info.actions[1]] == pytest.approx(.9 + .1 / len(info.actions))
    assert set(weights) == set(info.actions) and all(w > 0 for w in weights.values())


@pytest.mark.parametrize("logits", [[], [float("nan")], [True], ["1"]])
def test_bad_model_outputs_fail_explicitly(logits):
    with pytest.raises(ValueError, match="finite logit"):
        EncodedActionPrior(lambda state, actions: logits)(information())
