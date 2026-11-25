# News Center (MVP)

Free pipeline: **RSS → GitHub Actions collector → Library (`data/`) → Claude analyst → Telegram bot**.

- `config/sources.yaml` — 49 RSS sources (tier, category, filter)
- `config/rules.yaml` — freshness, dedup and junk/relevance rules
- `collector/collect.py` — runs every 5 min (`.github/workflows/collect.yml`)
- `ANALYST.md` — what the Claude analyst does on each run
- `sender/send.py` — posts `data/outbox.json` to Telegram (`.github/workflows/send.yml`, on push)

Secrets: `TELEGRAM_BOT_TOKEN`, optional `TELEGRAM_CHAT_ID` (default: the private chat of whoever pressed Start in the bot) (optional `SEC_USER_AGENT`, e.g. `NewsCenter you@example.com`).
Keep the repository **public** so Actions minutes are free.
