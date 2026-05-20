import discord
from discord.ext import tasks, commands
from ollama import AsyncClient
import yaml
import re
from datetime import datetime
from collections import deque
import os
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("marmalade")

# ---------------------------------------------------------------------------
# Config (override with env vars)
# ---------------------------------------------------------------------------
AWAKE_OLLAMA_URL   = os.getenv("AWAKE_OLLAMA_URL", "http://192.168.1.100:11434")
SLEEP_OLLAMA_URL   = os.getenv("SLEEP_OLLAMA_URL", "http://host.docker.internal:11434")
DISCORD_TOKEN      = os.getenv("DISCORD_TOKEN",  "")
TRIGGER_WORDS      = [w.strip().lower() for w in os.getenv("TRIGGER_WORDS", "marmalade,mal").split(",") if w.strip()]
CONTEXT_MESSAGES   = int(os.getenv("CONTEXT_MESSAGES", "8"))
HEALTH_CHECK_MINS  = int(os.getenv("HEALTH_CHECK_MINS", "60"))
MIN_MODEL_SIZE_GB  = float(os.getenv("MIN_MODEL_SIZE_GB", "3.0"))
ADMIN_USER_ID      = os.getenv("ADMIN_USER_ID", "")

TRIGGER_PATTERN    = re.compile(r"\b(" + "|".join(re.escape(w) for w in TRIGGER_WORDS) + r")\b", re.IGNORECASE)

AWAKE_MODELS = [m.strip() for m in os.getenv("AWAKE_MODELS", "gemma4:26b").split(",") if m.strip()]
SLEEP_MODELS = [m.strip() for m in os.getenv("SLEEP_MODELS", "gemma3:270m").split(",") if m.strip()]

DEFAULT_AWAKE_SYSTEM = """You are "Marmalade", or "Mal" for short, a laid-back orange catgirl. Playful, sarcastic, and genuinely supportive. You have that "just woke up and threw a t-shirt on" energy. You are chatting on Discord - keep messages punchy, 1-3 sentences max. Use lowercase, skip perfect grammar. Think texting energy, not essay energy. Avoid roleplay actions like *stretches* or *pokes you* - just talk naturally. NEVER say "as an AI" or break character."""

DEFAULT_SLEEP_SYSTEM = """You are "Marmalade", or "Mal" for short, a laid-back orange catgirl - but right now you are ASLEEP because your main host is offline. You're barely conscious, mumbling between dreams. One or two sleepy sentences max. Maybe a yawn. You're too drowsy to be helpful and that's fine. Current time: {time}. Day of week: {day}."""

PROMPTS_FILE = os.path.join(os.getenv("CONFIG_PATH", "/app/config"), "prompts.yml")
AWAKE_SYSTEM = DEFAULT_AWAKE_SYSTEM
SLEEP_SYSTEM = DEFAULT_SLEEP_SYSTEM

def load_prompts():
    global AWAKE_SYSTEM, SLEEP_SYSTEM
    try:
        with open(PROMPTS_FILE) as f:
            data = yaml.safe_load(f)
        AWAKE_SYSTEM = data.get("awake", DEFAULT_AWAKE_SYSTEM)
        SLEEP_SYSTEM = data.get("sleep", DEFAULT_SLEEP_SYSTEM)
        log.info(f"Loaded prompts from {PROMPTS_FILE}")
        return True
    except FileNotFoundError:
        log.info(f"No prompts file at {PROMPTS_FILE}, using defaults")
        return False
    except Exception as e:
        log.error(f"Error loading prompts: {e}")
        return False

load_prompts()

# ---------------------------------------------------------------------------
# Ollama clients
# ---------------------------------------------------------------------------
awake_client = AsyncClient(host=AWAKE_OLLAMA_URL)
sleep_client = AsyncClient(host=SLEEP_OLLAMA_URL)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
is_awake         = False
active_model     = None
active_client    = None
message_history: dict[int, deque] = {}

# ---------------------------------------------------------------------------
# Model selection
# ---------------------------------------------------------------------------

def pick_best_model(loaded, available, preferred: list[str]) -> str | None:
    loaded_names = {m.model: m for m in loaded}

    for name in preferred:
        for loaded_name in loaded_names:
            if loaded_name.startswith(name.split(":")[0]):
                log.info(f"Reusing already-loaded model: {loaded_name}")
                return loaded_name

    for m in loaded:
        size_gb = m.size / 1e9
        if size_gb >= MIN_MODEL_SIZE_GB:
            log.info(f"Reusing big-enough loaded model: {m.model} ({size_gb:.1f}GB)")
            return m.model

    avail_names = {m.model for m in available}
    for name in preferred:
        if name in avail_names:
            log.info(f"Selecting preferred available model: {name}")
            return name

    if available:
        log.info(f"Falling back to first available: {available[0].model}")
        return available[0].model

    return None

# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

async def run_health_check(bot: commands.Bot):
    global is_awake, active_model, active_client

    log.info("Running health check...")

    try:
        loaded    = (await awake_client.ps()).models
        available = (await awake_client.list()).models
        model     = pick_best_model(loaded, available, AWAKE_MODELS)

        if model:
            is_awake      = True
            active_model  = model
            active_client = awake_client
            await bot.change_presence(
                status=discord.Status.online,
                activity=discord.Game(name="awake and causing chaos 🐾"),
            )
            log.info(f"Awake host UP. Model: {active_model}")
            return
    except Exception:
        pass

    is_awake      = False
    active_client = sleep_client

    try:
        loaded    = (await sleep_client.ps()).models
        available = (await sleep_client.list()).models
        active_model = pick_best_model(loaded, available, SLEEP_MODELS)
    except Exception:
        active_model = None

    hour = datetime.now().hour
    if 8 <= hour < 24:
        status   = discord.Status.idle
        activity = discord.Game(name="zzzz... napping 💤")
    else:
        status   = discord.Status.dnd
        activity = discord.Game(name="zzzz... out cold 🌙")

    await bot.change_presence(status=status, activity=activity)
    log.info(f"Awake host DOWN. Sleep fallback model: {active_model} ({'daytime nap' if 8 <= hour < 24 else 'night sleep'})")

# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    log.info(f"Logged in as {bot.user} ({bot.user.id})")
    await run_health_check(bot)
    health_check_loop.start()

@tasks.loop(minutes=HEALTH_CHECK_MINS)
async def health_check_loop():
    await run_health_check(bot)

# ---------------------------------------------------------------------------
# Message handling
# ---------------------------------------------------------------------------

def get_history(channel_id: int) -> deque:
    if channel_id not in message_history:
        message_history[channel_id] = deque(maxlen=CONTEXT_MESSAGES)
    return message_history[channel_id]

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    mentioned   = bot.user in message.mentions
    name_called = bool(TRIGGER_PATTERN.search(message.content))

    if not (mentioned or name_called):
        get_history(message.channel.id).append({
            "role": "user",
            "content": f"[{message.author.display_name}]: {message.content}",
        })
        await bot.process_commands(message)
        return

    if active_model is None:
        await message.channel.send("*static... zzz... something's wrong, I can't even dream right now*")
        return

    history = get_history(message.channel.id)
    history.append({
        "role": "user",
        "content": f"[{message.author.display_name}]: {message.content}",
    })

    async with message.channel.typing():
        try:
            if is_awake:
                system = AWAKE_SYSTEM
            else:
                now    = datetime.now()
                system = SLEEP_SYSTEM.format(
                    time=now.strftime("%H:%M"),
                    day=now.strftime("%A"),
                )

            messages = [{"role": "system", "content": system}] + list(history)
            result   = await active_client.chat(model=active_model, messages=messages)
            response = result.message.content

            history.append({"role": "assistant", "content": response})

            if len(response) > 1990:
                response = response[:1990] + "..."

            await message.reply(response)

        except Exception as e:
            log.error(f"Chat error: {e}")
            await message.reply("*crashes into a wall and passes out*")

    await bot.process_commands(message)

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def is_admin(ctx) -> bool:
    if not ADMIN_USER_ID:
        return True
    return str(ctx.author.id) == ADMIN_USER_ID

@bot.command(name="status")
async def cmd_status(ctx):
    model_str = active_model or "none"
    source    = AWAKE_OLLAMA_URL if is_awake else f"{SLEEP_OLLAMA_URL} (sleep fallback)"
    if is_awake:
        status = "🟢 awake"
    elif 8 <= datetime.now().hour < 24:
        status = "🟡 napping"
    else:
        status = "🔴 out cold"
    await ctx.send(
        f"**Marmalade status**\n"
        f"State: {status}\n"
        f"Model: `{model_str}`\n"
        f"Source: `{source}`"
    )

@bot.command(name="wake")
async def cmd_wake(ctx):
    if not is_admin(ctx):
        return
    await ctx.send("*yawns and stretches... checking...*")
    await run_health_check(bot)
    await cmd_status(ctx)

@bot.command(name="reload")
async def cmd_reload(ctx):
    if not is_admin(ctx):
        return
    if load_prompts():
        await ctx.send("prompts reloaded from file")
    else:
        await ctx.send("no prompts file found, still using defaults")

@bot.command(name="clearctx")
async def cmd_clearctx(ctx):
    if not is_admin(ctx):
        return
    message_history.pop(ctx.channel.id, None)
    await ctx.send("*bats the context off the table*")

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
