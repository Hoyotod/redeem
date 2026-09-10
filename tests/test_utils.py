import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from utils import (
    GAMES,
    GameConfig,
    RedeemStatus,
    Settings,
    censor_uid,
    check_lang,
    format_name,
    get_active_games,
    get_game_by_key,
    get_used_codes,
    update_used_codes,
)


class TestHelperFunctions:
    def test_check_lang_valid(self) -> None:
        assert check_lang("en-us") == "en-us"
        assert check_lang("EN-US") == "en-us"
        assert check_lang("ja-jp") == "ja-jp"
        assert check_lang("zh-cn") == "zh-cn"

    def test_check_lang_invalid(self) -> None:
        assert check_lang("invalid") == "en-us"
        assert check_lang("fr-ca") == "en-us"

    def test_censor_uid(self) -> None:
        assert censor_uid("123456789") == "123■■■■■9"
        assert censor_uid(123456789) == "123■■■■■9"
        assert censor_uid("12345") == "12345"
        assert censor_uid("1") == "1"

    def test_format_name(self) -> None:
        assert format_name("alice") == "ALICE"
        assert format_name("Bob Test") == "BOB_TEST"
        assert format_name("charlie@123") == "CHARLIE_123"
        assert format_name("dave-the-great") == "DAVE_THE_GREAT"


class TestSettings:
    def test_settings_valid_database_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
        settings = Settings()
        assert settings.DATABASE_URL == "postgresql://user:pass@localhost:5432/db"

    def test_settings_invalid_database_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", "mysql://invalid")
        with pytest.raises(ValidationError):
            Settings()

    def test_settings_max_parallel_validation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
        monkeypatch.setenv("MAX_PARALLEL", "0")
        with pytest.raises(ValidationError):
            Settings()

        monkeypatch.setenv("MAX_PARALLEL", "51")
        with pytest.raises(ValidationError):
            Settings()

        monkeypatch.setenv("MAX_PARALLEL", "10")
        settings = Settings()
        assert settings.MAX_PARALLEL == 10

    def test_settings_log_level_validation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
        monkeypatch.setenv("LOG_LEVEL", "INVALID")
        with pytest.raises(ValidationError):
            Settings()

        monkeypatch.setenv("LOG_LEVEL", "debug")
        settings = Settings()
        assert settings.LOG_LEVEL == "DEBUG"


class TestRedeemStatus:
    def test_redeem_status_values(self) -> None:
        assert str(RedeemStatus.SUCCESS) == "✅"
        assert str(RedeemStatus.CLAIMED) == "🟡"
        assert str(RedeemStatus.INVALID) == "☠"
        assert str(RedeemStatus.COOLDOWN) == "⏱"
        assert str(RedeemStatus.FAILED) == "❌"


class TestGameConfig:
    def test_game_config_frozen(self) -> None:
        import genshin

        config = GameConfig(
            "gi", "Genshin", "genshin", genshin.Game.GENSHIN, "NO_GENSHIN"
        )
        assert config.key == "gi"
        assert config.name == "Genshin"

    def test_get_game_by_key(self) -> None:
        game = get_game_by_key("gi")
        assert game is not None
        assert game.key == "gi"
        assert game.name == "Genshin"

        assert get_game_by_key("invalid") is None

    def test_get_active_games(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
        monkeypatch.setenv("NO_GENSHIN", "true")
        monkeypatch.setenv("NO_STARRAIL", "false")
        monkeypatch.setenv("NO_ZZZ", "false")

        import importlib

        import utils

        importlib.reload(utils)

        active = utils.get_active_games()
        keys = [g.key for g in active]
        assert "gi" not in keys
        assert "sr" in keys or "zz" in keys


class TestUsedCodes:
    def test_get_used_codes_empty(self, tmp_used_dir: Path) -> None:
        result = get_used_codes()
        assert all(len(codes) == 0 for codes in result.values())

    def test_get_used_codes_existing(self, tmp_used_dir: Path) -> None:
        (tmp_used_dir / "genshin.txt").write_text("CODE1\nCODE2\nCODE1\n")
        (tmp_used_dir / "starrail.txt").write_text("SRCODE1\n")

        result = get_used_codes()
        assert result["gi"] == {"CODE1", "CODE2"}
        assert result["sr"] == {"SRCODE1"}
        assert result["zz"] == set()

    def test_update_used_codes(self, tmp_used_dir: Path) -> None:
        update_used_codes("gi", ["CODE1", "CODE2"])
        assert (tmp_used_dir / "genshin.txt").exists()
        content = (tmp_used_dir / "genshin.txt").read_text()
        assert "CODE1\n" in content
        assert "CODE2\n" in content

        update_used_codes("gi", ["CODE3"])
        content = (tmp_used_dir / "genshin.txt").read_text()
        assert "CODE3\n" in content
        assert content.count("CODE") == 3


class TestAsyncFunctions:
    async def test_get_active_codes_success(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(
            return_value=[
                {"code": "GENSHIN1"},
                {"code": "GENSHIN2"},
            ]
        )

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("utils.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            from utils import get_active_codes

            result = await get_active_codes()

            assert "gi" in result
            assert "GENSHIN1" in result["gi"]
            assert "GENSHIN2" in result["gi"]

    async def test_get_active_codes_http_error(self) -> None:
        import httpx

        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.HTTPError("Network error")

        with patch("utils.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            from utils import get_active_codes

            result = await get_active_codes()

            for game_codes in result.values():
                assert isinstance(game_codes, list)
                assert len(game_codes) == 0
