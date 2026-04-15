# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A Slack bot that monitors workspace messages for treat offerings (cakes, snacks, etc.) using a two-stage pipeline: keyword matching followed by OpenAI LLM classification. Alerts are posted to `#cake-radar`.

## Running

```bash
# Start the bot (Flask on port 3000)
python cake-radar.py

# Test a message locally (no Slack needed)
python cake-radar.py --test "I brought cake to the office"

# Interactive REPL mode
python cake-radar.py --interactive
```

## Testing

```bash
# Deduplication logic (unittest) — also runs in CI
python tests/test_dedup.py

# Keyword matching + AI response parsing (pytest)
pytest tests/test_radar.py
```

CI only runs `test_dedup.py`. `test_radar.py` is not in the CI workflow.

## Architecture

**Two-stage classification pipeline:**
1. Keyword match against `keywords.json` (~127 base terms + auto-generated plurals)
2. OpenAI API call (text + optional image) — only triggered after a keyword match

**Key files:**
- `cake-radar.py` — all bot logic, Slack event handlers, Flask server, CLI entrypoint
- `config.py` — `Config` class: env vars, AI prompts, model name, thresholds, channel IDs
- `keywords.json` — treat/event keyword list

**In-memory state (no database):**
- `processed_messages` — `deque(maxlen=1000)` for Slack retry deduplication
- `evaluated_messages` — `dict[(channel_id, ts) → set[keywords]]` to suppress duplicate logs/alerts on edited messages

**Slack integration:** Uses `slack_bolt` with a Flask adapter. Events arrive via HTTP POST to `/slack/events`.

## Environment Variables

Required in `.env`:
```
SLACK_BOT_TOKEN=
SLACK_SIGNING_SECRET=
OPENAI_API_KEY=
```

Optional overrides (see `config.py` for defaults): `OPENAI_MODEL`, `CERTAINTY_THRESHOLD`, `CAKE_RADAR_CHANNEL_ID`, `ALERT_CHANNEL`, `PORT`.

## Import Note

The main file is named `cake-radar.py` (hyphen), making it non-importable via standard `import`. Tests work around this with `__import__('cake-radar')`.
