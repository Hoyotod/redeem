import os
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import httpx
import pytest


@pytest.fixture
def mock_db_pool() -> AsyncMock:
    pool = AsyncMock(spec=asyncpg.Pool)
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__.return_value = conn
    return pool


@pytest.fixture
def mock_httpx_client() -> AsyncMock:
    client = AsyncMock(spec=httpx.AsyncClient)
    return client


@pytest.fixture
def mock_env_no_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)


@pytest.fixture
def mock_env_with_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/testdb")


@pytest.fixture
def tmp_used_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    used_dir = tmp_path / "used"
    used_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    return used_dir


@pytest.fixture
def sample_cookies() -> list[dict[str, Any]]:
    return [
        {
            "name": "Alice",
            "accountId": "123456789",
            "cookieToken": "abc123token",
        },
        {
            "name": "Bob_Test",
            "accountId": "987654321",
            "cookieToken": "xyz789token",
        },
    ]


@pytest.fixture
def mock_genshin_client() -> MagicMock:
    client = MagicMock()
    client.get_game_accounts = AsyncMock()
    client.redeem_code = AsyncMock()
    return client
