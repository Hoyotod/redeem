# HoYoverse Code Redeemer

Automated redemption tool for HoYoverse game codes (Genshin Impact, Honkai: Star Rail, Zenless Zone Zero).

## Features

- 🔄 **Auto-fetch codes** from GitHub repository (Genshin Impact & Star Rail)
- 🗄️ **PostgreSQL database** integration for account management
- 🔔 **Discord webhook** notifications for redemption results
- ⚡ **Async/concurrent** processing with configurable parallelism
- 🔒 **Type-safe** with mypy strict mode
- 🎨 **Rich terminal UI** with status tables

## Prerequisites

- Python 3.13+
- PostgreSQL database
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
DATABASE_URL=postgresql://user:password@host:5432/dbname

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
| `DATABASE_URL` | ✅ Yes | - | PostgreSQL connection string |
| `MAX_PARALLEL` | No | 5 | Concurrent redemptions (1-50) |
| `LOG_LEVEL` | No | INFO | Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL) |
| `LOCALE` | No | en-us | Game client language |
| `DISCORD_WEBHOOK_URL` | No | - | Discord webhook for notifications |
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

## Database Schema

The script expects a PostgreSQL database with the following schema:

```sql
CREATE TABLE "Account" (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  "accountId" TEXT NOT NULL UNIQUE,
  "cookieToken" TEXT NOT NULL,
  "userId" TEXT NOT NULL,
  "createdAt" TIMESTAMP DEFAULT NOW(),
  "updatedAt" TIMESTAMP DEFAULT NOW()
);
```

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

### Project Structure

```
.
├── main.py           # Entry point and orchestration
├── utils.py          # Core logic, models, and helpers
├── pyproject.toml    # Dependencies and tooling config
├── .env.example      # Environment template
└── AGENTS.md         # Detailed architecture documentation
```

## GitHub Actions

The workflow runs daily at 18:00 UTC automatically.

### Setup

1. Add `DATABASE_URL` to repository secrets
2. (Optional) Add `DISCORD_WEBHOOK_URL` for notifications
3. Configure repository variables:
   - `MAX_PARALLEL` (default: 5)
   - `LOCALE` (default: en-us)
   - `NO_GENSHIN`, `NO_STARRAIL`, `NO_ZZZ` (default: false)

### Manual Trigger

Go to Actions → Auto Redeem Codes → Run workflow

- Enable auto-fetch (`-a`)
- Enable force mode (`-f`)
- Add manual codes

## Architecture

For detailed architecture documentation, see [AGENTS.md](./AGENTS.md).

**Key Components:**
- **Database**: PostgreSQL with connection pooling (`asyncpg`)
- **HTTP Client**: `httpx` for async code fetching
- **Notifications**: Discord webhooks with chunked messages
- **Type Safety**: Full type hints with mypy strict mode

## Troubleshooting

**No cookies found:**
- Verify `DATABASE_URL` is correct
- Check that `Account` table has records
- Ensure `accountId` and `cookieToken` are not null

**Connection errors:**
- Verify PostgreSQL is running
- Check network connectivity
- Validate connection string format

**Rate limits (⏱):**
- Wait 5 minutes between redemptions
- Reduce `MAX_PARALLEL` setting

## License

See [LICENSE](./LICENSE) for details.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run code quality checks
5. Submit a pull request

---

**Version:** 0.2.0  
**Python:** 3.13+  
**Last Updated:** 2026-09-06
