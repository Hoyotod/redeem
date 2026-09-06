import argparse
import asyncio
from collections.abc import Sequence

import genshin
from rich.table import Table

from utils import (
    CookieInfo,
    RedeemInfo,
    RedeemStatus,
    censor_uid,
    check_lang,
    close_db_pool,
    console,
    create_genshin_client,
    fix_asyncio_windows_error,
    get_active_codes,
    get_cookies_from_db,
    get_used_codes,
    log,
    send_discord_embed,
    settings,
    update_used_codes,
)


async def redeem_process(
    semaphore: asyncio.Semaphore,
    cookie: CookieInfo,
    lang: str,
    game: genshin.Game,
    code: str,
) -> RedeemInfo:
    async with semaphore:
        client, err = await create_genshin_client(cookie, lang, game)
        if not client:
            return RedeemInfo(
                env_name=cookie.env_name, code=code, status=RedeemStatus.COOKIE_ERROR
            )

        try:
            parts = cookie.env_name.split("_", 1)
            display_name = parts[1] if len(parts) > 1 else cookie.env_name

            accs = await client.get_game_accounts()
            target = next((a for a in accs if a.game == game), None)

            if not target:
                return RedeemInfo(
                    env_name=display_name, code=code, status=RedeemStatus.NO_GAME
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
            )

        except Exception as e:
            parts = cookie.env_name.split("_", 1)
            display_name = parts[1] if len(parts) > 1 else cookie.env_name
            log.debug(f"Account Error ({display_name}): {e}")
            return RedeemInfo(
                env_name=display_name, code=code, status=RedeemStatus.UNKNOWN_ERROR
            )


async def process_game(
    cookies: list[CookieInfo],
    lang: str,
    game: genshin.Game,
    codes: list[str],
    name: str,
) -> list[RedeemInfo]:
    results = []
    semaphore = asyncio.Semaphore(settings.MAX_PARALLEL)

    for code in codes:
        tasks = [
            redeem_process(semaphore, cookie, lang, game, code) for cookie in cookies
        ]
        code_results = await asyncio.gather(*tasks)

        # Terminal tetap butuh info "No Game" untuk debugging, tapi webhook nanti filter
        results.extend(code_results)

        if len(codes) > 1:
            await asyncio.sleep(5)

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


def parse_arguments() -> argparse.Namespace:
    """Parse and return command-line arguments."""
    parser = argparse.ArgumentParser(
        description="HoYoverse code redeemer - Auto-redeem game codes"
    )
    parser.add_argument(
        "-a", "--auto", action="store_true", help="Auto-fetch codes from GitHub repo"
    )
    parser.add_argument(
        "-f", "--force", action="store_true", help="Force redeem (ignore used history)"
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
        if args.force:
            log.info("[FORCE] Ignoring used codes history.")
            used: dict[str, set[str]] = {k: set() for k in active}
        else:
            used = get_used_codes()

        for k in codes_map:
            new_codes = set(active.get(k, [])) - used.get(k, set())
            codes_map[k].update(new_codes)

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


async def report_cookie_errors(cookie_errors: set[str]) -> None:
    """Send final report for cookie errors."""
    if cookie_errors and settings.DISCORD_WEBHOOK_URL:
        err_names = ", ".join(sorted(cookie_errors))
        error_msg = [f"❌ Invalid Cookies ({len(cookie_errors)}): {err_names}"]
        send_chunked_webhook(
            settings.DISCORD_WEBHOOK_URL, "⚠️ Account Alert", error_msg, "ff0000"
        )


async def process_all_games(
    codes_map: dict[str, list[str]], cookies: list[CookieInfo]
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
        res = await process_game(cookies, lang, game, codes, name)

        await display_and_report_results(res, name, global_cookie_errors)
        update_used_codes(key, codes)

    await report_cookie_errors(global_cookie_errors)


async def main() -> None:
    """Main entry point for the redemption script."""
    fix_asyncio_windows_error()

    args = parse_arguments()
    codes_to_redeem = await prepare_codes(args)

    if not codes_to_redeem:
        log.info("No new codes to redeem.")
        return

    cookies = await get_cookies_from_db()
    if not cookies:
        log.error("No cookies found.")
        return

    try:
        await process_all_games(codes_to_redeem, cookies)
    finally:
        await close_db_pool()


if __name__ == "__main__":
    asyncio.run(main())
