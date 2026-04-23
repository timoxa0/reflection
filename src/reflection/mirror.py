from __future__ import annotations

import logging
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from .config import DEFAULT_PUSH_REFS, Config, Remote, Repository

logger = logging.getLogger(__name__)

# Per-repo locks: предотвращают одновременный запуск зеркалирования одного репо
_repo_locks: dict[str, threading.Lock] = {}
_locks_mu = threading.Lock()


def _repo_lock(name: str) -> threading.Lock:
    with _locks_mu:
        if name not in _repo_locks:
            _repo_locks[name] = threading.Lock()
        return _repo_locks[name]


# ── Результаты ───────────────────────────────────────────────────────────────

@dataclass
class PushResult:
    destination: str
    success: bool
    error: Optional[str] = None


@dataclass
class RepoResult:
    name: str
    fetch_ok: bool
    skipped: bool = False
    pushes: list[PushResult] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.fetch_ok and all(p.success for p in self.pushes)

    @property
    def failed_pushes(self) -> list[PushResult]:
        return [p for p in self.pushes if not p.success]


# ── Git helpers ───────────────────────────────────────────────────────────────

def _build_env(remote: Remote) -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"

    if remote.ssh_key:
        env["GIT_SSH_COMMAND"] = (
            f"ssh -i {remote.ssh_key}"
            " -o StrictHostKeyChecking=no"
            " -o BatchMode=yes"
        )

    if remote.pat:
        p = urlparse(remote.url)
        port_str = f":{p.port}" if p.port else ""
        plain = f"{p.scheme}://{p.hostname}{port_str}/"
        authed = f"{p.scheme}://oauth2:{remote.pat}@{p.hostname}{port_str}/"
        idx = int(env.get("GIT_CONFIG_COUNT", "0"))
        env["GIT_CONFIG_COUNT"] = str(idx + 1)
        env[f"GIT_CONFIG_KEY_{idx}"] = f"url.{authed}.insteadOf"
        env[f"GIT_CONFIG_VALUE_{idx}"] = plain

    if not remote.ssl_verify:
        env["GIT_SSL_NO_VERIFY"] = "true"

    return env


def _git(
    args: list[str],
    cwd: Optional[Path],
    env: dict[str, str],
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        timeout=timeout,
        capture_output=True,
        text=True,
    )


# ── Основная логика ───────────────────────────────────────────────────────────

def _fetch(repo: Repository, local: Path, timeout: int) -> tuple[bool, Optional[str]]:
    env = _build_env(repo.source)

    try:
        if local.exists():
            logger.debug("[%s] fetch: remote update from %s", repo.name, repo.source.url)
            r = _git(["remote", "update", "--prune"], cwd=local, env=env, timeout=timeout)
        else:
            logger.debug("[%s] fetch: cloning mirror from %s", repo.name, repo.source.url)
            local.parent.mkdir(parents=True, exist_ok=True)
            r = _git(
                ["clone", "--mirror", repo.source.url, str(local)],
                cwd=None, env=env, timeout=timeout,
            )

        if r.returncode != 0:
            return False, (r.stderr or r.stdout).strip()
        return True, None

    except subprocess.TimeoutExpired:
        return False, f"fetch timed out after {timeout}s"
    except Exception as exc:
        return False, str(exc)


def _push(local: Path, dest: Remote, dest_name: str, timeout: int) -> PushResult:
    env = _build_env(dest)

    try:
        refspecs = [*DEFAULT_PUSH_REFS, *dest.push_refs]
        logger.debug("  -> push to '%s' (%s) refs: %s", dest_name, dest.url, refspecs)
        r = _git(
            ["push", "--prune", dest.url, *refspecs],
            cwd=local, env=env, timeout=timeout,
        )

        if r.returncode != 0:
            error = (r.stderr or r.stdout).strip()
            return PushResult(destination=dest_name, success=False, error=error)

        return PushResult(destination=dest_name, success=True)

    except subprocess.TimeoutExpired:
        return PushResult(destination=dest_name, success=False,
                          error=f"push timed out after {timeout}s")
    except Exception as exc:
        return PushResult(destination=dest_name, success=False, error=str(exc))


def mirror_one(repo: Repository, mirrors_dir: Path, timeout: int) -> RepoResult:
    lock = _repo_lock(repo.name)
    if not lock.acquire(blocking=False):
        logger.warning("[%s] already running, skipping", repo.name)
        return RepoResult(name=repo.name, fetch_ok=False, skipped=True)

    try:
        local = mirrors_dir / repo.name

        fetch_ok, fetch_err = _fetch(repo, local, timeout)
        if not fetch_ok:
            logger.error("[%s] fetch failed: %s", repo.name, fetch_err)
            return RepoResult(name=repo.name, fetch_ok=False)

        logger.info("[%s] fetched from source", repo.name)

        pushes: list[PushResult] = []
        with ThreadPoolExecutor(max_workers=len(repo.destinations)) as pool:
            futures = {
                pool.submit(_push, local, dest, dest.url, timeout): dest
                for dest in repo.destinations
            }
            for future in as_completed(futures):
                pushes.append(future.result())

        for p in pushes:
            if p.success:
                logger.info("[%s] pushed -> %s", repo.name, p.destination)
            else:
                logger.error("[%s] push failed -> %s: %s", repo.name, p.destination, p.error)

        return RepoResult(name=repo.name, fetch_ok=True, pushes=pushes)
    finally:
        lock.release()


def mirror_all(config: Config, dry_run: bool = False) -> list[RepoResult]:
    repos = config.repositories
    mirrors_dir = config.mirrors_path
    timeout = config.settings.timeout
    workers = config.settings.workers

    total_dest = sum(len(r.destinations) for r in repos)
    logger.info(
        "Sync started: %d repos × up to %d destinations each (%d total pushes)",
        len(repos), max((len(r.destinations) for r in repos), default=0), total_dest,
    )

    if dry_run:
        for repo in repos:
            dest_names = [d.url for d in repo.destinations]
            logger.info("[dry-run] %s: %s -> %s", repo.name, repo.source.url, dest_names)
        return [
            RepoResult(
                name=r.name, fetch_ok=True,
                pushes=[PushResult(destination=d.url, success=True) for d in r.destinations],
            )
            for r in repos
        ]

    results: list[RepoResult] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(mirror_one, repo, mirrors_dir, timeout): repo
            for repo in repos
        }
        for future in as_completed(futures):
            results.append(future.result())

    ok = sum(1 for r in results if r.success)
    skipped = sum(1 for r in results if r.skipped)
    fail = len(results) - ok - skipped
    logger.info("Sync complete: %d ok, %d skipped, %d failed", ok, skipped, fail)
    for r in results:
        if not r.success and not r.skipped:
            for p in r.failed_pushes:
                logger.warning("  failed push: %s -> %s", r.name, p.destination)

    return results
