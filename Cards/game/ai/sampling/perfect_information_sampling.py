"""Trusted bridge from memory-conditioned sampling to memory-free rule worlds.

Only this layer can access a sanitized snapshot's root evidence. A materialized
worker drops that recipe and retains only rule state and continuation routing.
"""
from game.ai.search.contracts import SampledRootMismatch
from game.ai.simulation.perfect_information_world import PerfectInformationWorld


class PerfectInformationSampler:
    def __init__(self, factory, root_key, observer):
        self.factory, self.root_key, self.observer = factory, root_key, observer

    def __call__(self, seed):
        from game.ai.sampling.current_position_worlds import (_PreparedWorld, _Snapshot)
        source = self.factory(seed)
        prepared = getattr(source, 'factory', None)
        if type(prepared) is not _PreparedWorld or type(prepared.snapshot) is not _Snapshot:
            raise SampledRootMismatch('PIMC requires a native current-position sampler')
        snapshot = prepared.snapshot
        if snapshot.root_information_key != self.root_key or snapshot.observer != self.observer:
            raise SampledRootMismatch('Sampler evidence belongs to a different root')
        model = getattr(source.policy_for, 'candidate_model', None)
        if getattr(model, 'rank_window', 0) or getattr(model, 'rank_windows', ()):
            raise SampledRootMismatch('PIMC does not use observation-based candidate ranking')
        owned = _PreparedWorld(snapshot, prepared.seed, reuse=True, position=True, player_memory=False)
        return PerfectInformationWorld(owned, source.policy_for, observer=self.observer,
            max_options=source.max_options, max_decisions=source.max_decisions, max_phases=source.max_phases)
