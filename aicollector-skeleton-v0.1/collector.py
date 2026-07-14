#!/usr/bin/env python3
"""AICollector — Entry point for the knowledge collection pipeline.

Usage:
    python collector.py [options] run [--dry-run]
    python collector.py [options] check
    python collector.py [options] check-lock
    python collector.py version

Options:
    -c, --config PATH     Path to config.yaml (default: /etc/aicollector/config.yaml)
    -d, --dev-mode        Enable development mode (redirect paths to ./data/)
    -h, --help            Show this message

Environment:
    AICOLLECTOR_ROOT      Override all FHS paths with a custom prefix.
                          When set, the tool runs in dev mode automatically.
                          Example: AICOLLECTOR_ROOT=/tmp/test-run python collector.py run
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid
from pathlib import Path

# Ensure the local package is importable
_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

from core.exceptions import AICollectorError
from core.config_loader import AICollectorConfig
from core.lockfile import LockfileManager
from core.self_diagnostic import SelfDiagnostic
from core.event_bus import EventBus
from core.logger import setup_logging
from core.knowledge_store import KnowledgeStore
from core.pipeline import Pipeline


_VERSION_FILE = _ROOT / "VERSION"


def _load_version() -> str:
    """Return the current project version from VERSION file."""
    try:
        return _VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "1.0.0"


def _is_dev_mode(args: argparse.Namespace) -> bool:
    """Return True if running in development mode."""
    if args.dev_mode or "AICOLLECTOR_ROOT" in os.environ:
        return True
    return not Path("/opt/aicollector").exists()


def _resolve_config_path(args: argparse.Namespace) -> Path | None:
    """Resolve the config.yaml path according to mode and arguments."""
    if args.config:
        return Path(args.config)

    env_root = os.environ.get("AICOLLECTOR_ROOT")
    if env_root:
        return Path(env_root) / "config.yaml"

    if _is_dev_mode(args):
        dev_root = Path(os.environ.get("AICOLLECTOR_ROOT", ".")).resolve()
        local_cfg = dev_root / "config.yaml"
        if local_cfg.exists():
            return local_cfg

    prod_cfg = Path("/etc/aicollector/config.yaml")
    if prod_cfg.exists():
        return prod_cfg
    return None


def _resolve_data_dirs(config: AICollectorConfig) -> tuple[Path, Path, Path, Path]:
    """Return (base_dir, knowledge_dir, log_dir, cache_dir)."""
    base = config.paths.base_dir
    knowledge = base / config.paths.knowledge_subdir
    log = config.paths.log_dir
    cache = config.paths.cache_dir
    return base, knowledge, log, cache


def _load_config(args: argparse.Namespace) -> AICollectorConfig:
    """Load configuration with explicit error boundary."""
    config_path = _resolve_config_path(args)
    if config_path and not config_path.exists():
        raise AICollectorError(f"Config file not found: {config_path}", exit_code=10)

    if config_path:
        return AICollectorConfig.from_yaml(config_path)
    return AICollectorConfig()


def cmd_run(args: argparse.Namespace) -> int:
    """Execute a collection run."""
    try:
        config = _load_config(args)
    except AICollectorError as exc:
        print(f"[FATAL] Config loading failed: {exc}", file=sys.stderr)
        return getattr(exc, "exit_code", 10)

    base, _, log_dir, _ = _resolve_data_dirs(config)

    # Setup structured logger
    dev_active = _is_dev_mode(args)
    logger = setup_logging(log_dir, level=config.logging_level)
    logger.info("AICollector starting", extra={"mode": "development" if dev_active else "production"})
    logger.info("Config resolved: %s", _resolve_config_path(args) or "<defaults>")

    # --- Run diagnostics ---
    diag = SelfDiagnostic(base, log_dir)
    try:
        diag.run()
    except AICollectorError as exc:
        logger.critical("Diagnostic failed: %s", exc)
        return getattr(exc, "exit_code", 1)

    # --- Lockfile handling ---
    run_id = str(uuid.uuid4())
    lock_path = config.paths.lockfile_path
    if dev_active:
        # In development mode, bypass restricted FHS paths if not writable
        if lock_path.parent != Path("/run/aicollector"):
            lock_path = base / "aicollector.lock"

    lock_mgr = LockfileManager(lock_path)
    try:
        lock_mgr.acquire(run_id)
    except AICollectorError as exc:
        logger.critical("Lockfile error: %s", exc)
        return 30

    # Ensure lock is safely released post-execution
    try:
        event_bus = EventBus()
        ks = KnowledgeStore(base)
        pipeline = Pipeline(config, event_bus, ks)

        if args.dry_run:
            logger.info("DRY RUN — execution completed without files written")
        else:
            stats = pipeline.run()
            logger.info("Run completed successfully", extra={"stats": stats})
    except AICollectorError as exc:
        logger.critical("Pipeline error: %s", exc)
        return getattr(exc, "exit_code", 20)
    finally:
        lock_mgr.release()

    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Run self-diagnostic checks without a full collection."""
    try:
        config = _load_config(args)
    except AICollectorError as exc:
        print(f"[FATAL] Config loading failed: {exc}", file=sys.stderr)
        return getattr(exc, "exit_code", 10)

    base, _, log_dir, _ = _resolve_data_dirs(config)
    diag = SelfDiagnostic(base, log_dir)
    try:
        report = diag.run()
        print(f"[OK] Python {report.python_version} (>= 3.12)")
        print(f"[OK] Platform: {'Linux' if report.platform_ok else 'WARNING'}")
        print(f"[OK] Directories writable: {report.directories_ok}")
        if report.disk_space_mb:
            print(f"[INFO] Disk space free: {report.disk_space_mb:.1f} MB")
        for warning in report.warnings:
            print(f"[WARN] {warning}")
        return 0
    except AICollectorError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return getattr(exc, "exit_code", 1)


def cmd_check_lock(args: argparse.Namespace) -> int:
    """Check whether a run is currently locked."""
    try:
        config = _load_config(args)
    except AICollectorError:
        config = AICollectorConfig()

    lock_path = config.paths.lockfile_path
    mgr = LockfileManager(lock_path)
    pid = mgr._read_pid()
    if pid and mgr._process_alive(pid):
        print(f"Locked by PID {pid}", file=sys.stderr)
        return 30
    print("Not locked")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="collector.py",
        description="AICollector — Server knowledge collector for AI agents.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    
    # Global options shared across commands
    parser.add_argument(
        "-c", "--config", type=str, default=None,
        help="Path to config.yaml (default: /etc/aicollector/config.yaml)"
    )
    parser.add_argument(
        "-d", "--dev-mode", action="store_true",
        help="Enable development mode (redirect paths to ./data/)"
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # Subcommands
    run_parser = sub.add_parser("run", help="Run the full collection pipeline")
    run_parser.add_argument(
        "--dry-run", action="store_true",
        help="Execute without writing any files"
    )

    sub.add_parser("check", help="Run environment diagnostics")
    sub.add_parser("check-lock", help="Check if a run is in progress")
    sub.add_parser("version", help="Print version string")

    args = parser.parse_args(argv)

    # Inject dev-mode flag into environment if requested or auto-detected
    if _is_dev_mode(args):
        os.environ.setdefault("AICOLLECTOR_ROOT", ".")

    match args.command:
        case "run":
            return cmd_run(args)
        case "check":
            return cmd_check(args)
        case "check-lock":
            return cmd_check_lock(args)
        case "version":
            print(_load_version())
            return 0
        case _:
            parser.print_help()
            return 0


if __name__ == "__main__":
    sys.exit(main())
