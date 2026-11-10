import argparse
from unittest.mock import AsyncMock, MagicMock, patch

import genshin

from main import (
    parse_arguments,
    prepare_codes,
    process_game,
    redeem_process,
    send_chunked_webhook,
)
from utils import CookieInfo, RedeemInfo, RedeemStatus


class TestParseArguments:
    def test_parse_arguments_auto(self) -> None:
        with patch("sys.argv", ["main.py", "-a"]):
            args = parse_arguments()
            assert args.auto is True
            assert args.force is False

    def test_parse_arguments_force(self) -> None:
        with patch("sys.argv", ["main.py", "-a", "-f"]):
            args = parse_arguments()
            assert args.auto is True
            assert args.force is True

    def test_parse_arguments_manual_codes(self) -> None:
        with patch("sys.argv", ["main.py", "-gi", "CODE1", "CODE2", "-sr", "SRCODE"]):
            args = parse_arguments()
            assert args.gi == ["CODE1", "CODE2"]
            assert args.sr == ["SRCODE"]
            assert args.zz == []

    def test_parse_arguments_reset(self) -> None:
        with patch("sys.argv", ["main.py", "-r"]):
            args = parse_arguments()
            assert args.reset is True


class TestPrepareCodes:
    async def test_prepare_codes_manual_only(self) -> None:
        args = argparse.Namespace(auto=False, force=False, gi=["CODE1"], sr=[], zz=[])
        result = await prepare_codes(args)
        assert result == {"gi": ["CODE1"]}

    async def test_prepare_codes_auto_mode(self) -> None:
        args = argparse.Namespace(auto=True, force=False, gi=[], sr=[], zz=[])

        with patch("main.get_active_codes") as mock_get_active:
            mock_get_active.return_value = {
                "gi": ["CODE1", "CODE2"],
                "sr": ["SRCODE1"],
                "zz": [],
            }

            result = await prepare_codes(args)

        assert set(result["gi"]) == {"CODE1", "CODE2"}
        assert result["sr"] == ["SRCODE1"]

    async def test_prepare_codes_merges_manual_and_active(self) -> None:
        args = argparse.Namespace(auto=True, force=False, gi=["MANUAL"], sr=[], zz=[])

        with patch("main.get_active_codes") as mock_get_active:
            mock_get_active.return_value = {
                "gi": ["CODE1"],
                "sr": [],
                "zz": [],
            }

            result = await prepare_codes(args)

        assert set(result["gi"]) == {"MANUAL", "CODE1"}


class TestSendChunkedWebhook:
    def test_send_chunked_webhook_single_message(self) -> None:
        with patch("main.send_discord_embed") as mock_send:
            lines = ["Line 1", "Line 2", "Line 3"]
            send_chunked_webhook("http://webhook.url", "Title", lines, "00ff00")

            assert mock_send.call_count == 1
            call_args = mock_send.call_args[0]
            assert "Line 1" in call_args[2]
            assert "Line 2" in call_args[2]

    def test_send_chunked_webhook_multiple_chunks(self) -> None:
        with patch("main.send_discord_embed") as mock_send:
            lines = ["A" * 1000 for _ in range(5)]
            send_chunked_webhook("http://webhook.url", "Title", lines, "00ff00")

            assert mock_send.call_count > 1


class TestRedeemProcess:
    async def test_redeem_process_cookie_error(self) -> None:
        semaphore = AsyncMock()
        cookie = CookieInfo(
            env_name="ACC1_TEST", cookies="invalid", account_id="acc-uuid-1"
        )

        with patch("main.create_genshin_client") as mock_create:
            mock_create.return_value = (None, "Cookie error")

            result = await redeem_process(
                semaphore, cookie, "en-us", genshin.Game.GENSHIN, "CODE1"
            )

            assert result.status == RedeemStatus.COOKIE_ERROR
            assert result.env_name == "ACC1_TEST"
            assert result.account_id == "acc-uuid-1"

    async def test_redeem_process_no_game(self) -> None:
        semaphore = AsyncMock()
        cookie = CookieInfo(
            env_name="ACC1_TEST", cookies="valid", account_id="acc-uuid-1"
        )
        mock_client = MagicMock()
        mock_client.get_game_accounts = AsyncMock(return_value=[])

        with patch("main.create_genshin_client") as mock_create:
            mock_create.return_value = (mock_client, None)

            result = await redeem_process(
                semaphore, cookie, "en-us", genshin.Game.GENSHIN, "CODE1"
            )

            assert result.status == RedeemStatus.NO_GAME
            assert result.account_id == "acc-uuid-1"

    async def test_redeem_process_success(self) -> None:
        semaphore = AsyncMock()
        cookie = CookieInfo(
            env_name="ACC1_ALICE", cookies="valid", account_id="acc-uuid-1"
        )
        mock_client = MagicMock()

        mock_account = MagicMock()
        mock_account.game = genshin.Game.GENSHIN
        mock_account.uid = 123456789

        mock_client.get_game_accounts = AsyncMock(return_value=[mock_account])
        mock_client.redeem_code = AsyncMock()

        with patch("main.create_genshin_client") as mock_create:
            mock_create.return_value = (mock_client, None)

            result = await redeem_process(
                semaphore, cookie, "en-us", genshin.Game.GENSHIN, "CODE1"
            )

            assert result.status == RedeemStatus.SUCCESS
            assert result.success is True
            assert result.env_name == "ALICE"
            assert result.account_id == "acc-uuid-1"
            assert result.raw_uid == "123456789"

    async def test_redeem_process_claimed(self) -> None:
        semaphore = AsyncMock()
        cookie = CookieInfo(
            env_name="ACC1_BOB", cookies="valid", account_id="acc-uuid-1"
        )
        mock_client = MagicMock()

        mock_account = MagicMock()
        mock_account.game = genshin.Game.GENSHIN
        mock_account.uid = 123456789

        mock_client.get_game_accounts = AsyncMock(return_value=[mock_account])
        mock_client.redeem_code = AsyncMock(side_effect=genshin.RedemptionClaimed())

        with patch("main.create_genshin_client") as mock_create:
            mock_create.return_value = (mock_client, None)

            result = await redeem_process(
                semaphore, cookie, "en-us", genshin.Game.GENSHIN, "CODE1"
            )

            assert result.status == RedeemStatus.CLAIMED
            assert result.account_id == "acc-uuid-1"


class TestProcessGame:
    async def test_process_game_single_code(self) -> None:
        cookies = [
            CookieInfo(env_name="ACC1_ALICE", cookies="cookie1", account_id="acc-1"),
            CookieInfo(env_name="ACC2_BOB", cookies="cookie2", account_id="acc-2"),
        ]

        with (
            patch("main.redeem_process") as mock_redeem,
            patch("main.record_redemptions", new=AsyncMock(return_value=True)),
        ):
            mock_redeem.return_value = RedeemInfo(
                uid="123■■■■■9",
                code="CODE1",
                status=RedeemStatus.SUCCESS,
                success=True,
                env_name="ALICE",
                account_id="acc-1",
            )

            result = await process_game(
                cookies,
                "en-us",
                genshin.Game.GENSHIN,
                "gi",
                ["CODE1"],
                "Genshin",
                {},
            )

            assert len(result) == 2
            assert mock_redeem.call_count == 2

    async def test_process_game_multiple_codes(self) -> None:
        cookies = [
            CookieInfo(env_name="ACC1_ALICE", cookies="cookie1", account_id="acc-1")
        ]

        with (
            patch("main.redeem_process") as mock_redeem,
            patch("main.record_redemptions", new=AsyncMock(return_value=True)),
            patch("asyncio.sleep") as mock_sleep,
        ):
            mock_redeem.return_value = RedeemInfo(
                uid="123■■■■■9",
                code="CODE",
                status=RedeemStatus.SUCCESS,
                success=True,
                env_name="ALICE",
                account_id="acc-1",
            )

            result = await process_game(
                cookies,
                "en-us",
                genshin.Game.GENSHIN,
                "gi",
                ["CODE1", "CODE2"],
                "Genshin",
                {},
            )

            assert len(result) == 2
            assert mock_sleep.call_count >= 1

    async def test_process_game_skips_terminal_codes_per_account(self) -> None:
        cookies = [
            CookieInfo(env_name="ACC1_ALICE", cookies="cookie1", account_id="acc-1"),
            CookieInfo(env_name="ACC2_BOB", cookies="cookie2", account_id="acc-2"),
        ]
        history = {"acc-1": {"gi": {"CODE1"}}}

        with (
            patch("main.redeem_process") as mock_redeem,
            patch("main.record_redemptions", new=AsyncMock(return_value=True)),
        ):
            mock_redeem.return_value = RedeemInfo(
                code="CODE1",
                status=RedeemStatus.SUCCESS,
                success=True,
                env_name="BOB",
                account_id="acc-2",
            )

            result = await process_game(
                cookies,
                "en-us",
                genshin.Game.GENSHIN,
                "gi",
                ["CODE1"],
                "Genshin",
                history,
            )

            assert len(result) == 1
            assert mock_redeem.call_count == 1
            assert mock_redeem.call_args.args[1].account_id == "acc-2"

    async def test_process_game_skips_code_when_all_accounts_terminal(self) -> None:
        cookies = [
            CookieInfo(env_name="ACC1_ALICE", cookies="cookie1", account_id="acc-1"),
            CookieInfo(env_name="ACC2_BOB", cookies="cookie2", account_id="acc-2"),
        ]
        history = {
            "acc-1": {"gi": {"CODE1"}},
            "acc-2": {"gi": {"CODE1"}},
        }

        with (
            patch("main.redeem_process") as mock_redeem,
            patch("main.record_redemptions", new=AsyncMock(return_value=True)),
        ):
            result = await process_game(
                cookies,
                "en-us",
                genshin.Game.GENSHIN,
                "gi",
                ["CODE1"],
                "Genshin",
                history,
            )

            assert result == []
            mock_redeem.assert_not_called()

    async def test_process_game_records_terminal_results(self) -> None:
        cookies = [
            CookieInfo(env_name="ACC1_ALICE", cookies="cookie1", account_id="acc-1")
        ]
        results = [
            RedeemInfo(code="CODE1", status=RedeemStatus.SUCCESS, account_id="acc-1")
        ]

        with (
            patch("main.redeem_process", new=AsyncMock(return_value=results[0])),
            patch(
                "main.record_redemptions", new=AsyncMock(return_value=True)
            ) as mock_record,
        ):
            await process_game(
                cookies,
                "en-us",
                genshin.Game.GENSHIN,
                "gi",
                ["CODE1"],
                "Genshin",
                {},
            )

        mock_record.assert_awaited_once_with("gi", results)


class TestResetFlow:
    async def test_main_with_reset_only_exits(self) -> None:
        with (
            patch("sys.argv", ["main.py", "-r"]),
            patch(
                "main.reset_redemptions", new=AsyncMock(return_value=True)
            ) as mock_reset,
            patch("main.prepare_codes") as mock_prepare,
            patch("main.get_cookies_from_api") as mock_get_cookies,
        ):
            mock_prepare.return_value = {}

            from main import main

            await main()

            mock_reset.assert_awaited_once()
            mock_prepare.assert_called_once()
            mock_get_cookies.assert_not_called()

    async def test_main_with_reset_and_auto(self) -> None:
        history = {"acc-1": {"gi": {"CODE0"}}}

        with (
            patch("sys.argv", ["main.py", "-r", "-a"]),
            patch(
                "main.reset_redemptions", new=AsyncMock(return_value=True)
            ) as mock_reset,
            patch("main.prepare_codes") as mock_prepare,
            patch("main.get_cookies_from_api") as mock_get_cookies,
            patch("main.get_redemptions", new=AsyncMock(return_value=history)),
            patch("main.process_all_games") as mock_process,
        ):
            mock_prepare.return_value = {"gi": ["CODE1", "CODE2"]}
            mock_get_cookies.return_value = [
                CookieInfo(env_name="ACC1_TEST", cookies="test_cookie")
            ]

            from main import main

            await main()

            mock_reset.assert_awaited_once()
            mock_prepare.assert_called_once()
            mock_get_cookies.assert_awaited_once()
            mock_process.assert_awaited_once_with(
                {"gi": ["CODE1", "CODE2"]}, mock_get_cookies.return_value, history
            )

    async def test_main_with_reset_failure_exits(self) -> None:
        with (
            patch("sys.argv", ["main.py", "-r", "-a"]),
            patch("main.reset_redemptions", new=AsyncMock(return_value=False)),
            patch("main.prepare_codes") as mock_prepare,
            patch("main.get_cookies_from_api") as mock_get_cookies,
        ):
            from main import main

            await main()

            mock_prepare.assert_not_called()
            mock_get_cookies.assert_not_called()


class TestMainHistoryLoading:
    async def test_main_force_skips_history_load(self) -> None:
        with (
            patch("sys.argv", ["main.py", "-a", "-f"]),
            patch("main.prepare_codes") as mock_prepare,
            patch("main.get_cookies_from_api") as mock_get_cookies,
            patch("main.get_redemptions") as mock_get_history,
            patch("main.process_all_games") as mock_process,
        ):
            mock_prepare.return_value = {"gi": ["CODE1"]}
            mock_get_cookies.return_value = [
                CookieInfo(
                    env_name="ACC1_TEST", cookies="test_cookie", account_id="acc-1"
                )
            ]

            from main import main

            await main()

            mock_get_history.assert_not_called()
            mock_process.assert_awaited_once_with(
                {"gi": ["CODE1"]}, mock_get_cookies.return_value, {}
            )

    async def test_main_aborts_when_history_unavailable(self) -> None:
        with (
            patch("sys.argv", ["main.py", "-a"]),
            patch("main.prepare_codes") as mock_prepare,
            patch("main.get_cookies_from_api") as mock_get_cookies,
            patch("main.get_redemptions", new=AsyncMock(return_value=None)),
            patch("main.process_all_games") as mock_process,
        ):
            mock_prepare.return_value = {"gi": ["CODE1"]}
            mock_get_cookies.return_value = [
                CookieInfo(
                    env_name="ACC1_TEST", cookies="test_cookie", account_id="acc-1"
                )
            ]

            from main import main

            await main()

            mock_process.assert_not_called()
