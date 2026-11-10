# HoYoverse Code Redeemer

Automated redemption tool for HoYoverse game codes (Genshin Impact, Honkai: Star Rail, Zenless Zone Zero).

## Features

- 🔄 **Auto-fetch codes** from GitHub repository (Genshin Impact & Star Rail)
- 🗄️ **hoyo-api** integration for account management
- 🔔 **Discord webhook** notifications for redemption results
- ⚡ **Async/concurrent** processing with configurable parallelism
- 🔒 **Type-safe** with mypy strict mode
- 🎨 **Rich terminal UI** with status tables
- ✅ **Tested** with 37 unit tests and 81% coverage
- 🤖 **CI/CD** with automated quality checks

## Prerequisites

- Python 3.13+
- A running [hoyo-api](https://github.com/hoyotod/hoyo-api) instance with game accounts stored
- [uv](https://github.com/astral-sh/uv) package manager

## Quick Start

### Installation

```bash
git clone <repository-url>
cd redeem
cp .env.example .env
# Edit .env with your configuration
uv sync
```

### Configuration

Create a `.env` file based on `.env.example`:

```env
# Required
HOYO_API_URL=https://hoyo-api.haiueom.workers.dev
HOYO_API_KEY=your-service-api-key

# Optional
MAX_PARALLEL=5
LOG_LEVEL=INFO
LOCALE=en-us
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

# Feature flags
NO_GENSHIN=False
NO_STARRAIL=False
NO_ZZZ=False
```

**Environment Variables:**

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `HOYO_API_URL` | ✅ Yes | - | hoyo-api base URL |
| `HOYO_API_KEY` | ✅ Yes | - | hoyo-api `SERVICE_API_KEY` (sent as `X-API-Key`) |
| `MAX_PARALLEL` | No | 5 | Concurrent redemptions (1-50) |
| `LOG_LEVEL` | No | INFO | Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL) |
| `LOCALE` | No | en-us | Game client language |
| `DISCORD_WEBHOOK_URL` | No | - | Discord webhook for notifications |
| `TELEGRAM_BOT_TOKEN` | No | - | Telegram bot token for notifications |
| `TELEGRAM_CHAT_ID` | No | - | Telegram chat ID for notifications |
| `NO_GENSHIN` | No | False | Disable Genshin Impact |
| `NO_STARRAIL` | No | False | Disable Honkai: Star Rail |
| `NO_ZZZ` | No | False | Disable Zenless Zone Zero |

### Usage

```bash
# Auto-fetch and redeem codes from GitHub
uv run main.py -a

# Force redeem (ignore used history)
uv run main.py -a -f

# Manual codes for specific games
uv run main.py -gi CODE1 CODE2
uv run main.py -sr CODE3 CODE4
uv run main.py -zz CODE5 CODE6

# Mix auto and manual
uv run main.py -a -gi EXTRA_CODE
```

## Account Source

Accounts are read from `GET {HOYO_API_URL}/api/internal/accounts` on hoyo-api
(authenticated with `X-API-Key: HOYO_API_KEY`). Each row provides `name`,
`accountId`, and a plaintext `cookieToken`, which is formatted as
`account_id_v2={accountId}; cookie_token_v2={cookieToken}`.

## Status Codes

| Symbol | Status | Description |
|--------|--------|-------------|
| ✅ | Success | Code redeemed successfully |
| 🟡 | Claimed | Code already claimed |
| ☠ | Invalid | Invalid or expired code |
| ⏱ | Cooldown | Rate limit active |
| ❌ | Failed | Redemption failed |

## Development

### Install Dev Dependencies

```bash
uv sync --extra dev
```

### Code Quality

```bash
# Format code
uv run ruff format .

# Lint code
uv run ruff check .

# Type check
uv run mypy utils.py main.py
```

### Testing

```bash
# Run all tests
uv run pytest tests/ -v

# Run with coverage
uv run pytest tests/ --cov --cov-report=term-missing

# Run specific test file
uv run pytest tests/test_utils.py -v
```

**Test Suite:**
- 37 tests across `test_main.py` and `test_utils.py`
- 81% code coverage
- Async functions tested with mocks
- Settings validation and error handling coverage

### Project Structure

```
.
├── main.py                    # Entry point and orchestration
├── utils.py                   # Core logic, models, and helpers
├── tests/                     # Test suite (37 tests, 81% coverage)
│   ├── conftest.py           # Shared fixtures
│   ├── test_main.py          # Main module tests
│   └── test_utils.py         # Utils module tests
├── .github/workflows/
│   ├── ci.yml                # CI workflow (lint, test, typecheck)
│   └── redeem.yml            # Auto-redeem workflow (daily)
├── pyproject.toml            # Dependencies and tooling config
├── .env.example              # Environment template
└── AGENTS.md                 # Detailed architecture documentation
```

## GitHub Actions

### Auto Redeem Workflow

The workflow runs every 3 hours automatically.

**Setup:**

1. Add `HOYO_API_KEY` to repository secrets and `HOYO_API_URL` to repository variables
2. (Optional) Add `DISCORD_WEBHOOK_URL` for notifications
3. Configure repository variables:
   - `MAX_PARALLEL` (default: 5)
   - `LOCALE` (default: en-us)
   - `NO_GENSHIN`, `NO_STARRAIL`, `NO_ZZZ` (default: false)

**Manual Trigger:**

Go to Actions → Auto Redeem Codes → Run workflow

- Enable auto-fetch (`-a`)
- Enable force mode (`-f`)
- Add manual codes

### CI Workflow

Runs automatically on:
- Push to `main` branch
- Pull requests

**Checks:**
- ✅ Code formatting (`ruff format --check`)
- ✅ Linting (`ruff check`)
- ✅ Type checking (`mypy`)
- ✅ Tests with coverage (`pytest`)

Pull requests include coverage report artifacts.

## Architecture

For detailed architecture documentation, see [AGENTS.md](./AGENTS.md).

**Key Components:**
- **Account Source**: hoyo-api (`GET /api/internal/accounts`)
- **HTTP Client**: `httpx` for async code fetching and account lookup
- **Notifications**: Discord webhooks with chunked messages
- **Type Safety**: Full type hints with mypy strict mode
- **Testing**: pytest with async support and 81% coverage
- **CI/CD**: Automated quality checks on every PR

## Troubleshooting

**No cookies found:**
- Verify `HOYO_API_URL` and `HOYO_API_KEY` are correct
- Check that hoyo-api has game accounts stored
- Ensure `accountId` and `cookieToken` are not null in the API response

**Connection errors:**
- Verify hoyo-api is running and reachable
- Check the `SERVICE_API_KEY` secret matches `HOYO_API_KEY`
- Validate the base URL (must start with `http://` or `https://`)

**Rate limits (⏱):**
- Wait 5 minutes between redemptions
- Reduce `MAX_PARALLEL` setting

## License

See [LICENSE](./LICENSE) for details.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run code quality checks (`ruff`, `mypy`)
5. Run tests (`pytest tests/ -v`)
6. Submit a pull request

All PRs must pass CI checks (formatting, linting, type checking, tests).

---

**Version:** 0.2.0
**Python:** 3.13+
**Last Updated:** 2026-09-10
