import argparse
import asyncio
from collections.abc import Sequence

import genshin
from rich.table import Table

from utils import (
    TERMINAL_STATUS_MAP,
    CookieInfo,
    RedeemInfo,
    RedeemStatus,
    censor_uid,
    check_lang,
    console,
    create_genshin_client,
    fix_asyncio_windows_error,
    get_active_codes,
    get_cookies_from_api,
    get_redemptions,
    log,
    record_redemptions,
    reset_redemptions,
    send_discord_embed,
    send_telegram_message,
    settings,
)


async def redeem_process(
    semaphore: asyncio.Semaphore,
    cookie: CookieInfo,
    lang: str,
    game: genshin.Game,
    code: str,
) -> RedeemInfo:
    async with semaphore:
        client, _ = await create_genshin_client(cookie, lang, game)
        if not client:
            return RedeemInfo(
                env_name=cookie.env_name,
                code=code,
                status=RedeemStatus.COOKIE_ERROR,
                account_id=cookie.account_id,
            )

        try:
            parts = cookie.env_name.split("_", 1)
            display_name = parts[1] if len(parts) > 1 else cookie.env_name

            accs = await client.get_game_accounts()
            target = next((a for a in accs if a.game == game), None)

            if not target:
                return RedeemInfo(
                    env_name=display_name,
                    code=code,
                    status=RedeemStatus.NO_GAME,
                    account_id=cookie.account_id,
                )

            try:
                await client.redeem_code(code, uid=target.uid)
                status = RedeemStatus.SUCCESS
            except genshin.RedemptionClaimed:
                status = RedeemStatus.CLAIMED
            except genshin.RedemptionInvalid:
                status = RedeemStatus.INVALID
            except genshin.RedemptionCooldown:
                status = RedeemStatus.COOLDOWN
            except genshin.RedemptionException as e:
                log.debug(f"Redeem Error ({display_name}): {e}")
                status = RedeemStatus.FAILED

            return RedeemInfo(
                uid=censor_uid(target.uid),
                code=code,
                status=status,
                success=(status == RedeemStatus.SUCCESS),
                env_name=display_name,
                account_id=cookie.account_id,
                raw_uid=str(target.uid),
            )

        except Exception as e:
            parts = cookie.env_name.split("_", 1)
            display_name = parts[1] if len(parts) > 1 else cookie.env_name
            log.debug(f"Account Error ({display_name}): {e}")
            return RedeemInfo(
                env_name=display_name,
                code=code,
                status=RedeemStatus.UNKNOWN_ERROR,
                account_id=cookie.account_id,
            )


async def process_game(
    cookies: list[CookieInfo],
    lang: str,
    game: genshin.Game,
    game_key: str,
    codes: list[str],
    name: str,
    history: dict[str, dict[str, set[str]]],
) -> list[RedeemInfo]:
    results: list[RedeemInfo] = []
    semaphore = asyncio.Semaphore(settings.MAX_PARALLEL)

    for code in codes:
        pending = [
            cookie
            for cookie in cookies
            if code not in history.get(cookie.account_id, {}).get(game_key, set())
        ]
        if not pending:
            log.debug(f"[{name}] {code} already terminal for all accounts, skip.")
            continue

        tasks = [
            redeem_process(semaphore, cookie, lang, game, code) for cookie in pending
        ]
        # Terminal tetap butuh info "No Game" untuk debugging, tapi webhook filter
        results.extend(await asyncio.gather(*tasks))

        if len(codes) > 1:
            await asyncio.sleep(5)

    if not await record_redemptions(game_key, results):
        log.error(f"[{name}] Failed to persist redemption history.")

    # Keep the in-memory view current so duplicate codes in the same run are
    # not attempted twice for an account.
    for result in results:
        if result.status in TERMINAL_STATUS_MAP and result.account_id:
            history.setdefault(result.account_id, {}).setdefault(game_key, set()).add(
                result.code
            )

    return results


def send_chunked_webhook(
    webhook_url: str, title: str, lines: Sequence[str], color: str
) -> None:
    max_length = 1900
    current_msg = "```\n"
    for line in lines:
        if len(current_msg) + len(line) > max_length:
            current_msg += "```"
            send_discord_embed(webhook_url, title, current_msg, color)
            current_msg = "```\n" + line + "\n"
        else:
            current_msg += line + "\n"
    if len(current_msg) > 4:
        current_msg += "```"
        send_discord_embed(webhook_url, title, current_msg, color)


def send_chunked_telegram(
    bot_token: str, chat_id: str, title: str, lines: Sequence[str]
) -> None:
    max_length = 4000
    current = f"{title}\n" if title else ""
    for line in lines:
        if len(current) + len(line) + 1 > max_length:
            send_telegram_message(bot_token, chat_id, current.strip())
            current = f"{title}\n{line}\n" if title else f"{line}\n"
        else:
            current += line + "\n"
    if current.strip():
        send_telegram_message(bot_token, chat_id, current.strip())


def parse_arguments() -> argparse.Namespace:
    """Parse and return command-line arguments."""
    parser = argparse.ArgumentParser(
        description="HoYoverse code redeemer - Auto-redeem game codes"
    )
    parser.add_argument(
        "-a", "--auto", action="store_true", help="Auto-fetch codes from GitHub repo"
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Force redeem (ignore per-account redemption history)",
    )
    parser.add_argument(
        "-r",
        "--reset",
        action="store_true",
        help="Clear per-account redemption history and exit",
    )
    parser.add_argument("-gi", nargs="*", default=[], help="Genshin Impact codes")
    parser.add_argument("-sr", nargs="*", default=[], help="Star Rail codes")
    parser.add_argument("-zz", nargs="*", default=[], help="ZZZ codes")
    return parser.parse_args()


async def prepare_codes(args: argparse.Namespace) -> dict[str, list[str]]:
    """Prepare codes to redeem based on arguments.

    Returns:
        Dictionary mapping game keys to list of codes.
        Empty dict if no codes to process.
    """
    codes_map = {"gi": set(args.gi), "sr": set(args.sr), "zz": set(args.zz)}

    if args.auto:
        active = await get_active_codes()
        for key in codes_map:
            codes_map[key].update(active.get(key, []))

    return {k: list(v) for k, v in codes_map.items() if v}


async def display_and_report_results(
    results: list[RedeemInfo], game_name: str, global_cookie_errors: set[str]
) -> None:
    """Display results in terminal and send webhook notifications."""
    if not results:
        return

    # Terminal table
    table = Table(title=f"🎁 {game_name}", expand=True)
    table.add_column("Akun", style="cyan")
    table.add_column("UID", style="dim")
    table.add_column("Status", justify="center")
    table.add_column("Kode", justify="center", style="magenta")

    success_lines = []
    error_lines = []

    for r in results:
        # Terminal display (skip "No Game")
        if r.status != RedeemStatus.NO_GAME:
            table.add_row(r.env_name, r.uid, r.status, r.code)

        # Webhook filtering
        if r.status == RedeemStatus.NO_GAME:
            continue

        if r.status == RedeemStatus.COOKIE_ERROR:
            global_cookie_errors.add(r.env_name)
            continue

        if r.status in [RedeemStatus.SUCCESS, RedeemStatus.CLAIMED]:
            success_lines.append(f"{r.status} [{r.code}] {r.env_name} ({r.uid})")
        elif r.status in [
            RedeemStatus.INVALID,
            RedeemStatus.COOLDOWN,
            RedeemStatus.FAILED,
            RedeemStatus.UNKNOWN_ERROR,
        ]:
            error_lines.append(f"{r.status} [{r.code}] {r.env_name}")

    console.print(table)

    # Send webhooks
    if settings.DISCORD_WEBHOOK_URL:
        if success_lines:
            send_chunked_webhook(
                settings.DISCORD_WEBHOOK_URL,
                f"Redeem Code - {game_name}",
                success_lines,
                "00ff00",
            )
        if error_lines:
            send_chunked_webhook(
                settings.DISCORD_WEBHOOK_URL,
                f"⚠️ Redeem Error - {game_name}",
                error_lines,
                "ff0000",
            )

    # Send Telegram notifications
    if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID:
        if success_lines:
            send_chunked_telegram(
                settings.TELEGRAM_BOT_TOKEN,
                settings.TELEGRAM_CHAT_ID,
                f"Redeem Code - {game_name}",
                success_lines,
            )
        if error_lines:
            send_chunked_telegram(
                settings.TELEGRAM_BOT_TOKEN,
                settings.TELEGRAM_CHAT_ID,
                f"⚠️ Redeem Error - {game_name}",
                error_lines,
            )


async def report_cookie_errors(cookie_errors: set[str]) -> None:
    """Send final report for cookie errors."""
    if not cookie_errors:
        return
    err_names = ", ".join(sorted(cookie_errors))
    error_msg = [f"❌ Invalid Cookies ({len(cookie_errors)}): {err_names}"]
    if settings.DISCORD_WEBHOOK_URL:
        send_chunked_webhook(
            settings.DISCORD_WEBHOOK_URL, "⚠️ Account Alert", error_msg, "ff0000"
        )
    if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID:
        send_chunked_telegram(
            settings.TELEGRAM_BOT_TOKEN,
            settings.TELEGRAM_CHAT_ID,
            "⚠️ Account Alert",
            error_msg,
        )


async def process_all_games(
    codes_map: dict[str, list[str]],
    cookies: list[CookieInfo],
    history: dict[str, dict[str, set[str]]],
) -> None:
    """Process redemption for all games with codes."""
    config = {
        "gi": (genshin.Game.GENSHIN, "Genshin", settings.NO_GENSHIN),
        "sr": (genshin.Game.STARRAIL, "Star Rail", settings.NO_STARRAIL),
        "zz": (genshin.Game.ZZZ, "ZZZ", settings.NO_ZZZ),
    }

    lang = check_lang(settings.LOCALE)
    log.info(f"🚀 Starting Redeem (Max Parallel: {settings.MAX_PARALLEL})")

    global_cookie_errors: set[str] = set()

    for key, codes in codes_map.items():
        if not codes:
            continue
        game, name, disabled = config[key]
        if disabled:
            continue

        log.info(f"[{name}] Processing {len(codes)} Codes...")
        res = await process_game(cookies, lang, game, key, codes, name, history)

        await display_and_report_results(res, name, global_cookie_errors)

    await report_cookie_errors(global_cookie_errors)


async def main() -> None:
    """Main entry point for the redemption script."""
    fix_asyncio_windows_error()

    args = parse_arguments()

    if args.reset:
        if not await reset_redemptions():
            log.error("Failed to clear redemption history.")
            return
        log.info("All redemption history cleared.")

    codes_to_redeem = await prepare_codes(args)

    if not codes_to_redeem:
        log.info("No new codes to redeem.")
        return

    cookies = await get_cookies_from_api()
    if not cookies:
        log.error("No cookies found.")
        return

    history: dict[str, dict[str, set[str]]] = {}
    if args.force:
        log.info("[FORCE] Ignoring redemption history for this run.")
    else:
        loaded = await get_redemptions()
        if loaded is None:
            log.error(
                "Could not load redemption history; aborting to avoid duplicates."
            )
            return
        history = loaded

    await process_all_games(codes_to_redeem, cookies, history)


if __name__ == "__main__":
    asyncio.run(main())
