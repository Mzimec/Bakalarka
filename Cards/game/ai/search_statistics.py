"""Live-only search coverage and latency; sampled replay never records here."""
from collections import Counter
from math import ceil
from statistics import median


class SearchStatistics:
    """!
    @brief Accumulate detached decision telemetry over a controller session.
    """
    def __init__(self):
        self.reasons = Counter()
        self.history_mismatches = Counter()
        self.latencies = []
        self.attempts = self.fallbacks = self.iterations = self.rejections = 0
        self.search_latencies = []
        self.cpu_latencies = []
        self.stage_ms = Counter()
        self.by_request = {}
        self.reuse_reasons = Counter()

    def record(self, detail, elapsed_ms):
        """!
        @brief Accumulate a completed action or decision in the owner statistics.
        """
        self.reasons[detail["reason"]] += 1
        self.latencies.append(elapsed_ms)
        if "cpu_ms" in detail:
            self.cpu_latencies.append(detail["cpu_ms"])
        self.attempts += detail["used_search"]
        self.fallbacks += detail["used_fallback"]
        metrics = (detail.get("search") or {}).get("metrics", {})
        self.iterations += metrics.get("iterations_completed", 0)
        self.rejections += metrics.get("history_rejected_samples", 0)
        self.history_mismatches.update(dict(metrics.get("history_mismatch_reasons", ())))
        self.reuse_reasons.update(dict(metrics.get('reuse_reasons',())))
        self.stage_ms.update(detail.get("stage_ms", {}))
        if detail["used_search"]:
            self.search_latencies.append(elapsed_ms)
        kind = detail.get("request_kind", "unknown")
        row = self.by_request.setdefault(kind, {"decisions": 0, "options_total": 0, "options_max": 0,
            "search_attempts": 0, "fallbacks": 0, "root_actions_visited": 0,
            "simulator_advance_calls": 0, "transition_cache_hits": 0,
            "trajectory_runs": 0,
            "hierarchy_nodes": 0, "hierarchy_selection_steps": 0,
            "forced_transitions": 0,
            "forced_chain_hits": 0, "forced_chain_steps": 0,
            'native_forced_steps': 0, 'candidate_expansions': 0, 'candidate_cache_hits': 0, 'structured_value_hits': 0,
            'parallel_searches': 0, 'parallel_workers_max': 1, 'parallel_worker_cpu_ms': 0.0,
            'parallel_setup_ms': 0.0, 'parallel_payload_bytes': 0,
            "projection_cache_hits": 0, "projection_cache_entries": 0,
            "candidate_orderings": 0, "eligible_actions": 0,
            "reused_information_nodes": 0, "reused_root_visits": 0,
            'reused_structure_nodes':0, 'reused_prior_nodes':0, 'reuse_pruned_nodes':0,'reuse_charged_bytes':0,
            'encoding_cache_hits':0,'neural_cache_hits':0,'encoding_ms':0.,'inference_ms':0.,
            'checkpoint_copies':0,'checkpoint_evictions':0,'checkpoint_capture_ms':0.,
            'checkpoint_copy_rejections':0,
            "checkpoint_hits": 0, "checkpoint_skipped_decisions": 0,
            "world_materializations": 0, "world_restores": 0, "reasons": Counter()})
        row["decisions"] += 1
        row["options_total"] += detail["options"]
        row["options_max"] = max(row["options_max"], detail["options"])
        row["search_attempts"] += detail["used_search"]
        row["fallbacks"] += detail["used_fallback"]
        row["reasons"][detail["reason"]] += 1
        row['parallel_searches'] += int(metrics.get('parallel_backend', 'serial') != 'serial')
        row['parallel_workers_max'] = max(row['parallel_workers_max'], metrics.get('parallel_workers', 1))
        for name in ('parallel_worker_cpu_ms', 'parallel_setup_ms', 'parallel_payload_bytes'):
            row[name] += metrics.get(name, 0)
        for name in ("root_actions_visited", "simulator_advance_calls", "transition_cache_hits",
                     "trajectory_runs",
                     "hierarchy_nodes", "hierarchy_selection_steps",
                     "forced_transitions",
                     "forced_chain_hits", "forced_chain_steps", "projection_cache_hits", "projection_cache_entries",
                     'native_forced_steps', 'candidate_expansions', 'candidate_cache_hits', 'structured_value_hits',
                     "candidate_orderings", "eligible_actions", "reused_information_nodes", "reused_root_visits",
                     'reused_structure_nodes','reused_prior_nodes','reuse_pruned_nodes','reuse_charged_bytes',
                     'encoding_cache_hits','neural_cache_hits','encoding_ms','inference_ms',
                     'checkpoint_copies','checkpoint_evictions','checkpoint_capture_ms',
                     'checkpoint_copy_rejections',
                     "checkpoint_hits", "checkpoint_skipped_decisions", "world_materializations", "world_restores"):
            row[name] += metrics.get(name, 0)

    def summary(self):
        """!
        @brief Return detached counters and current configured resource limits.
        """
        times = sorted(self.latencies)
        search_times = sorted(self.search_latencies)
        cpu_times = sorted(self.cpu_latencies)
        return {
            "decisions": len(times), "search_attempts": self.attempts,
            "searched_decisions": self.reasons["searched"], "fallback_decisions": self.fallbacks,
            "forced_decisions": self.reasons["forced_choice"], "reasons": dict(self.reasons),
            "iterations_completed": self.iterations, "history_rejected_samples": self.rejections,
            "history_mismatch_reasons": dict(self.history_mismatches),
            'reuse_reasons':dict(self.reuse_reasons),
            "decision_ms_total": sum(times), "decision_ms_median": median(times) if times else 0,
            "decision_ms_p95": times[ceil(0.95 * len(times)) - 1] if times else 0,
            "decision_ms_max": max(times, default=0),
            "cpu_timed_decisions": len(cpu_times),
            "decision_cpu_ms_total": sum(cpu_times),
            "decision_cpu_ms_median": median(cpu_times) if cpu_times else 0,
            "decision_cpu_ms_p95": cpu_times[ceil(.95 * len(cpu_times)) - 1] if cpu_times else 0,
            "decision_cpu_ms_max": max(cpu_times, default=0),
            "search_decision_ms_median": median(search_times) if search_times else 0,
            "search_decision_ms_p95": search_times[ceil(.95 * len(search_times)) - 1] if search_times else 0,
            "stage_ms_total": dict(self.stage_ms),
            "by_request": {kind: {**row, "reasons": dict(row["reasons"]),
                "options_mean": row["options_total"] / row["decisions"]} for kind, row in self.by_request.items()},
        }
