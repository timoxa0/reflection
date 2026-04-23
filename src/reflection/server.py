from __future__ import annotations

import logging
import secrets
import threading
from contextlib import asynccontextmanager
from typing import Callable

import uvicorn
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    threading.Thread(target=_trigger_all, daemon=True, name="initial-sync").start()
    yield


app = FastAPI(title="reflection", docs_url=None, redoc_url=None, lifespan=_lifespan)

# Инициализируются при старте через init()
_secret: str = ""
_trigger_one: Callable[[str], bool | None] = lambda _: None
_trigger_all: Callable[[], None] = lambda: None


def init(
    secret: str,
    trigger_one: Callable[[str], bool | None],
    trigger_all: Callable[[], None],
) -> None:
    global _secret, _trigger_one, _trigger_all
    _secret = secret
    _trigger_one = trigger_one
    _trigger_all = trigger_all


def _auth(token: str = Query(...)) -> None:
    if not secrets.compare_digest(token, _secret):
        raise HTTPException(status_code=401, detail="Invalid token")


@app.get("/mirror/{repo_name}", status_code=202)
async def trigger_one(
    repo_name: str,
    background_tasks: BackgroundTasks,
    _: None = Depends(_auth),
) -> dict:
    if _trigger_one(repo_name) is None:
        raise HTTPException(status_code=404, detail=f"Repository '{repo_name}' not found")
    background_tasks.add_task(_trigger_one, repo_name)
    logger.info("Webhook: triggered mirror for '%s'", repo_name)
    return {"status": "accepted", "repo": repo_name}


@app.get("/mirror", status_code=202)
async def trigger_all(
    background_tasks: BackgroundTasks,
    _: None = Depends(_auth),
) -> dict:
    background_tasks.add_task(_trigger_all)
    logger.info("Webhook: triggered mirror for all repos")
    return {"status": "accepted", "repos": "all"}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


def run_server(host: str, port: int) -> None:
    uvicorn.run(app, host=host, port=port, log_config=None)
