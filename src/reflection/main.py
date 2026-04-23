from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import coloredlogs
import schedule

from .config import load_config
from .mirror import mirror_all


def setup_logging(level: str) -> None:
    coloredlogs.install(
        level=level.upper(),
        fmt="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
        # Убираем цвет для WARNING/ERROR чтобы они читались на светлом фоне тоже
        level_styles={
            "debug":    {"color": "white", "faint": True},
            "info":     {"color": "cyan"},
            "warning":  {"color": "yellow", "bold": True},
            "error":    {"color": "red", "bold": True},
            "critical": {"color": "red", "bold": True, "background": "white"},
        },
        field_styles={
            "asctime":  {"color": "green"},
            "levelname": {"bold": True},
        },
        stream=sys.stdout,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="reflection",
        description="Mirror git repositories defined in a YAML config",
    )
    parser.add_argument(
        "-c", "--config",
        type=Path,
        default=Path("config.yaml"),
        metavar="FILE",
        help="Path to config file (default: config.toml)",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run continuously on schedule_interval from config",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without running git",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        metavar="SECONDS",
        help="Override schedule_interval from config (daemon mode only)",
    )
    return parser.parse_args(argv)


def run_sync(config_path: Path, dry_run: bool) -> bool:
    try:
        config = load_config(config_path)
    except (FileNotFoundError, ValueError) as exc:
        logging.getLogger(__name__).error("Config error: %s", exc)
        return False

    results = mirror_all(config, dry_run=dry_run)
    return all(r.success for r in results)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    # Ранняя инициализация логов с уровнем из конфига (если файл доступен)
    try:
        cfg = load_config(args.config)
        setup_logging(cfg.settings.log_level)
    except Exception:
        setup_logging("INFO")

    log = logging.getLogger(__name__)

    if args.daemon:
        interval = args.interval or cfg.settings.schedule_interval
        log.info("Daemon mode: syncing every %d seconds", interval)

        # Первый запуск сразу
        run_sync(args.config, args.dry_run)

        schedule.every(interval).seconds.do(run_sync, args.config, args.dry_run)

        while True:
            schedule.run_pending()
            time.sleep(10)
    else:
        success = run_sync(args.config, args.dry_run)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
