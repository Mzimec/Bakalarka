"""The opponent prior sees its own information, never a determinized world."""
from dataclasses import replace
import json

import pytest

from benchmarks.search_tactics import tactical_scenario, tactical_policy
from game.ai.observation_policy import ObservationOpponentPolicy
from game.ai.search_simulator import SearchSimulator
from game.ai.search_actions import PlayerAction
from game.ai.visible_effects import effect_features


def scenario(case, wrapper=None):
    factory = tactical_scenario(case)
    if wrapper:
        base = factory
        factory = lambda cs: wrapper(base(cs))
    sim = SearchSimulator(factory, policy_for=tactical_policy, track_information=True)
    return sim, sim.start()


@pytest.mark.parametrize("case,kind", [("lethal_shock", "AbilityAction"),
    ("useful_pacifism", "AbilityAction"), ("redundant_pacifism", "PassPriorityAction"),
    ("attack_pacified", "DeclareAttackersOption")])
def test_tactical_choices_are_legal_and_deterministic(case, kind):
    sim, node = scenario(case)
    info = node.player_information()
    policy = ObservationOpponentPolicy()
    chosen = policy(info)
    assert chosen.kind == kind
    assert policy(replace(info, actions=tuple(reversed(info.actions)))) == chosen
    assert policy(info) == chosen and policy.rank(info)[0].supported
    if case == "attack_pacified":
        assert chosen.describe()["declarations"]["mapping"]
    child = sim.advance_action(node, chosen)
    if case == "lethal_shock":
        for _ in range(2):
            child = sim.advance_action(child, next(a for a in child.player_information().actions if a.kind == "PassPriorityAction"))
        assert child.terminal and child.observation(0)["players"][1]["has_lost"]
    assert sim.fork(child) == child


def test_counter_targets_lethal_spell_and_resolves_through_engine():
    sim, node = scenario("answered_shock")
    policy = ObservationOpponentPolicy()
    node = sim.advance_action(node, policy(node.player_information()))
    node = sim.advance_action(node, next(a for a in node.player_information().actions if a.kind == "PassPriorityAction"))
    assert node.decision.player == 1
    counter = policy(node.player_information())
    assert counter.describe()["source"]["name"] == "Counterspell"
    node = sim.advance_action(node, counter)
    for _ in range(2):
        node = sim.advance_action(node, next(a for a in node.player_information().actions if a.kind == "PassPriorityAction"))
    assert not node.observation(1)["pending_abilities"]
    assert node.observation(1)["players"][1]["life"] == 2
    assert sim.fork(node) == node


def test_uncounterable_public_keyword_makes_counter_unattractive():
    sim, node = scenario("answered_shock")
    policy = ObservationOpponentPolicy()
    node = sim.advance_action(node, policy(node.player_information()))
    node = sim.advance_action(node, next(a for a in node.player_information().actions if a.kind == "PassPriorityAction"))
    info = node.player_information()
    view = info.current.observation()
    view["stack"][0]["keywords"].append("can't be countered")
    changed = replace(info, current=replace(info.current, _json=json.dumps(view)))
    assert policy(changed).kind == "PassPriorityAction"


def test_unknown_effect_does_not_get_inferred_from_familiar_name_or_text():
    from game.cards.starter_cards import DamageTargetEffect
    class CustomDamage(DamageTargetEffect):
        def to_operations(self, *args):
            raise AssertionError("Observation must never execute effects")
    assert effect_features(CustomDamage("shock_damage", 2)) == {"kind": "unknown"}
    assert effect_features(DamageTargetEffect("completely_different_key", 7))["amount"] == 7
    _, root = scenario("lethal_shock")
    info = root.player_information()
    actions = []
    for action in info.actions:
        data = action.describe()
        if action.kind == "AbilityAction":
            data["effect"]["effects"][0]["features"] = {"kind": "unknown"}
            action = PlayerAction(action.kind, json.dumps(data, sort_keys=True))
        actions.append(action)
    unknown = replace(info, actions=tuple(actions))
    ratings = ObservationOpponentPolicy().rank(unknown)
    assert ratings[0].action.kind == "PassPriorityAction"
    assert all(not r.supported for r in ratings if r.action.kind == "AbilityAction")


def test_changing_inaccessible_hand_and_library_does_not_change_policy_input_or_scores():
    from game.game_state import Card
    from game.enums import ZoneType as Z
    from benchmarks.search_hidden_worlds import catalog
    inputs, ratings = [], []
    policy = ObservationOpponentPolicy()
    for name in ("Island", "Counterspell"):
        def wrap(game):
            player = game.state.players[1]
            for zone in (Z.HAND, Z.DECK):
                player.add_card(Card(catalog()[name], player), zone)
            return game
        _, node = scenario("useful_pacifism", wrap)
        info = node.player_information()
        inputs.append(info)
        ratings.append(policy.rank(info))
        assert name not in str(info.current.observation()["hand"])
    assert inputs[0] == inputs[1] and ratings[0] == ratings[1]


def test_opponent_policy_does_not_learn_root_private_hand_between_calls():
    from benchmarks.search_hidden_worlds import beliefs, catalog, public_position
    from game.ai.sampled_scenario import SampledScenario
    inputs, ratings = [], []
    policy = ObservationOpponentPolicy()
    for name in ("Shock", "Plains"):
        own, other = beliefs()
        factory = SampledScenario(public_position, catalog, (replace(own, known_hand=(name,)), other), observer=0, seed=1)
        sim = SearchSimulator(factory, policy_for=tactical_policy, track_information=True)
        root = sim.start()
        policy(root.player_information())  # Reuse one instance for both actors.
        reply = sim.advance_action(root, next(a for a in root.player_information().actions if a.kind == "PassPriorityAction"))
        inputs.append(reply.player_information())
        ratings.append(policy.rank(inputs[-1]))
    assert inputs[0] == inputs[1] and ratings[0] == ratings[1]


def test_memory_risk_uses_only_still_known_hand_cards_and_their_seen_rules():
    _, node = scenario("useful_pacifism")
    info = node.player_information()
    view = info.current.observation()
    # Evidence is already viewer-projected. Name alone is insufficient: the
    # policy must have seen the rule description and a still-known location.
    old = {"stack": [{"id": "known", "name": "Counterspell", "identity_visible": True,
                       "abilities": [{"effects": [{"features": {"kind": "counter", "slot": "target"}}]}]}]}
    recall = info.recall.append("observation", {"view": old})
    view["knowledge"]["known_locations"].append(
        {"card_id": "known", "name": "Counterspell", "owner": 1, "zone": "HAND"})
    remembered = replace(info, current=replace(info.current, _json=json.dumps(view)), recall=recall)
    policy = ObservationOpponentPolicy()
    base = next(r for r in policy.rank(info) if r.action.kind == "AbilityAction")
    changed = next(r for r in policy.rank(remembered) if r.action == base.action)
    assert changed.score < base.score and "remembered_counter_in_hand" in changed.reasons
    # Invalidated location or unrelated observed card identity cannot carry risk.
    forgotten = replace(remembered, current=info.current)
    assert policy.rank(forgotten) == policy.rank(info)
    assert policy.rank(replace(remembered, recall=info.recall)) == policy.rank(info)
    assert policy.rank(info) == policy.rank(forgotten)  # No mutable cross-call memory.


def test_losing_block_is_preferred_to_taking_lethal_damage():
    from benchmarks.search_hidden_worlds import catalog
    from game.enums import ZoneType as Z, TurnPhase as P
    from game.game_state import Card
    def wrap(game):
        state = game.state
        # Convert the unpacified Angel into an attack at a two-life defender.
        attacker = next(c for c in state.players[1].battlefield.values() if c.name == "Serra Angel")
        blocker = Card(catalog()["Cloudkin Seer"], state.players[0])
        blocker.owner.add_card(blocker, Z.BATTLEFIELD)
        state.players[0].health = 2
        state.switch_active_player()
        state.begin_turn()
        state.combat.begin()
        state.turn.phase = P.DECLARE_ATTACKERS
        state.combat.declare_attackers(state.players[1], {attacker: state.players[0]})
        state.turn.phase = P.DECLARE_BLOCKERS
        return game
    sim, node = scenario("useful_pacifism", wrap)
    assert node.decision.kind == "DeclareBlockersRequest"
    chosen = ObservationOpponentPolicy()(node.player_information())
    assert chosen.describe()["declarations"]["mapping"]
    assert sim.advance_action(node, chosen).observation(0)["combat"]["blockers"]


def test_unknown_mandatory_request_is_a_visible_deterministic_fallback():
    _, node = scenario("lethal_shock")
    choices = (PlayerAction("OptionalEffectOption", '{"accept":true}'),
               PlayerAction("OptionalEffectOption", '{"accept":false}'))
    info = replace(node.player_information(), actions=choices)
    policy = ObservationOpponentPolicy()
    assert not policy.rank(info)[0].supported
    assert policy(info) == policy(replace(info, actions=tuple(reversed(choices))))
    assert policy(info) in choices


def test_face_down_blocker_does_not_disclose_printed_characteristics_through_pairs():
    def wrap(game):
        next(iter(game.state.players[1].battlefield.values())).state.face_down = True
        return game
    _, node = scenario("attack_pacified", wrap)
    info = node.player_information()
    assert info.current.observation()["decision_context"]["possible_blocks"] is None
    assert all(not r.supported for r in ObservationOpponentPolicy().rank(info))


def damage_information(*, source_keywords=(), target_toughness=3):
    _, node = scenario("lethal_shock")
    info = node.player_information()
    view = info.current.observation()
    template = view["hand"][0]
    view["battlefield"] = [
        {**template, "id": "source", "controller": 0, "owner": 0, "types": ["CREATURE"],
         "power": 7, "toughness": 7, "keywords": list(source_keywords)},
        {**template, "id": "small", "controller": 1, "owner": 1, "types": ["CREATURE"],
         "power": 1, "toughness": 1, "keywords": [], "damage_marked": 0},
        {**template, "id": "large", "controller": 1, "owner": 1, "types": ["CREATURE"],
         "power": 3, "toughness": target_toughness, "keywords": [], "damage_marked": 0},
    ]
    view["decision_context"] = {"creature": {"card": "source"}}
    return replace(info, current=replace(info.current, request_kind="CombatDamageRequest", _json=json.dumps(view)))


def damage_action(*assignments):
    return PlayerAction("CombatDamageOption", json.dumps({"kind": "CombatDamageOption",
        "assignments": {"mapping": [[target, amount] for target, amount in assignments]}}))


def test_combat_damage_prefers_two_lethal_hits_to_overkilling_one_blocker():
    split = damage_action(({"card": "small"}, 1), ({"card": "large"}, 6))
    overkill = damage_action(({"card": "small"}, 7))
    info = replace(damage_information(), actions=(overkill, split))
    policy = ObservationOpponentPolicy()
    assert policy(info) == split
    assert policy.rank(info)[0].supported
    assert policy(replace(info, actions=(split, overkill))) == split


def test_equal_value_damage_splits_prefer_explicit_recipient_order_not_menu_order():
    info = damage_information()
    view = info.current.observation()
    view["decision_context"].update(recipients=[{"card": "small"}, {"card": "large"}], amount=7)
    info = replace(info, current=replace(info.current, _json=json.dumps(view)))
    default = damage_action(({"card": "small"}, 1), ({"card": "large"}, 6))
    other = damage_action(({"card": "small"}, 4), ({"card": "large"}, 3))
    policy = ObservationOpponentPolicy()
    assert policy(replace(info, actions=(other, default))) == default
    assert policy(replace(info, actions=(default, other))) == default


def test_combat_damage_scores_deathtouch_from_visible_source_and_poison_from_visible_counters():
    info = damage_information(source_keywords=("deathtouch",), target_toughness=7)
    split = damage_action(({"card": "small"}, 1), ({"card": "large"}, 1))
    overkill = damage_action(({"card": "small"}, 2))
    assert ObservationOpponentPolicy()(replace(info, actions=(overkill, split))) == split
    info = damage_information(source_keywords=("infect",))
    view = info.current.observation()
    view["players"][1]["life"] = 1
    view["players"][1]["poison_counters"] = 8
    info = replace(info, current=replace(info.current, _json=json.dumps(view)))
    to_player = damage_action(({"player": 1}, 2))
    to_creature = damage_action(({"card": "small"}, 2))
    info = replace(info, actions=(to_creature, to_player))
    assert ObservationOpponentPolicy().rank(info)[0].action == to_player
    assert ObservationOpponentPolicy().rank(info)[0].score >= 1000
