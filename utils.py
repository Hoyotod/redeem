import asyncio
import logging
import sys
from dataclasses import dataclass
from enum import Enum
from re import sub

import genshin
import httpx
from discord_webhook import DiscordEmbed, DiscordWebhook
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from rich.console import Console
from rich.logging import RichHandler


# --- Configuration Management (Pydantic) ---
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # App Config
    LOCALE: str = "en-us"
    MAX_PARALLEL: int = 5
    LOG_LEVEL: str = "INFO"

    # HoYo API
    HOYO_API_URL: str
    HOYO_API_KEY: str

    # Webhooks
    DISCORD_WEBHOOK_URL: str | None = None

    # Telegram
    TELEGRAM_BOT_TOKEN: str | None = None
    TELEGRAM_CHAT_ID: str | None = None

    # Feature Flags
    NO_GENSHIN: bool = False
    NO_STARRAIL: bool = False
    NO_ZZZ: bool = False

    @field_validator("HOYO_API_URL")
    @classmethod
    def validate_hoyo_api_url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not v.startswith(("http://", "https://")):
            raise ValueError("HOYO_API_URL must start with http:// or https://")
        return v

    @field_validator("MAX_PARALLEL")
    @classmethod
    def validate_max_parallel(cls, v: int) -> int:
        if v < 1 or v > 50:
            raise ValueError("MAX_PARALLEL must be between 1 and 50")
        return v

    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        v_upper = v.upper()
        if v_upper not in valid:
            raise ValueError(f"LOG_LEVEL must be one of {valid}")
        return v_upper


settings = Settings()  # type: ignore[call-arg]

# --- Setup Logging & Console ---
logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(console=Console(), rich_tracebacks=True)],
)
log = logging.getLogger("rich")
console = Console()


# --- Data Structures ---
class RedeemStatus(str, Enum):  # noqa: UP042
    """Status codes for redemption results."""

    SUCCESS = "✅"
    CLAIMED = "🟡"
    INVALID = "☠"
    COOLDOWN = "⏱"
    FAILED = "❌"
    COOKIE_ERROR = "🍪"
    NO_GAME = "🎮"
    UNKNOWN_ERROR = "❓"

    def __str__(self) -> str:
        return self.value


# Results that must never be retried for the same game account.
# COOLDOWN / FAILED / UNKNOWN_ERROR are intentionally absent: they are
# temporary and should be attempted again on the next run.
TERMINAL_STATUS_MAP: dict[RedeemStatus, str] = {
    RedeemStatus.SUCCESS: "success",
    RedeemStatus.CLAIMED: "claimed",
    RedeemStatus.INVALID: "invalid",
}


@dataclass
class CookieInfo:
    env_name: str = ""
    cookies: str = ""
    account_id: str = ""

    def get(self) -> str:
        return self.cookies


@dataclass
class RedeemInfo:
    uid: str = "❓"
    name: str = "❓"
    code: str = "❓"
    status: RedeemStatus = RedeemStatus.FAILED
    success: bool = False
    env_name: str = "❓"
    account_id: str = ""
    raw_uid: str | None = None


# --- Helper Functions ---
def check_lang(lang: str) -> str:
    valid = {
        "zh-cn",
        "zh-tw",
        "de-de",
        "en-us",
        "es-es",
        "fr-fr",
        "id-id",
        "ja-jp",
        "ko-kr",
        "pt-pt",
        "ru-ru",
        "th-th",
        "vi-vn",
    }
    lang = lang.lower()
    if lang not in valid:
        log.warning(f"[LANGUAGE] '{lang}' not supported. Using 'en-us'.")
        return "en-us"
    return lang


def censor_uid(uid: int | str) -> str:
    s = str(uid)
    return s[:-6] + "■■■■■" + s[-1] if len(s) >= 6 else s


def format_name(name: str) -> str:
    # Ganti karakter non-alphanumeric (kecuali awal/akhir) dengan underscore
    name = sub(r"(?<!^)\W+(?!$)", "_", name)
    return name.upper()


# Karakter yang tidak boleh muncul di nilai cookie (RFC 6265 cookie-octet).
_COOKIE_FORBIDDEN = frozenset('",;\\')


def is_safe_cookie_value(value: str) -> bool:
    """Validate a cookie value against RFC 6265.

    HoYoverse cookie tokens may contain base64/URL-encoded characters
    (+, /, =, %, ...). Only separators/whitespace/quotes are rejected to
    prevent cookie injection.
    """
    return bool(value) and all(
        0x21 <= ord(char) <= 0x7E and char not in _COOKIE_FORBIDDEN for char in value
    )


def fix_asyncio_windows_error() -> None:
    if sys.version_info >= (3, 8) and sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


# --- Core Logic ---


async def get_cookies_from_api() -> list[CookieInfo]:
    """Fetch account cookies from hoyo-api.

    Calls `GET /api/internal/accounts` (shared-secret auth) and formats the
    returned tokens for HoYoverse API authentication.

    Returns:
        List of CookieInfo objects with formatted cookie strings.
        Empty list if the request fails or the payload is invalid.
    """
    url = f"{settings.HOYO_API_URL.rstrip('/')}/api/internal/accounts"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                url, headers={"X-API-Key": settings.HOYO_API_KEY}
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as e:
        log.error(f"[COOKIE] hoyo-api request failed (HTTP {e.response.status_code}).")
        return []
    except httpx.HTTPError as e:
        log.error(f"[COOKIE] Could not reach hoyo-api: {type(e).__name__}")
        return []
    except ValueError:
        log.error("[COOKIE] hoyo-api returned invalid JSON.")
        return []

    rows = payload.get("accounts") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        log.error("[COOKIE] Unexpected hoyo-api response shape.")
        return []

    cookies = []
    for idx, row in enumerate(rows, 1):
        try:
            name = row.get("name")
            safe_name = format_name(str(name)) if name else ""
            env_name = f"ACC{idx}_{safe_name}" if safe_name else f"ACC{idx}"

            account_uuid = row.get("id")
            account_id = row.get("accountId")
            cookie_token = row.get("cookieToken")

            if not account_uuid:
                log.warning(f"[COOKIE] Missing account id for akun {env_name}, skip.")
                continue

            if not account_id or not cookie_token:
                log.warning(f"[COOKIE] Data tidak lengkap untuk akun {env_name}, skip.")
                continue

            # Validate to prevent cookie injection (dangerous characters).
            account_id = str(account_id)
            cookie_token = str(cookie_token)
            if not is_safe_cookie_value(account_id) or not is_safe_cookie_value(
                cookie_token
            ):
                log.warning(
                    f"[COOKIE] Format cookie tidak valid untuk akun {env_name}, skip."
                )
                continue

            cookie_str = f"account_id_v2={account_id}; cookie_token_v2={cookie_token}"
            cookies.append(
                CookieInfo(
                    env_name=env_name,
                    cookies=cookie_str,
                    account_id=str(account_uuid),
                )
            )
        except Exception as e:
            log.warning(f"[COOKIE] Gagal memproses row {idx}: {e}")
            continue

    log.info(f"[COOKIE] Berhasil memuat {len(cookies)} akun dari hoyo-api.")
    return cookies


def _internal_headers() -> dict[str, str]:
    return {"X-API-Key": settings.HOYO_API_KEY}


def _internal_url(path: str) -> str:
    return f"{settings.HOYO_API_URL.rstrip('/')}{path}"


async def get_redemptions() -> dict[str, dict[str, set[str]]] | None:
    """Fetch per-account redemption history from hoyo-api.

    Returns:
        Mapping of game account UUID -> game key -> redeemed/terminal codes.
        None when the request fails, so callers can abort instead of risking
        a duplicate redemption.
    """
    url = _internal_url("/api/internal/redemptions")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=_internal_headers())
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as e:
        log.error(f"[HISTORY] hoyo-api request failed (HTTP {e.response.status_code}).")
        return None
    except httpx.HTTPError as e:
        log.error(f"[HISTORY] Could not reach hoyo-api: {type(e).__name__}")
        return None
    except ValueError:
        log.error("[HISTORY] hoyo-api returned invalid JSON.")
        return None

    rows = payload.get("redemptions") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        log.error("[HISTORY] Unexpected hoyo-api response shape.")
        return None

    history: dict[str, dict[str, set[str]]] = {}
    for row in rows:
        try:
            account_uuid = str(row["gameAccountId"])
            game = str(row["game"])
            code = str(row["code"])
        except (KeyError, TypeError):
            continue
        history.setdefault(account_uuid, {}).setdefault(game, set()).add(code)

    total = sum(len(codes) for games in history.values() for codes in games.values())
    log.info(f"[HISTORY] Loaded {total} redemption records.")
    return history


async def record_redemptions(game_key: str, results: list[RedeemInfo]) -> bool:
    """Persist terminal results for a game so they are never retried."""
    rows = []
    for result in results:
        status = TERMINAL_STATUS_MAP.get(result.status)
        if not status or not result.account_id or not result.code:
            continue
        rows.append(
            {
                "gameAccountId": result.account_id,
                "game": game_key,
                "code": result.code,
                "uid": result.raw_uid,
                "status": status,
            }
        )

    if not rows:
        return True

    url = _internal_url("/api/internal/redemptions")
    for start in range(0, len(rows), 500):
        chunk = rows[start : start + 500]
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    url, headers=_internal_headers(), json={"redemptions": chunk}
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as e:
            log.error(
                f"[HISTORY] Failed to record codes (HTTP {e.response.status_code})."
            )
            return False
        except httpx.HTTPError as e:
            log.error(f"[HISTORY] Could not record codes: {type(e).__name__}")
            return False

    log.info(f"[HISTORY] Recorded {len(rows)} terminal results for {game_key}.")
    return True


async def reset_redemptions() -> bool:
    """Clear all per-account redemption history."""
    url = _internal_url("/api/internal/redemptions")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.delete(url, headers=_internal_headers())
            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        log.error(f"[HISTORY] Reset failed (HTTP {e.response.status_code}).")
        return False
    except httpx.HTTPError as e:
        log.error(f"[HISTORY] Could not reset history: {type(e).__name__}")
        return False

    log.info("[HISTORY] Cleared all redemption history.")
    return True


async def create_genshin_client(
    cookie: CookieInfo, lang: str, game: genshin.Game
) -> tuple[genshin.Client | None, str | None]:
    """Create a client from raw cookies (no v1 token-completion network call)."""
    try:
        client = genshin.Client(cookies=cookie.get(), lang=lang, game=game)  # type: ignore
        return client, None
    except Exception as e:
        return None, str(e)


def send_discord_embed(
    webhook_url: str, title: str, msg: str, color: str = "00ff00"
) -> None:
    """Mengirim notifikasi ke Discord (Unified)."""
    if not webhook_url:
        return
    try:
        webhook = DiscordWebhook(url=webhook_url)
        embed = DiscordEmbed(title=title, description=msg, color=color)
        embed.set_timestamp()
        embed.set_footer(text="Hoyo Tools")
        webhook.add_embed(embed)
        webhook.execute()
    except Exception as e:
        log.error(f"[DISCORD] Gagal mengirim webhook: {e}")


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> None:
    """Send a message to a Telegram chat via Bot API."""
    if not bot_token or not chat_id or not text:
        return
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, json={"chat_id": chat_id, "text": text})
            if resp.status_code != 200:
                log.error(
                    f"[TELEGRAM] Gagal mengirim (HTTP {resp.status_code}): {resp.text}"
                )
    except Exception as e:
        log.error(f"[TELEGRAM] Gagal mengirim pesan: {e}")


# --- Code Logic ---


@dataclass(frozen=True)
class GameConfig:
    """Configuration for a HoYoverse game."""

    key: str  # Short key: "gi", "sr", "zz"
    name: str  # Display name: "Genshin", "Star Rail"
    path: str  # GitHub path: "genshin", "starrail"
    game_enum: genshin.Game
    disabled_setting: str  # Settings attribute name
    fetch_enabled: bool = True  # Whether auto-fetch is available for this game


# Centralized game configurations
GAMES: list[GameConfig] = [
    GameConfig("gi", "Genshin", "genshin", genshin.Game.GENSHIN, "NO_GENSHIN"),
    GameConfig("sr", "Star Rail", "starrail", genshin.Game.STARRAIL, "NO_STARRAIL"),
    GameConfig("zz", "ZZZ", "zzz", genshin.Game.ZZZ, "NO_ZZZ", fetch_enabled=False),
]

# Legacy map for backward compatibility (to be removed later)
GAME_MAP = {g.path: g.key for g in GAMES}
GITHUB_RAW_URL = "https://raw.githubusercontent.com/Hoyotod/code/refs/heads/main/"


def get_game_by_key(key: str) -> GameConfig | None:
    """Get game configuration by short key (gi, sr, zz)."""
    return next((g for g in GAMES if g.key == key), None)


def get_active_games() -> list[GameConfig]:
    """Get list of games that are not disabled in settings."""
    return [g for g in GAMES if not getattr(settings, g.disabled_setting)]


async def get_active_codes() -> dict[str, list[str]]:
    """Fetch active codes from GitHub repository.

    Returns:
        Dictionary mapping game keys to list of active codes.
    """
    active: dict[str, list[str]] = {g.key: [] for g in GAMES}
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        for game in GAMES:
            if not game.fetch_enabled:
                log.debug(
                    f"[CODES] Skipping {game.name}: no auto-fetch source available"
                )
                continue
            try:
                r = await client.get(f"{GITHUB_RAW_URL}{game.path}/active.json")
                if r.status_code == 200:
                    data = r.json()
                    if isinstance(data, list):
                        if data and isinstance(data[0], dict):
                            active[game.key] = [i["code"] for i in data if "code" in i]
                        else:
                            active[game.key] = data
                else:
                    log.warning(
                        f"[CODES] Failed to fetch {game.path} (HTTP {r.status_code})"
                    )
            except httpx.HTTPError as e:
                log.warning(f"[CODES] Network error fetching {game.path}: {e}")
            except httpx.TimeoutException:
                log.warning(f"[CODES] Timeout fetching {game.path}")
            except Exception as e:
                log.warning(f"[CODES] Unexpected error fetching {game.path}: {e}")
    return active
