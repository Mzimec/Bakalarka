# Partial card index optimization

The same 20 white-vs-white games (seeds 1-10, both starts): 181.864 s before, 155.194 s final (14.7% reduction).
All winners, statuses, turn counts and decision counts match.

Interleaved runs of seed 1/start 0 in a single process: mean CPU time 10.141 s with full index recomputation vs 5.177 s optimized (48.9% reduction). Three samples per configuration in off/on/on/off/on/off order.

The sequential intermediate implementation took 241.455 s; it was refined further, and timings also varied substantially during this session. The final short-game CPU result must not be presented as the whole-tournament speedup.

Implementation:
- Cache only the derived index projection, independently of full event LKI. Runtime flags and mana value remain live.
- At layer boundaries, avoid reindexing clean unmodified cards whose invariant projection is already current. Never drop queued mutation updates.
- Preserve full evaluation for custom/modified cards, mutable permissions/mana and stack X.
- Give entry projections their own cache so cloned identities cannot accumulate in the live state.

Validation includes mutation, layer, rollback and entry-projection regression tests; a seeded full-vs-optimized gameplay-log comparison excludes only timing/result records.

Run benchmark.py LABEL with PYTHONPATH=Cards to reproduce the 20-game workload. paired.py runs the interleaved comparison. Raw measurements and comparison.json are in this directory.
