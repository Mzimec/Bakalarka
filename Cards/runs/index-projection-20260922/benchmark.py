"""Compare the same 20 games; run separately, with no tests/profiler competing."""
import json
import sys
from pathlib import Path
from game.simulation.match_runner import run_match
from game.ai.simple_agent import SimpleAgent
from game.ai.modular_agent import ModularAgent
from game.game_state import card_snapshot_cache
from game.game_state.registers.card_register import CardRegister
out=Path(__file__).parent / (sys.argv[1] + ".json")
results=[]
for seed in range(1,11):
    for start in (0,1):
        r=run_match(("white","white"),None,seed=seed,starting_player=start,max_turns=100,
                    controllers=(ModularAgent(),SimpleAgent()))
        results.append({"seed":seed,"start":start,"seconds":r.elapsed_seconds,
                        "status":r.status,"turns":r.turns,"decisions":r.decisions,
                        "winner":r.winner_index,"error":r.error})
        out.write_text(json.dumps(results,indent=2),encoding="utf-8")
        print(json.dumps(results[-1]),flush=True)
print("TOTAL",sum(r["seconds"] for r in results),flush=True)
