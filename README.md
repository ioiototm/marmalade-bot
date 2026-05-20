# Marmalade

Marmalade is a Discord chatbot powered by [Ollama](https://ollama.com) that runs entirely on your own hardware. Just a work in progress, fun idea I am playing with. 

She has two modes depending on whether your main machine is available. When your powerful host is on, she uses a bigger model and has her full personality. When it's off, she falls back to a tiny model running locally and responds like she's half asleep. Her Discord status reflects this live:

- **Green** — awake, main host is up
- **Yellow** — napping, main host is off during the day (8am-midnight)
- **Red (DND)** — out cold, main host is off at night (midnight-8am)

## Setup

### 1. Create a Discord bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. **New Application** -> name it -> go to **Bot**
3. Copy the bot token
4. Under **Privileged Gateway Intents**, enable **Message Content Intent**
5. Go to **OAuth2 > URL Generator**, select scopes: `bot`, permissions: `Send Messages`, `Read Message History`, `Embed Links`
6. Open the generated URL to invite the bot to your server

### 2. Set up Ollama

The bot needs two Ollama instances — one on a powerful host (awake) and one local fallback (sleep).

**Awake host** — expose Ollama to the LAN:
```bash
OLLAMA_HOST=0.0.0.0 ollama serve
```

**Sleep host** — pull at least one tiny model:
```bash
ollama pull gemma3:270m
```

### 3. Deploy

```bash
cp .env.example .env
# edit .env with your values
docker compose up -d
```

## Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DISCORD_TOKEN` | yes | | Your bot token |
| `AWAKE_OLLAMA_URL` | yes | | Ollama endpoint on the main/powerful host |
| `SLEEP_OLLAMA_URL` | yes | | Ollama endpoint for the local fallback |
| `TRIGGER_WORDS` | no | `marmalade,mal` | Comma-separated trigger words (word-boundary matched) |
| `AWAKE_MODELS` | no | `gemma4:26b` | Models to try when awake (comma-separated, tried in order) |
| `SLEEP_MODELS` | no | `gemma3:270m` | Models to try when sleeping (comma-separated, tried in order) |
| `CONTEXT_MESSAGES` | no | `8` | Messages to remember per channel |
| `HEALTH_CHECK_MINS` | no | `60` | How often to check if awake host is up |
| `MIN_MODEL_SIZE_GB` | no | `3.0` | Ignore loaded models smaller than this |
| `CONFIG_PATH` | no | `/app/config` | Host path to mount for config files (e.g. `prompts.yml`) |
| `ADMIN_USER_ID` | no | | Your Discord user ID. Restricts `!wake`, `!reload`, `!clearctx` to you. |

## Custom prompts

You can customize Marmalade's personality without rebuilding. Copy `prompts.example.yml` to your config path as `prompts.yml`, edit it, and run `!reload` in Discord.

```bash
cp prompts.example.yml /your/config/path/prompts.yml
# edit prompts.yml
# then type !reload in Discord
```

## Commands

| Command | Description |
|---------|-------------|
| `!status` | Shows current state, model, and source |
| `!wake` | Forces an immediate health check |
| `!reload` | Reloads prompts from `prompts.yml` without restarting |
| `!clearctx` | Clears message history for the channel |

## License

Public domain under the [Unlicense](UNLICENSE). Do whatever you want with it.
