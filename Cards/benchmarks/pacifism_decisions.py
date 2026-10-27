"""Reproduce tactical decisions without an LLM; report failures, not fix rules."""
import json
from datetime import datetime
from pathlib import Path

from game.ai.modular_agent import ModularAgent, observation
from game.ai.simple_agent import SimpleAgent
from game.ai.decision_maker import PriorityDecisionRequest, DeclareAttackersRequest
from game.cards.starter_cards import starter_catalog
from game.enums import ZoneType as Z, TurnPhase as P
from game.game_state import State, Player, Card
from game.game_actions.data_structs.game_action import PassPriorityAction
from game.game_actions.resolution.action_processor import ActionProcessor
from game.game_actions.resolution.operation_executor import OperationExecutor
from game.game_actions.resolution.event_bus import EventBus
from game.game_actions.resolution.resolution_engine import ResolutionEngine


def add(state, name, zone=Z.BATTLEFIELD, player=0):
    card = Card(starter_catalog()[name], state.players[player])
    card.owner.add_card(card, zone)
    return card


def position():
    state = State([Player([], ModularAgent(), idx=0), Player([], SimpleAgent(), idx=1)])
    state.turn.phase = P.PRECOMBAT_MAIN
    victim = add(state, "Serra Angel", player=1)
    aura = add(state, "Pacifism")
    aura.attach(victim, state)
    assert victim.has_keyword(state, "can't attack") and victim.has_keyword(state, "can't block")
    return state, victim, aura


def duplicate_pacifism(with_alternative=False):
    state, victim, aura = position()
    player = state.players[0]
    for _ in range(2):
        add(state, "Plains")
    spell = add(state, "Pacifism", Z.HAND)
    other = add(state, "Impassioned Orator", player=1) if with_alternative else None
    candidates = player.controller.candidates.generate(state, player, set())
    chosen = player.controller.decide(PriorityDecisionRequest(state, player)).value
    targets = () if isinstance(chosen, PassPriorityAction) else tuple(SimpleAgent._targets(chosen))
    desired = (isinstance(chosen, PassPriorityAction) if other is None else targets == (other,))
    report = {"case": "prefer_unenchanted_creature" if other is not None else "avoid_second_pacifism",
              "expected": "target unenchanted Orator" if other is not None else "pass",
              "passed": desired, "chosen": type(chosen).__name__,
              "targets": [c.name for c in targets],
              "candidates": [{"score": c.score, **c.description} for c in candidates],
              "state_before": observation(state, player)}
    if not isinstance(chosen, PassPriorityAction):
        engine = ResolutionEngine(OperationExecutor(), EventBus())
        payments = ActionProcessor(engine).process(state, chosen)
        assert payments and all(result.success for result in payments)
        assert not state.stack.is_empty()
        resolution = engine.resolve(state, state.stack.pop())
        assert resolution.success, resolution.error
        assert spell.get_zone() == Z.BATTLEFIELD and spell.attached_to in (victim, other)
        report["pacifisms_on_original_victim_after_resolution"] = sum(
            c.name == "Pacifism" for c in victim.state.attached.values())
    return report


def attack_pacified_creature(remove_aura=False):
    state, victim, aura = position()
    player = state.players[0]
    attacker = add(state, "Impassioned Orator")
    attacker.controlled_since = 0
    if remove_aura:
        aura.owner.move_card(aura, Z.GRAVEYARD, state)
    state.combat.begin()
    state.turn.phase = P.DECLARE_ATTACKERS
    assert attacker in state.combat.legal_attackers(player)
    candidates = player.controller.combat_policy.attackers(state, player)
    chosen = player.controller.decide(DeclareAttackersRequest(state, player)).value
    # Independently prove what the rules allow, including the rejected attack.
    state.combat.declare_attackers(player, {attacker: state.players[1]})
    legal_blockers = state.combat.legal_blockers(state.players[1], attacker)
    assert (victim in legal_blockers) == remove_aura
    return {"case": "avoid_losing_attack_after_aura_removed" if remove_aura else "attack_into_pacified_larger_creature",
            "expected": "no attack" if remove_aura else "attack with 2/2",
            "passed": (not chosen.declarations) if remove_aura else attacker in chosen.declarations,
            "selected_attackers": [c.name for c in chosen.declarations],
            "actual_legal_blockers": [c.name for c in legal_blockers],
            "candidates": [{"score": c.score, **c.description} for c in candidates]}


def main():
    results = [duplicate_pacifism(), duplicate_pacifism(True),
               attack_pacified_creature(), attack_pacified_creature(True)]
    root = Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("pacifism-decisions-%Y%m%d-%H%M%S-%f")
    root.mkdir(parents=True)
    (root / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    for row in results:
        print(("PASS" if row["passed"] else "FAIL"), row["case"],
              row.get("targets", row.get("selected_attackers")))
    print(root)


if __name__ == "__main__":
    main()
