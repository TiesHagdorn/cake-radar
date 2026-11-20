# Cake Radar — Run on Slack

A version of Cake Radar that runs on Slack's own infrastructure, so Slack
messages are not sent to a self-hosted server. Only messages that match a cake
word are sent to OpenAI.

## How it works

1. `triggers/message_posted_trigger.ts` fires on every new top-level human
   message in channels the app has been invited to. Its filter drops app
   messages and thread replies.
2. `functions/detect_treat_function.ts` skips the alert channel, private
   channels, DMs and duplicates, then looks for words from
   `../cake_radar/keywords.json`.
3. On a match it asks the OpenAI classifier, with the first image if there is
   one. A confident "yes" goes to the four-judge panel, which only blocks the
   alert when at least three judges overturn it. A "no" gets a second look from
   the rescue judge.
4. Alerts are posted to `ALERT_CHANNEL_ID`.

The prompts in `functions/prompts.ts` are copied from `cake_radar/config.py`.
Keep them in sync.

Not supported yet, compared with the Python bot: re-checking edited messages
(Run on Slack has no edit event), summoning Cake Radar by tag or DM, and HEIC
images (they are skipped). Logs do not contain message text or user names.

## Setup

Requires the Slack CLI, Deno, and admin approval to install the app.

```bash
slack login
slack deploy
slack env add OPENAI_API_KEY <key>
slack trigger create --trigger-def triggers/message_posted_trigger.ts
```

Invite the app to every channel it should watch.

Optional env vars (defaults in `functions/config.ts`): `ALERT_CHANNEL_ID`,
`OPERATIONAL_ALERT_CHANNEL_ID`, `OPERATIONAL_ALERT_SUPPORT_MENTION`,
`IGNORED_CHANNEL_IDS` (comma-separated), `OPENAI_MODEL`, `JUDGE_MODEL`,
`CERTAINTY_THRESHOLD`.

Logs: `slack activity --tail`.

## Development

```bash
deno task test
deno check manifest.ts triggers/*.ts functions/*.ts
```

`slack run` runs a local dev version and reads `.env` in this folder. Slack
gives a local function 15 seconds and a deployed one 60 seconds. The classifier
plus judge panel often takes longer than 15 seconds, so local runs may log
`event_dispatch_failed` even though the alert is still posted.
