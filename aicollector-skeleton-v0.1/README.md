# AICollector

**Read-only server knowledge collector for AI agents.**

AICollector runs periodically (via cron, default: every 2 hours) and builds a
versioned, JSON-based knowledge base of server state. It detects changes between
runs, maintains an history of snapshots, and exposes all data as structured
JSON files ready for consumption by an external AI agent.

> **Philosophy:** AICollector never judges, never modifies, never decides.
> It only observes, normalises, and reports.

---

## Quick start (development mode)

No installation required — just run the collector in dev mode:

```bash
# No deps required for the skeleton (stdlib + pydantic + pyyaml for full feature set)
pip install pydantic pyyaml

# Run in dev mode (paths redirected to ./data/)
python collector.py run --dev-mode

# Validate the environment
python collector.py check --dev-mode

# Dry run (no files written)
python collector.py run --dev-mode --dry-run
```

Dev mode automatically sets `AICOLLECTOR_ROOT=.` and redirects all FHS paths
to local directories:

| Production path | Dev mode path |
|---|---|
| `/var/lib/aicollector/knowledge/` | `./data/knowledge/` |
| `/var/log/aicollector/` | `./logs/` |
| `/run/aicollector/aicollector.lock` | `./data/aicollector.lock` |

---

## Directory structure

```
aicollector/
├── collector.py           # CLI entry point
├── VERSION                # Semantic version
├── pyproject.toml         # Project metadata + dependencies
├── config.yaml            # Default configuration
├── install.sh             # Idempotent production installer (root)
├── README.md               # This file
│
├── core/                  # Pipeline core — DO NOT modify by plugins
│   ├── base_collector.py     # ABC BaseCollector + dataclasses
│   ├── system_adapter.py      # Whitelisted system calls
│   ├── event_bus.py          # In-process pub/sub
│   ├── registry.py           # Dynamic @register_collector registry
│   ├── pipeline.py           # 4-phase orchestrator
│   ├── config_loader.py      # YAML + Pydantic validation
│   ├── schemas.py            # Dynamic schema registry
│   ├── logger.py             # NDJSON structured logging
│   ├── lockfile.py           # PID-based lock
│   ├── hashing.py            # SHA256 utilities
│   ├── diff_engine.py        # Recursive diff
│   ├── sanitizer.py         # Secret redaction
│   ├── knowledge_store.py   # knowledge/ history/ changes/ persistence
│   ├── self_diagnostic.py   # Startup environment checks
│   └── exceptions.py         # Hierarchical exception tree
│
├── collectors/            # Plugin collectors (auto-discovered)
│   ├── __init__.py
│   ├── base.py             # Shared import alias
│   ├── system.py           # Hostname, OS, kernel, uptime
│   ├── cpu.py              # CPU model, cores, flags, load
│   ├── ram.py              # Memory and swap
│   ├── storage.py          # Mounted filesystems (df)
│   ├── example_collector.py # Reference implementation template
│   └── ...                  # 15+ planned collectors
│
└── tests/                 # Test suite (V0.2+)
    ├── unit/
    ├── integration/
    └── collectors/
```

---

## Adding a new collector (V0.1+)

```python
# collectors/my_collector.py
from collectors.base import (
    BaseCollector, CollectorResult, SystemAdapter,
    register_collector, Severity,
)

@register_collector("my_collector")
class MyCollector(BaseCollector):
    name = "my_collector"
    schema_version = "1.0"
    collector_version = "1.0.0"
    requires_root = False
    timeout_seconds = 20

    def collect(self, system: SystemAdapter) -> CollectorResult:
        import time
        start = time.monotonic()
        errors = []

        # Use SystemAdapter for ALL system calls — never subprocess directly!
        result = system.run_command("df", ["-h"])
        data = {"raw": result.stdout}  # normalise in NORMALIZE phase

        return CollectorResult(
            data=data,
            errors=errors,
            execution_time_ms=(time.monotonic() - start) * 1000,
            raw_output=result.stdout,
        )
```

The collector is **auto-discovered** — no configuration change needed.

---

## Production installation

```bash
# Run as root on a clean Ubuntu 26.04 server
sudo bash install.sh

# Or with options:
sudo bash install.sh --user aicollector --cron "0 */3 * * *" --systemd-timer
```

`install.sh` will:
- Create the system user `aicollector`
- Set up the full FHS directory tree
- Copy files to `/opt/aicollector/`
- Install a cron job (default: every 2 hours)
- Optionally install a systemd timer

---

## Configuration

Edit `/etc/aicollector/config.yaml` to customise:

```yaml
logging_level: INFO          # DEBUG | INFO | WARNING | ERROR

retention:
  history_versions: 50        # Snapshots per collector
  changes_entries: 200        # Max change files (FIFO purge)
  logs_days: 30               # Log retention

scheduler:
  frequency_cron: "0 */2 * * *"  # Custom cron expression
  use_systemd_timer: false     # Use systemd timer instead

collectors:
  enabled: []                  # Empty = all; or list specific names
  disabled: []                 # Blacklist specific collectors
  timeout_seconds: 30           # Per-collector timeout
  root_required_behavior: skip  # skip | warn | fail
```

---

## Architecture

```
cron (every 2h) → collector.py run
    │
    ├── Phase 1 — COLLECT
    │     system_adapter.run_command() (whitelist) → CollectorResult
    │
    ├── Phase 2 — NORMALIZE
    │     schemas.py validation + SHA256 hash
    │
    ├── Phase 3 — COMPARE
    │     diff_engine.py: SHA256 comparison → changes/<timestamp>.json
    │
    └── Phase 4 — KNOWLEDGE BASE
          knowledge/*.json + manifest.json + history/ rotation
```

---

## Requirements

- Python ≥ 3.12
- Ubuntu 26.04 LTS (server)
- pydantic ≥ 2.0, pyyaml ≥ 6.0 (optional — for full schema validation)

---

## License

MIT
