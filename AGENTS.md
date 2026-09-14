# AGENTS.md

## Project Overview

Python 3.13 async script that auto-redeems HoYoverse game codes (Genshin Impact, Star Rail, ZZZ) by fetching account cookies from PostgreSQL database and codes from a GitHub repo.

**Entry point:** `main.py`  
**Core logic:** `utils.py`  
**Version:** 0.2.0

## Commands

```bash
# Install dependencies
uv sync

# Install dev dependencies (linting, type checking)
uv sync --extra dev

# Run with auto-fetch from https://github.com/Hoyotod/code
uv run main.py -a

# Force redeem (ignore used history)
uv run main.py -a -f

# Clear all used codes history (exit after clear)
uv run main.py -r

# Clear history then redeem new codes
uv run main.py -r -a

# Manual codes
uv run main.py -gi CODE1 CODE2 -sr CODE3 -zz CODE4

# Debug mode with verbose logging
LOG_LEVEL=DEBUG uv run main.py -a

# Code quality checks
uv run ruff format .        # Format code
uv run ruff check .         # Lint code
uv run mypy utils.py main.py  # Type check

# Run tests
uv run pytest tests/ -v     # Run all tests
uv run pytest tests/ --cov  # Run with coverage report
```

## Environment Setup

Copy `.env.example` to `.env` and configure:
- `DATABASE_URL` (required, PostgreSQL connection string: `postgresql://user:pass@host:5432/dbname`)
- `MAX_PARALLEL` (default: 5, controls concurrent redeem tasks, range: 1-50)
- `LOG_LEVEL` (default: INFO, options: DEBUG, INFO, WARNING, ERROR, CRITICAL)
- `LOCALE` (default: en-us, supported languages in `utils.py:check_lang`)
- `DISCORD_WEBHOOK_URL` (optional Discord notifications)
- `NO_GENSHIN` / `NO_STARRAIL` / `NO_ZZZ` (feature flags to disable specific games)

## Architecture Notes (v0.2.0)

### Key Technologies
- **HTTP Client:** `httpx` (async, replaces requests)
- **Database:** `asyncpg` with connection pooling
- **Validation:** Pydantic with field validators
- **Code Quality:** `ruff` (linting/formatting), `mypy` (type checking)
- **Testing:** `pytest` with `pytest-asyncio` and `pytest-cov` (31 tests, 77% coverage)

### Database
- Direct PostgreSQL access via `asyncpg` async connection pool
- **Table:** `Account` (Prisma schema)
- **Columns:** `name`, `"accountId"`, `"cookieToken"`
- **Query:** `SELECT name, "accountId", "cookieToken" FROM "Account" ORDER BY name`
- Connection pool: min 1, max 5 connections, 10s timeout
- Cookies formatted as: `account_id_v2={accountId}; cookie_token_v2={cookieToken}` (v2 tokens)
- **Note:** `genshin.complete_cookies()` deliberately not used (v1 endpoint rejects v2 tokens)

### Code Fetching
- Active codes fetched asynchronously from GitHub using `httpx.AsyncClient`
- Source: `https://raw.githubusercontent.com/Hoyotod/code/refs/heads/main/{game}/active.json`
- **Auto-fetch available for:** Genshin Impact, Honkai: Star Rail
- **ZZZ:** No upstream source available; use manual `-zz CODE` only
- Used codes tracked in `used/{genshin|starrail|zzz}.txt` (auto-committed by GitHub Actions)

### Discord Notifications
- Webhook messages chunked at 1900 chars max
- Filters out "No Game" and "Cookie Err" from success reports
- Cookie errors aggregated and sent as single alert at end

### Error Handling
- Specific exception types (`asyncpg.PostgresError`, `httpx.HTTPError`, `OSError`)
- Graceful degradation (empty lists on failures)
- Structured logging with configurable log levels

## GitHub Actions

### Redeem Workflow (`.github/workflows/redeem.yml`)
- Scheduled: daily at 18:00 UTC (`-a` mode)
- Manual dispatch: supports `-a`, `-f`, and custom args
- Auto-commits updated `used/*.txt` files
- Secrets required: `DATABASE_URL`, `DISCORD_WEBHOOK_URL`

### CI Workflow (`.github/workflows/ci.yml`)
- Triggers: push to main, pull requests
- Steps: format check → lint → typecheck → tests with coverage
- Uploads coverage HTML artifact on PRs
- Ensures code quality before merging

## Code Style

- 4 spaces indentation
- CRLF line endings (see `.editorconfig`)
- Uses `uv` for dependency management (not pip/poetry)
- Type hints on all functions (mypy strict mode enabled)
- Ruff for linting and formatting (line length: 88, extended rule set: E/F/I/N/W/UP/ASYNC/B/S/RUF/PT/PERF/FLY/SIM/TCH/PIE/C4)
- Google-style docstrings on core functions
- Test coverage target: >75%

## Testing

### Test Suite Structure
- **`tests/conftest.py`**: Shared fixtures (mock DB pool, HTTP client, environments)
- **`tests/test_utils.py`**: Unit tests for helpers, Settings validation, GameConfig, used codes I/O, async functions (17 tests)
- **`tests/test_main.py`**: Unit tests for argument parsing, code preparation, redeem process, game processing (14 tests)

### Running Tests
```bash
# Run all tests
uv run pytest tests/ -v

# Run with coverage report
uv run pytest tests/ --cov --cov-report=term-missing

# Run specific test file
uv run pytest tests/test_utils.py -v
```

### Coverage
- **Total:** 77% (31 tests, all passing)
- **Main modules:** `main.py` (50%), `utils.py` (70%)
- **Test modules:** 100% (test_main.py, test_utils.py)
- Uncovered: mostly error handling paths and database I/O

## Important Implementation Details

- `get_active_codes()` is **async** (must be awaited in main.py)
- Database connection pool must be closed with `close_db_pool()` before exit
- `MAX_PARALLEL` validated between 1-50 at startup
- `DATABASE_URL` validated for PostgreSQL connection string format
- Unused fields removed from `RedeemInfo` dataclass: `level`, `server`
- `CookieInfo.cookies` is `str` only (not `str | dict`)
- All async functions have corresponding test coverage with mocked dependencies
