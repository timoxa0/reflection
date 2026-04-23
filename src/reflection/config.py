from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


DEFAULT_PUSH_REFS = (
    "+refs/heads/*:refs/heads/*",
    "+refs/tags/*:refs/tags/*",
)


@dataclass
class Remote:
    """Общая модель для source и destination."""
    url: str
    ssh_key: Optional[str] = None
    ssl_verify: bool = True
    # Дополнительные refspecs поверх дефолтных (heads + tags)
    push_refs: list[str] = field(default_factory=list)


@dataclass
class Repository:
    name: str
    source: Remote
    destinations: list[Remote] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Repository 'name' cannot be empty")
        if not self.destinations:
            raise ValueError(f"Repository '{self.name}': at least one destination required")


@dataclass
class Settings:
    mirrors_dir: str = "/mirrors"
    log_level: str = "INFO"
    schedule_interval: int = 3600
    timeout: int = 300
    workers: int = 4

    def __post_init__(self) -> None:
        if self.schedule_interval < 60:
            raise ValueError("schedule_interval must be >= 60 seconds")
        if self.workers < 1:
            raise ValueError("workers must be >= 1")


@dataclass
class Config:
    settings: Settings
    repositories: list[Repository] = field(default_factory=list)

    @property
    def mirrors_path(self) -> Path:
        return Path(self.settings.mirrors_dir)


def _parse_remote(data: dict, context: str) -> Remote:
    url = data.get("url")
    if not url:
        raise ValueError(f"{context}: 'url' is required")
    return Remote(
        url=url,
        ssh_key=data.get("ssh_key"),
        ssl_verify=data.get("ssl_verify", True),
        push_refs=data.get("push_refs", []),
    )


def load_config(path: Path) -> Config:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    settings_data = data.get("settings", {})
    known = Settings.__dataclass_fields__.keys()
    settings = Settings(**{k: v for k, v in settings_data.items() if k in known})

    repositories: list[Repository] = []
    for i, repo_data in enumerate(data.get("repositories", [])):
        ctx = f"repositories[{i}]"
        name = repo_data.get("name", "")

        source_data = repo_data.get("source")
        if not source_data:
            raise ValueError(f"{ctx} '{name}': 'source' table is required")
        source = _parse_remote(source_data, f"{ctx}.source")

        destinations: list[Remote] = []
        for j, dest_data in enumerate(repo_data.get("destinations", [])):
            destinations.append(_parse_remote(dest_data, f"{ctx}.destinations[{j}]"))

        repositories.append(Repository(name=name, source=source, destinations=destinations))

    if not repositories:
        raise ValueError("No repositories defined in config")

    return Config(settings=settings, repositories=repositories)
