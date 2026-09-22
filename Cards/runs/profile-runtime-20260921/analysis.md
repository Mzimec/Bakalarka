# Runtime profiling: white vs white

Seed 1621711597, ModularAgent vs SimpleAgent, default policies, no match log files.
All three runs finished with a win on turn 21 and 515 agent-recorded decisions.
The observer sees 531 typed decisions; some inherited handlers do not increment the agent counter.

Without profiling: 13.908 s total, 1.456 s in measured decisions.
With scoped timers: 16.336 s. Profiling overhead means these are diagnostic timings.
cProfile is substantially more intrusive: 45.820 s for the same game. Do not compare its seconds directly with tournament timings.

## Disjoint scoped timings

Nested measured scopes are subtracted from parents; rows below can be added.
All categories except Decisions exclude work done inside DecisionMaker.decide.

| Category | Seconds | Share of profiled run |
|---|---:|---:|
| card_snapshots | 7.026 | 43.0% |
| index_updates | 4.966 | 30.4% |
| continuous_effects | 0.763 | 4.7% |
| state_based_actions | 0.591 | 3.6% |
| rollback_snapshots | 0.406 | 2.5% |
| other | 0.396 | 2.4% |
| trigger_rosters | 0.286 | 1.7% |
| operation_execution | 0.148 | 0.9% |
| event_matching | 0.116 | 0.7% |
| All decision work | 1.640 | 10.0% |

## Evidence and interpretation

- OperationExecutor.execute_batch takes a complete card LKI snapshot before every batch. cProfile counted 317 complete captures and 38,199 single-card captures, including cards in library/hand. This is runtime event correctness work, not file logging.
- Card characteristic evaluation is the common underlying expense: 805,635 get_stat / modifier-pipeline calls. _get_modifier_pipeline collects modifier sources, groups layers and orders dependencies anew for each read. The cProfile cumulative cost overlaps both snapshots and index updates; do not add it to their times.
- CardRegister.index_values ran 20,835 times, rebuilding all index memberships for each dirty card. Continuous-effect evaluation marks all cards dirty between layers and at the end, then synchronizes indexes. Query entry points can consequently charge pending global refresh work to apparently simple lookups such as is_game_over.
- 175,170 refresh_continuous_effects calls in cProfile include recursive and clean-graph guard exits; this is NOT 175,170 full graph rebuilds. Scoped index time is separated from effect-refresh time.
- Rollback checkpointing is secondary in this scenario: 20 checkpoints and roughly 0.406 s including watch_operations in the scoped run. It is not the primary bottleneck.
- No per-match files were written, so JSON/text serialization to disk cannot explain these dominant costs. Other decks, continuous effects and larger combat states can change this distribution.

## Suggested optimization order (not implemented)

1. Share evaluated card characteristics within a validated state/layer revision so LKI and index extraction reuse the same work. Invalidate for counters, zone/control/attachment/definition changes, continuous-effect dependencies and layer ceilings; retain a conservative fallback for dynamic/custom rules.
2. Reduce LKI capture to explicitly required objects for supported operations/triggers, or reuse immutable snapshots of unchanged cards. Keep full pre-batch capture for unknown dependencies and preserve simultaneous-event semantics.
3. Track dirty index fields/dependencies instead of recomputing all keys for every marked card. Avoid full-card reindexing between layers when the affected dependency set is provably smaller.
4. Only then consider narrower rollback snapshots; custom cost rollback must retain current identity/state guarantees.

## Reproduction

From the repository root (PowerShell):
```powershell
$env:PYTHONPATH=(Resolve-Path Cards).Path
.venv/Scripts/python.exe Cards/runs/profile-runtime-20260921/measure_runtime.py
```

Artifacts: whole-game.prof / whole-game.txt (cProfile), runtime-breakdown.json (exclusive scopes), unprofiled-result.json (reference run).
Only profiling artifacts were added; engine implementation was not changed during this investigation.
