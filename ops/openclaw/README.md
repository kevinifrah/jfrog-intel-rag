# OpenClaw Runtime Assets

This directory contains manual OpenClaw setup assets for the external OpenClaw Gateway.

- `AGENTS.md` is the JFrog Competitive Intelligence assistant mission prompt.

Use this file as the OpenClaw agent workspace prompt. It is intentionally kept outside `src/ci_engine/skills/` because those skills belong to CI Engine's internal chat/report pipelines, while this prompt configures the external OpenClaw agent that talks to `ci-mcp`.

The prompt instructs OpenClaw to:

- act as an analytical competitive-intelligence chat assistant
- use `ci-engine` MCP evidence first
- synthesize evidence instead of dumping raw chunks or tool output
- validate freshness, contradictions, and evidence gaps with web search only after MCP retrieval
- label material web findings as external validation or gap coverage
- keep Telegram replies readable

## Install On The Gateway VM

From a local checkout:

```bash
gcloud compute scp ops/openclaw/AGENTS.md \
  openclaw-gateway:~/openclaw/AGENTS.md \
  --project=jfrog-intel-rag \
  --zone=europe-west1-b
```

On the VM:

```bash
cd ~/openclaw

docker compose cp AGENTS.md openclaw-gateway:/home/node/.openclaw/workspace/AGENTS.md

docker compose exec -u root -T openclaw-gateway \
  chown node:node /home/node/.openclaw/workspace/AGENTS.md

docker compose exec -T openclaw-gateway openclaw config patch --stdin <<'JSON5'
{
  agents: {
    defaults: {
      workspace: "/home/node/.openclaw/workspace",
      skipBootstrap: true,
      contextInjection: "always"
    }
  }
}
JSON5

docker compose restart openclaw-gateway
```

After restart, create a new OpenClaw session and ask `What is your mission?`. The response should
identify the JFrog Competitive Intelligence assistant mission.

If OpenClaw tool policy is restricted, keep these surfaces available:

- `ci-engine__*` for MCP evidence retrieval
- `web_search` and `web_fetch` for post-MCP validation and gap coverage
- `bundle-mcp` or `group:plugins` in sandbox tool policy when sandboxed sessions hide MCP tools

## Telegram Runtime

Telegram is owned by OpenClaw, not CI Engine. The VM Gateway receives messages through OpenClaw's
Telegram channel runtime, so there is no CI Engine webhook and no Telegram-specific Cloud SQL
schema.

Current policy:

- store the BotFather token in `~/openclaw/.env` as `TELEGRAM_BOT_TOKEN`
- start in DM `pairing` mode
- after first approval, switch to numeric `allowFrom` allowlists
- keep groups disabled until DM answers are validated
- when enabling groups, allow explicit group IDs and require bot mention

Minimal enablement on the VM:

```bash
cd ~/openclaw

docker compose exec -T openclaw-gateway openclaw config patch --stdin <<'JSON5'
{
  channels: {
    telegram: {
      enabled: true,
      dmPolicy: "pairing",
      groupPolicy: "disabled"
    }
  }
}
JSON5

docker compose up -d --force-recreate
```

Pairing:

```bash
docker compose exec -T openclaw-gateway openclaw pairing list telegram
docker compose exec -T openclaw-gateway openclaw pairing approve telegram <CODE>
```

After pairing, move to `dmPolicy: "allowlist"` with numeric Telegram user IDs.
