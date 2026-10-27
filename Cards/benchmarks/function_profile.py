"""Per-activation wall timings, including maxima unavailable in cProfile.

Generators/coroutines are measured in active resume-to-yield/return segments;
suspended time is excluded. Only the calling thread is instrumented.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
import sys
from time import perf_counter_ns


class FunctionProfile:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.stats = {}
        self.stack = []
        self.started_ns = 0
        self.elapsed_ns = 0
        self.unmatched_returns = 0

    def _event(self, frame, event, arg):
        now = perf_counter_ns()
        if frame.f_code.co_filename == __file__:
            return
        if event == "call":
            key = frame.f_code
            kind = "python"
        elif event == "c_call":
            key = (getattr(arg, "__module__", None),
                   getattr(arg, "__qualname__", getattr(arg, "__name__", str(type(arg)))))
            kind = "native"
        elif event in ("return", "c_return", "c_exception"):
            kind = "python" if event == "return" else "native"
            if not self.stack or self.stack[-1][0] is not frame or self.stack[-1][1] != kind:
                # The context manager was entered before tracing began.
                self.unmatched_returns += 1
                return
            _, _, record, start, children = self.stack.pop()
            elapsed = now - start
            own = max(0, elapsed - children)
            record["calls"] += 1
            record["total_ns"] += elapsed
            record["self_ns"] += own
            record["max_ns"] = max(record["max_ns"], elapsed)
            record["max_self_ns"] = max(record["max_self_ns"], own)
            if self.stack:
                self.stack[-1][4] += elapsed
            return
        else:
            return
        record = self.stats.get(key)
        if record is None:
            if kind == "python":
                path = Path(key.co_filename)
                try:
                    filename = path.resolve().relative_to(self.root).as_posix()
                    project = filename.startswith(("game/", "helper/", "benchmarks/"))
                except ValueError:
                    filename, project = str(path), False
                name, line = key.co_qualname, key.co_firstlineno
                generator = bool(key.co_flags & (0x20 | 0x80 | 0x200))
            else:
                filename, name, line, project, generator = "<native>", ".".join(p for p in key if p), 0, False, False
            record = self.stats[key] = dict(function=name, file=filename, line=line,
                kind=kind, project=project, generator_segments=generator,
                calls=0, total_ns=0, self_ns=0, max_ns=0, max_self_ns=0)
        self.stack.append([frame, kind, record, now, 0])

    def __enter__(self):
        if sys.getprofile() is not None:
            raise RuntimeError("A profiler is already active; run without another profiler.")
        self.started_ns = perf_counter_ns()
        sys.setprofile(self._event)
        return self

    def __exit__(self, *exc):
        sys.setprofile(None)
        self.elapsed_ns = perf_counter_ns() - self.started_ns

    def rows(self):
        rows = []
        for record in self.stats.values():
            if not record["calls"]:
                continue
            row = {k: v for k, v in record.items() if not k.endswith("_ns")}
            row.update(total_ms=record["total_ns"] / 1e6,
                       self_ms=record["self_ns"] / 1e6,
                       average_us=record["total_ns"] / record["calls"] / 1e3,
                       average_self_us=record["self_ns"] / record["calls"] / 1e3,
                       max_ms=record["max_ns"] / 1e6,
                       max_self_ms=record["max_self_ns"] / 1e6)
            rows.append(row)
        return sorted(rows, key=lambda r: r["self_ms"], reverse=True)

    def write(self, folder: Path, metadata=None):
        folder.mkdir(parents=True, exist_ok=False)
        rows = self.rows()
        notes = (
            "Wall time with profiling overhead; not normal game latency. Current thread only. "
            "Includes Python, standard library and reported native calls, tournament setup and summary writing; "
            "imports/deck loading before run_tournament and profiler export are excluded. "
            "Calls for generators/coroutines count active segments (each resume), not logical generator instances; "
            "their maxima/averages also describe segments and exclude suspended time. "
            "Inclusive totals include nested calls and recursive activations, so do not sum them. "
            "Self times subtract recorded children but still contain profiler overhead. "
            "Native operations without profile events cannot be separated from their caller. "
            "Maximum is one observed call/segment, not a percentile or a repeatable worst case."
        )
        payload = dict(elapsed_seconds=self.elapsed_ns / 1e9, notes=notes,
                       unmatched_returns=self.unmatched_returns,
                       unfinished_activations=len(self.stack), metadata=metadata, functions=rows)
        (folder / "functions.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        for name, selected in (("functions.csv", rows), ("project-functions.csv", [r for r in rows if r["project"]])):
            with (folder / name).open("w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else ["function"])
                writer.writeheader()
                writer.writerows(selected)
        lines = ["# Tournament function profile", "", notes, "",
                 f"Profiled interval: {self.elapsed_ns / 1e9:.3f} s; functions: {len(rows)}.", "",
                 "Full data: `functions.csv`, `project-functions.csv`, `functions.json`.", ""]
        for title, metric in (("Project functions by self time", "self_ms"),
                              ("Project functions by inclusive time", "total_ms"),
                              ("Project functions by maximum call time", "max_ms")):
            lines += [f"## {title}", "", "| Function | Calls/segments | Total ms | Self ms | Average us | Max ms |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
            selected = sorted((r for r in rows if r["project"]), key=lambda r: r[metric], reverse=True)[:50]
            for r in selected:
                label = f"{r['file']}:{r['line']} {r['function']}".replace("|", "\\|")
                lines.append(f"| {label} | {r['calls']} | {r['total_ms']:.3f} | {r['self_ms']:.3f} | {r['average_us']:.3f} | {r['max_ms']:.3f} |")
            lines.append("")
        (folder / "analysis.md").write_text("\n".join(lines), encoding="utf-8")
