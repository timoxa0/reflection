from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from pathlib import Path

import coloredlogs
import schedule

from .config import Config, load_config
from .mirror import mirror_all, mirror_one
from .server import init as server_init
from .server import run_server


def setup_logging(level: str) -> None:
    coloredlogs.install(
        level=level.upper(),
        fmt="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
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
        help="Path to config file (default: config.yaml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without running git",
    )
    return parser.parse_args(argv)


def _make_trigger(config: Config):
    """Возвращает trigger_one и trigger_all замыкания для webhook-сервера."""
    mirrors_dir = config.mirrors_path
    timeout = config.settings.timeout

    def trigger_one(repo_name: str) -> bool | None:
        repo = config.find_repo(repo_name)
        if repo is None:
            return None
        result = mirror_one(repo, mirrors_dir, timeout)
        return result.success

    def trigger_all() -> None:
        mirror_all(config)

    return trigger_one, trigger_all


def _setup_schedule(config: Config) -> None:
    mirrors_dir = config.mirrors_path
    timeout = config.settings.timeout
    global_interval = config.settings.schedule_interval

    for repo in config.repositories:
        interval = repo.schedule_interval or global_interval
        schedule.every(interval).seconds.do(mirror_one, repo, mirrors_dir, timeout)
        logging.getLogger(__name__).debug(
            "Scheduled [%s] every %ds", repo.name, interval
        )


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as exc:
        # Логгер ещё не настроен — используем stderr
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    setup_logging(config.settings.log_level)
    log = logging.getLogger(__name__)

    if args.dry_run:
        mirror_all(config, dry_run=True)
        return

    wh = config.settings.webhook

    # Webhook-сервер в отдельном потоке (если включён)
    if wh.enabled:
        trigger_one, trigger_all = _make_trigger(config)
        server_init(secret=wh.secret, trigger_one=trigger_one, trigger_all=trigger_all)
        t = threading.Thread(
            target=run_server, args=(wh.host, wh.port), daemon=True, name="webhook"
        )
        t.start()
        log.info("Webhook server listening on %s:%d", wh.host, wh.port)
    else:
        mirror_all(config)

    # Настраиваем расписание
    _setup_schedule(config)
    log.info("Scheduler ready. Press Ctrl+C to stop.")

    try:
        while True:
            schedule.run_pending()
            time.sleep(5)
    except KeyboardInterrupt:
        log.info("Stopped.")


if __name__ == "__main__":
    main()
