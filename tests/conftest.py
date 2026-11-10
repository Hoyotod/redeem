import os
from unittest.mock import AsyncMock, MagicMock

# Ensure Settings can be instantiated during collection even without a .env file.
os.environ.setdefault("HOYO_API_URL", "https://hoyo-api.test")
os.environ.setdefault("HOYO_API_KEY", "test-key")

import httpx
import pytest


@pytest.fixture
def mock_httpx_client() -> AsyncMock:
    client = AsyncMock(spec=httpx.AsyncClient)
    return client


@pytest.fixture
def mock_genshin_client() -> MagicMock:
    client = MagicMock()
    client.get_game_accounts = AsyncMock()
    client.redeem_code = AsyncMock()
    return client
