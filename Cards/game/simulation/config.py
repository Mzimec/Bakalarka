"""Validated JSON match configuration shared by scripts and console entry points."""

from datetime import datetime
import json
from pathlib import Path

from game.ai.simple_agent import SimpleAgent
from game.ai.modular_agent import ModularAgent, CandidateGenerator, CombatPolicy, ParameterPolicy
from game.cards.decks import load_arena_starter, DeckList
from game.paths import RUNS_DIR
from game.simulation.match_runner import run_match


def known_keys(data, allowed, label):
    """!
    @brief Reject misspelled settings rather than silently changing an experiment.
    """
    if not isinstance(data, dict) or set(data) - set(allowed):
        raise ValueError(f"Invalid fields in {label}; allowed: {', '.join(allowed)}.")


def make_controller(config, *, seed, max_decisions):
    """!
    @brief Compose a player controller and its bounded decision policies.
    """
    known_keys(config, ("type", "candidates", "combat", "llm", "parameters", "payment", "candidate_limit"), "agent")
    kind = config.get("type", "modular")
    if kind in ("human", "simple") and set(config) - {"type"}:
        raise ValueError("Auxiliary policies require a modular or LLM agent.")
    if kind != "llm" and "llm" in config:
        raise ValueError("LLM settings require agent type llm.")
    if kind == "human":
        from game.console.demo_game import ConsoleDecisionMaker
        return ConsoleDecisionMaker(auto_pass=True)
    if kind == "simple":
        return SimpleAgent(max_decisions=max_decisions)
    if kind not in ("modular", "llm"):
        raise ValueError(f"Unknown agent type: {kind}.")
    proposal = config.get("candidates", {})
    combat = config.get("combat", {})
    known_keys(proposal, ("target_limit", "cost_limit", "keep_per_ability"), "candidates")
    known_keys(combat, ("max_assignments",), "combat")
    parameters = config.get("parameters", {})
    known_keys(parameters, ("max_x",), "parameters")
    from game.ai.mana_solver import PoolManaSolver, SourceActivatingManaSolver
    payment = config.get("payment", "automatic")
    if payment not in ("automatic", "pool"):
        raise ValueError("Payment must be automatic or pool.")
    solver = SourceActivatingManaSolver() if payment == "automatic" else PoolManaSolver()
    selector = None
    if kind == "llm":
        from game.ai.llm_policy import OllamaSelector
        llm = config.get("llm", {})
        known_keys(llm, ("model", "endpoint", "timeout", "max_requests"), "llm")
        if "model" not in llm:
            raise ValueError("LLM agent requires llm.model.")
        selector = OllamaSelector(**llm, seed=seed)
    return ModularAgent(candidates=CandidateGenerator(**proposal, mana_solver=solver,
                                                     parameters=ParameterPolicy(**parameters)),
                        combat=CombatPolicy(**combat), selector=selector,
                        candidate_limit=config.get("candidate_limit", 32),
                        max_decisions=max_decisions)


def run_config(path, *, output=None, seed=None):
    """!
    @brief Resolve two decklists and controllers, then use the normal logged runner.

    Deck paths and configured output paths are relative to the configuration file.
    """
    path = Path(path).resolve()
    config = json.loads(path.read_text(encoding="utf-8"))
    known_keys(config, ("seed", "starting_player", "max_turns", "max_decisions",
                        "players", "output"), "match")
    players = config.get("players")
    if not isinstance(players, list) or len(players) != 2:
        raise ValueError("Configure exactly two players.")
    seed = config.get("seed", 1) if seed is None else seed
    if type(seed) is not int:
        raise ValueError("Seed must be an integer.")
    limits = {key: config.get(key, default) for key, default in
              (("max_turns", 100), ("max_decisions", 10000))}
    if any(type(v) is not int or v < 1 for v in limits.values()):
        raise ValueError("Run limits must be positive integers.")
    starting = config.get("starting_player", 0)
    if type(starting) is not int or starting not in (0, 1):
        raise ValueError("Starting player must be 0 or 1.")
    decks, controllers, names = [], [], []
    for index, player in enumerate(players):
        known_keys(player, ("name", "deck", "agent"), "player")
        deck = player.get("deck")
        if isinstance(deck, str):
            decks.append(load_arena_starter(deck))
        else:
            known_keys(deck, ("file",), "deck")
            if not isinstance(deck.get("file"), str):
                raise ValueError("Deck file must be a path string.")
            deck_path = path.parent / deck["file"]
            decks.append(DeckList.from_arena(deck_path.read_text(encoding="utf-8"),
                                            deck_path.stem))
        names.append(player.get("name", f"Player {index + 1}"))
        if not isinstance(names[-1], str) or not names[-1].strip():
            raise ValueError("Player names must be nonempty strings.")
        controllers.append(make_controller(
            player.get("agent", {}), seed=seed + index,
            max_decisions=limits["max_decisions"],
        ))
    if len(set(names)) != 2:
        raise ValueError("Player names must be distinct.")
    # Validate deck content before opening any experiment output.
    from game.cards.catalog import game_catalog
    for deck in decks:
        deck.resolve(game_catalog())
    if output is None:
        output = (path.parent / config["output"] if "output" in config
                  else RUNS_DIR / datetime.now().strftime("configured-%Y%m%d-%H%M%S-%f.jsonl"))
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    return run_match(
        tuple(deck.name for deck in decks), output, seed=seed,
        starting_player=starting, controllers=tuple(controllers),
        names=tuple(names), decklists=tuple(decks),
        metadata={"configuration": config, "effective_seed": seed},
        **limits,
    )
