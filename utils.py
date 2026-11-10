import asyncio
import logging
import os
import re
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


@dataclass
class CookieInfo:
    env_name: str = ""
    cookies: str = ""

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

            account_id = row.get("accountId")
            cookie_token = row.get("cookieToken")

            if not account_id or not cookie_token:
                log.warning(f"[COOKIE] Data tidak lengkap untuk akun {env_name}, skip.")
                continue

            # Validate to prevent cookie injection (dangerous characters).
            account_id = str(account_id)
            cookie_token = str(cookie_token)
            if not re.fullmatch(r"[A-Za-z0-9_-]+", account_id) or not re.fullmatch(
                r"[A-Za-z0-9_-]+", cookie_token
            ):
                log.warning(
                    f"[COOKIE] Format cookie tidak valid untuk akun {env_name}, skip."
                )
                continue

            cookie_str = f"account_id_v2={account_id}; cookie_token_v2={cookie_token}"
            cookies.append(CookieInfo(env_name=env_name, cookies=cookie_str))
        except Exception as e:
            log.warning(f"[COOKIE] Gagal memproses row {idx}: {e}")
            continue

    log.info(f"[COOKIE] Berhasil memuat {len(cookies)} akun dari hoyo-api.")
    return cookies


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


def get_used_codes() -> dict[str, set[str]]:
    """Load used codes from local files.

    Returns:
        Dictionary mapping game keys to set of used codes.
    """
    used: dict[str, set[str]] = {g.key: set() for g in GAMES}
    for game in GAMES:
        try:
            if os.path.exists(f"used/{game.path}.txt"):
                with open(f"used/{game.path}.txt", encoding="utf-8") as f:
                    used[game.key] = set(f.read().splitlines())
        except OSError as e:
            log.debug(f"Could not read used codes for {game.path}: {e}")
    return used


def update_used_codes(game_key: str, codes: list[str]) -> None:
    """Append newly used codes to history file.

    Args:
        game_key: Game identifier (gi, sr, zz)
        codes: List of codes to mark as used
    """
    game = get_game_by_key(game_key)
    if not game:
        return
    try:
        os.makedirs("used", exist_ok=True)
        with open(f"used/{game.path}.txt", "a", encoding="utf-8") as f:
            for c in codes:
                f.write(f"{c}\n")
    except OSError as e:
        log.error(f"Failed to update used codes for {game_key}: {e}")


def reset_used_codes() -> None:
    """Clear all used codes history files."""
    try:
        os.makedirs("used", exist_ok=True)
        for game in GAMES:
            with open(f"used/{game.path}.txt", "w", encoding="utf-8") as f:
                f.write("")
            log.info(f"Cleared used codes for {game.name}")
    except OSError as e:
        log.error(f"Failed to reset used codes: {e}")
