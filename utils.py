import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from enum import Enum
from re import sub

import asyncpg
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

    # Database
    DATABASE_URL: str

    # Webhooks
    DISCORD_WEBHOOK_URL: str | None = None

    # Feature Flags
    NO_GENSHIN: bool = False
    NO_STARRAIL: bool = False
    NO_ZZZ: bool = False

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        if not v.startswith(("postgresql://", "postgres://")):
            raise ValueError(
                "DATABASE_URL must be a valid PostgreSQL connection string"
            )
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

# Database connection pool
_db_pool: asyncpg.Pool | None = None


async def get_db_pool() -> asyncpg.Pool:
    """Get or create database connection pool."""
    global _db_pool
    if _db_pool is None:
        _db_pool = await asyncpg.create_pool(
            dsn=settings.DATABASE_URL,
            min_size=1,
            max_size=5,
            timeout=10.0,
        )
    return _db_pool


async def close_db_pool() -> None:
    """Close database connection pool."""
    global _db_pool
    if _db_pool:
        await _db_pool.close()
        _db_pool = None


async def get_cookies_from_db() -> list[CookieInfo]:
    """Fetch account cookies from PostgreSQL database.

    Queries the Account table (Prisma schema) and formats cookies
    for HoYoverse API authentication.

    Returns:
        List of CookieInfo objects with formatted cookie strings.
        Empty list if DATABASE_URL not configured or query fails.
    """
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                'SELECT name, "accountId", "cookieToken" FROM "Account" ORDER BY name'
            )

        cookies = []
        for idx, row in enumerate(rows, 1):
            name = row["name"]
            account_id = row["accountId"]
            cookie_token = row["cookieToken"]
            safe_name = format_name(name)
            env_name = f"ACC{idx}_{safe_name}"

            if not account_id or not cookie_token:
                log.warning(f"[COOKIE] Data tidak lengkap untuk akun {env_name}, skip.")
                continue

            cookie_str = f"account_id_v2={account_id}; cookie_token_v2={cookie_token}"
            cookies.append(CookieInfo(env_name=env_name, cookies=cookie_str))

        log.info(f"[COOKIE] Berhasil memuat {len(cookies)} akun dari database.")
        return cookies

    except asyncpg.PostgresError as e:
        log.error(f"[COOKIE] Database error: {e}")
        return []
    except Exception as e:
        log.error(f"[COOKIE] Unexpected error: {e}")
        return []


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
