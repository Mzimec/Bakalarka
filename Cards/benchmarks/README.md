# Component benchmark

From the repository root in PowerShell:

```powershell
$env:PYTHONPATH=(Resolve-Path Cards).Path
.venv/Scripts/python.exe -m benchmarks.component_benchmark
```

Each invocation creates a separate timestamped JSON file under `results/`.
Existing reports are preserved. Schema version 2 adds:

- `environment.python`: interpreter version, executable, build and flags.
- `environment.instrumentation`: tracing/profiling hooks, loaded debugger
  modules and Python monitoring tools, captured before installing the component
  profiler. A loaded debugger module alone does not prove an attached debugger;
  a trace hook can also belong to coverage or another tool.
- `environment.system`: operating system, architecture, CPU description and
  logical CPU count. This does not measure CPU frequency or background load.
- `environment.code`: Git commit/branch, changes to Python source files and a
  SHA-256 fingerprint of Python files under `game`, `helper` and `benchmarks`,
  including untracked sources. The fingerprint excludes bytecode and JSON
  reports; it is not a fingerprint of dependencies or external data files.
- `configuration`: decks, agents, seeds, starting players and turn limit.
- `cpu_seconds` and `cpu_to_wall_ratio`: process CPU consumption over the same
  interval as `elapsed_seconds`. Each match also has `cpu_seconds` and
  `wall_seconds`; its existing `elapsed_seconds` remains engine-reported time.

Metadata collection and report writing are outside the timed interval. The
component profiler's overhead is included. A low CPU/wall ratio can indicate
waiting or contention, but neither this ratio nor matching metadata proves
identical machine conditions. Compare repeated, alternating old/new runs using
the same interpreter and instrumentation. Older reports lack this metadata;
their original environment cannot be reconstructed from these new fields.
