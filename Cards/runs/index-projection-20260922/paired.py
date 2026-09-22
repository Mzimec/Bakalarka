"""Interleaved full-game comparison, with CPU and wall time in one process."""
import json
from pathlib import Path
from time import process_time
from game.game_state.card_snapshot_cache import CardSnapshotCache
from game.game_state.registers.card_register import CardRegister
from game.simulation.match_runner import run_match
from game.ai.simple_agent import SimpleAgent
from game.ai.modular_agent import ModularAgent
original=CardSnapshotCache.index_characteristics
original_layer=CardRegister.mark_layer_changed
reference=lambda self,state,card,reader:reader(state,card)
rows=[]
for i,enabled in enumerate((False,True,True,False,True,False)):
    CardSnapshotCache.index_characteristics=original if enabled else reference
    CardRegister.mark_layer_changed=original_layer if enabled else CardRegister.mark_changed
    started=process_time()
    r=run_match(("white","white"),None,seed=1,starting_player=0,
                controllers=(ModularAgent(),SimpleAgent()))
    row={"cached":enabled,"cpu":process_time()-started,"seconds":r.elapsed_seconds,
         "status":r.status,"turns":r.turns,"decisions":r.decisions,"winner":r.winner_index}
    rows.append(row)
    print(json.dumps(row),flush=True)
    Path(__file__).with_name("paired-final.json").write_text(json.dumps(rows,indent=2),encoding="utf-8")
CardSnapshotCache.index_characteristics=original

CardRegister.mark_layer_changed=original_layer
