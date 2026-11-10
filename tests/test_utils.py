from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from utils import (
    GameConfig,
    RedeemInfo,
    RedeemStatus,
    Settings,
    censor_uid,
    check_lang,
    format_name,
    get_game_by_key,
    get_redemptions,
    record_redemptions,
    reset_redemptions,
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
    def test_settings_valid_hoyo_api_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOYO_API_URL", "https://hoyo-api.test/")
        settings = Settings()
        assert settings.HOYO_API_URL == "https://hoyo-api.test"

    def test_settings_invalid_hoyo_api_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOYO_API_URL", "mysql://invalid")
        with pytest.raises(ValidationError):
            Settings()

    def test_settings_max_parallel_validation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOYO_API_URL", "https://hoyo-api.test")
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
        monkeypatch.setenv("HOYO_API_URL", "https://hoyo-api.test")
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
        monkeypatch.setenv("HOYO_API_URL", "https://hoyo-api.test")
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


class TestRedemptionHistory:
    async def test_get_redemptions_groups_by_account_and_game(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "redemptions": [
                {"gameAccountId": "acc-1", "game": "gi", "code": "CODE1"},
                {"gameAccountId": "acc-1", "game": "gi", "code": "CODE2"},
                {"gameAccountId": "acc-1", "game": "sr", "code": "SR1"},
                {"gameAccountId": "acc-2", "game": "gi", "code": "CODE1"},
                {"gameAccountId": "acc-2", "game": "gi", "code": "CODE1"},
            ]
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("utils.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            result = await get_redemptions()

        assert result is not None
        assert result["acc-1"]["gi"] == {"CODE1", "CODE2"}
        assert result["acc-1"]["sr"] == {"SR1"}
        assert result["acc-2"]["gi"] == {"CODE1"}

    async def test_get_redemptions_returns_none_on_http_error(self) -> None:
        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.HTTPError("Network error")

        with patch("utils.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            result = await get_redemptions()

        assert result is None

    async def test_get_redemptions_returns_none_on_bad_payload(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"unexpected": []}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("utils.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            result = await get_redemptions()

        assert result is None

    async def test_record_redemptions_only_terminal_results(self) -> None:
        mock_response = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        results = [
            RedeemInfo(
                code="CODE1",
                status=RedeemStatus.SUCCESS,
                account_id="acc-1",
                raw_uid="123456789",
            ),
            RedeemInfo(code="CODE2", status=RedeemStatus.COOLDOWN, account_id="acc-1"),
            RedeemInfo(code="CODE3", status=RedeemStatus.INVALID, account_id="acc-2"),
            RedeemInfo(code="CODE4", status=RedeemStatus.CLAIMED, account_id="acc-2"),
        ]

        with patch("utils.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            assert await record_redemptions("gi", results) is True

        payload = mock_client.post.call_args.kwargs["json"]
        assert len(payload["redemptions"]) == 3
        by_code = {row["code"]: row for row in payload["redemptions"]}
        assert by_code["CODE1"]["status"] == "success"
        assert by_code["CODE3"]["status"] == "invalid"
        assert by_code["CODE4"]["status"] == "claimed"
        assert "CODE2" not in by_code

    async def test_record_redemptions_skips_request_without_terminal_results(
        self,
    ) -> None:
        mock_client = AsyncMock()

        with patch("utils.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            result = await record_redemptions(
                "gi", [RedeemInfo(code="CODE1", status=RedeemStatus.FAILED)]
            )

        assert result is True
        mock_client.post.assert_not_called()

    async def test_record_redemptions_returns_false_on_http_error(self) -> None:
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.HTTPError("Network error")

        with patch("utils.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            result = await record_redemptions(
                "gi",
                [
                    RedeemInfo(
                        code="CODE1",
                        status=RedeemStatus.SUCCESS,
                        account_id="acc-1",
                    )
                ],
            )

        assert result is False

    async def test_reset_redemptions_success(self) -> None:
        mock_response = MagicMock()
        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(return_value=mock_response)

        with patch("utils.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            assert await reset_redemptions() is True

        mock_client.delete.assert_awaited_once()

    async def test_reset_redemptions_returns_false_on_http_error(self) -> None:
        mock_client = AsyncMock()
        mock_client.delete.side_effect = httpx.HTTPError("Network error")

        with patch("utils.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            assert await reset_redemptions() is False


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
