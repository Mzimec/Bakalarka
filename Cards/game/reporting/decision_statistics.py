"""Streaming decision latency statistics, mergeable across matches."""
from copy import deepcopy


def _empty():
    return {
        "count": 0, "errors": 0, "auto_pass_count": 0, "total_elapsed_ns": 0,
        "min_elapsed_ns": None, "max_elapsed_ns": None,
        "total_options_observed": 0, "known_size_count": 0,
        "total_known_size": 0,
    }


def _merge_bucket(target, source):
    for key in ("count", "errors", "total_elapsed_ns", "total_options_observed",
                "known_size_count", "total_known_size"):
        target[key] += source[key]
    target["auto_pass_count"] += source.get("auto_pass_count", 0)
    for key, operation in (("min_elapsed_ns", min), ("max_elapsed_ns", max)):
        values = [v for v in (target[key], source[key]) if v is not None]
        target[key] = operation(values) if values else None


def _averages(bucket):
    count = bucket["count"]
    return {
        **bucket,
        "average_elapsed_ms": bucket["total_elapsed_ns"] / count / 1_000_000 if count else None,
        "average_options_observed": bucket["total_options_observed"] / count if count else None,
        "average_known_size": (bucket["total_known_size"] / bucket["known_size_count"]
                               if bucket["known_size_count"] else None),
    }


def _option_bucket(count):
    for limit, label in ((0, "0"), (1, "1"), (10, "2-10"), (100, "11-100"),
                         (1000, "101-1000")):
        if count <= limit:
            return label
    return "1001+"


class DecisionStatistics:
    """Keep totals rather than all samples; means are weighted by decision count."""

    def __init__(self):
        self._players = {}

    def _entry(self, player_index, player, agent, mode):
        return self._players.setdefault((player_index, agent, mode), {
            "player_index": player_index, "player": player, "agent": agent, "mode": mode,
            "overall": _empty(), "by_request": {}, "by_options_observed": {},
        })

    def record(self, player_index, player, agent, mode, metrics):
        entry = self._entry(player_index, player, agent, mode)
        sample = {
            "auto_pass_count": int(metrics.get("auto_pass", False)),
            "count": 1, "errors": int(metrics["status"] != "success"),
            "total_elapsed_ns": metrics["elapsed_ns"],
            "min_elapsed_ns": metrics["elapsed_ns"], "max_elapsed_ns": metrics["elapsed_ns"],
            "total_options_observed": metrics["options_observed"],
            "known_size_count": int(metrics["option_space_size"] is not None),
            "total_known_size": metrics["option_space_size"] or 0,
        }
        _merge_bucket(entry["overall"], sample)
        request = metrics["request_type"]
        _merge_bucket(entry["by_request"].setdefault(request, _empty()), sample)
        # Group within request type: combat and priority choices have different costs.
        buckets = entry["by_options_observed"].setdefault(request, {})
        _merge_bucket(buckets.setdefault(_option_bucket(metrics["options_observed"]), _empty()), sample)

    def merge(self, summaries):
        for summary in summaries:
            entry = self._entry(summary["player_index"], summary["player"], summary["agent"], summary["mode"])
            _merge_bucket(entry["overall"], summary["overall"])
            for request, bucket in summary["by_request"].items():
                _merge_bucket(entry["by_request"].setdefault(request, _empty()), bucket)
            for request, buckets in summary["by_options_observed"].items():
                target = entry["by_options_observed"].setdefault(request, {})
                for label, bucket in buckets.items():
                    _merge_bucket(target.setdefault(label, _empty()), bucket)

    def summary(self):
        result = []
        for entry in self._players.values():
            item = deepcopy(entry)
            item["overall"] = _averages(item["overall"])
            item["by_request"] = {key: _averages(value) for key, value in item["by_request"].items()}
            item["by_options_observed"] = {
                request: {key: _averages(value) for key, value in buckets.items()}
                for request, buckets in item["by_options_observed"].items()
            }
            result.append(item)
        return result


def format_decision_statistics(summaries):
    if not summaries:
        return ["Decision timing: unavailable (not recorded)."]
    lines = ["Decision timing (wall time; includes option generation and input/model waits):"]
    for entry in summaries:
        overall = entry["overall"]
        lines.append(
            f"  {entry['player']} [{entry['agent']}, {entry['mode']}]: "
            f"{overall['count']} decisions, average {overall['average_elapsed_ms']:.3f} ms, "
            f"total {overall['total_elapsed_ns'] / 1e9:.3f} s, errors {overall['errors']}, "
            f"auto-passes {overall.get('auto_pass_count', 0)}"
        )
        for request, stats in entry["by_request"].items():
            known = stats["average_known_size"]
            size = f"{known:.2f} (known for {stats['known_size_count']} decisions)" if known is not None else "unknown"
            lines.append(
                f"    {request}: n={stats['count']}, average {stats['average_elapsed_ms']:.3f} ms; "
                f"observed options {stats['average_options_observed']:.2f}; space size {size}"
            )
            for label, bucket in entry["by_options_observed"].get(request, {}).items():
                lines.append(f"      {label} observed options: n={bucket['count']}, "
                             f"average {bucket['average_elapsed_ms']:.3f} ms")
    lines.append("  Observed options are consumed yields, not unique actions. Size refers to the policy-filtered space.")
    return lines
