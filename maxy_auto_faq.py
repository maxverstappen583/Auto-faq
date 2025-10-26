"""
Maxy Auto-FAQ Bot (single-file)
- Plain text replies (no embeds)
- Admin FAQ management
- JSON storage (faqs.json, config.json)
- Uses .env for token and owner/guild config
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import json
import random
from difflib import SequenceMatcher
from dotenv import load_dotenv
from typing import Optional

# ---------- Config / Environment ----------
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "1319292111325106296"))  # your ID from context
GUILD_ID_ENV = os.getenv("GUILD_ID")  # optional, for guild-scoped command registration

if not DISCORD_TOKEN:
    raise RuntimeError("Please set DISCORD_TOKEN in .env")

# Files
FAQ_FILE = "faqs.json"
CONFIG_FILE = "config.json"

# Matching threshold (0-1). Lower = more permissive matches.
DEFAULT_SIMILARITY_THRESHOLD = 0.60

# Fallback messages (bot will choose one randomly when no good match)
FALLBACK_MESSAGES = [
    "Hmm, I don’t have an answer for that yet. Could you try rephrasing?",
    "I’m not sure about that — maybe check `/faq list` or ask a staff member.",
    "That one’s new to me 👀 — want me to tell the team to add this question?",
    "I couldn’t find anything on that, sorry! You can open a ticket or ask staff.",
]

# Channels where the bot answers automatically.
# Stored in config.json as { "faq_channels": [<channel_id>, ...], "threshold": 0.6 }
# If empty list, the bot will not auto-reply anywhere until a channel is set.


# ---------- Utilities: JSON storage ----------
def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        # If file corrupted, back it up and return default
        try:
            os.rename(path, path + ".bak")
        except Exception:
            pass
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# Initialize storage
faqs = load_json(FAQ_FILE, {})  # dict: question -> answer
config = load_json(CONFIG_FILE, {"faq_channels": [], "threshold": DEFAULT_SIMILARITY_THRESHOLD})


# ---------- Helper functions ----------
def normalize_text(s: str) -> str:
    # Lowercase, strip, remove excessive spaces and punctuation that users commonly add.
    return " ".join(s.lower().strip().split())


def best_faq_match(message: str):
    """
    Returns (best_key, best_answer, score) if above threshold, else (None, None, best_score).
    Uses SequenceMatcher ratio for similarity.
    """
    msg_norm = normalize_text(message)
    best_key = None
    best_score = 0.0

    for key in faqs.keys():
        key_norm = normalize_text(key)
        # Compute similarity on raw strings
        score = SequenceMatcher(None, msg_norm, key_norm).ratio()
        if score > best_score:
            best_score = score
            best_key = key

    threshold = config.get("threshold", DEFAULT_SIMILARITY_THRESHOLD)
    if best_score >= threshold and best_key is not None:
        return best_key, faqs[best_key], best_score
    return None, None, best_score


def is_admin_or_owner(member: discord.Member) -> bool:
    if member is None:
        return False
    if member.id == OWNER_ID:
        return True
    try:
        return member.guild_permissions.administrator
    except Exception:
        return False


# ---------- Bot Setup ----------
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guilds = True
intents.members = True  # to check admin perms

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree  # app_commands tree for slash commands


# ---------- Events ----------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    # Sync commands (guild if provided for quick availability)
    try:
        if GUILD_ID_ENV:
            gid = int(GUILD_ID_ENV)
            await tree.sync(guild=discord.Object(id=gid))
            print(f"Synced slash commands to guild {gid}")
        else:
            await tree.sync()
            print("Synced global slash commands (may take up to an hour to appear).")
    except Exception as e:
        print("Slash command sync failed:", e)
    print("Maxy Auto-FAQ ready.")


@bot.event
async def on_message(message: discord.Message):
    # Ignore messages from bots (including itself)
    if message.author.bot:
        return

    # Only check FAQ replies in guild channels (not DMs) and only in configured channels
    if message.guild is None:
        return

    guild_cfg_channels = config.get("faq_channels", [])
    if not guild_cfg_channels:
        # No channels configured: nothing to auto-reply to
        await bot.process_commands(message)
        return

    if message.channel.id not in guild_cfg_channels:
        await bot.process_commands(message)
        return

    # Try find best FAQ match
    content = message.content or ""
    key, answer, score = best_faq_match(content)

    if key:
        # Plain text reply (no embed), reply to the user message
        reply_text = answer
        # Optionally include a short "matched" note — comment out if you want purely the answer
        # reply_text = f"{answer}\n\n(FAQ match: \"{key}\")"
        try:
            await message.reply(reply_text, mention_author=False)
        except discord.Forbidden:
            # fallback: send in channel without replying
            await message.channel.send(reply_text)
        return
    else:
        # No strong match -> send a logical fallback (randomized)
        fallback = random.choice(FALLBACK_MESSAGES)
        try:
            await message.reply(fallback, mention_author=False)
        except discord.Forbidden:
            await message.channel.send(fallback)
        return


# ---------- Slash commands group: /faq ----------
# Note: we make these server-usable slash commands and also provide prefix versions for convenience.

def admin_check(interaction: discord.Interaction) -> bool:
    # Allow if owner or guild admin
    member = interaction.user
    if member.id == OWNER_ID:
        return True
    if isinstance(member, discord.Member) and member.guild is not None:
        return member.guild_permissions.administrator
    return False


@tree.command(name="faq_add", description="Add a new FAQ entry")
@app_commands.describe(question="The question/key users will ask", answer="The plain-text answer the bot should give")
async def faq_add(interaction: discord.Interaction, question: str, answer: str):
    if not admin_check(interaction):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    key = normalize_text(question)
    if key in faqs:
        await interaction.response.send_message("That FAQ already exists. Use `/faq_view` or `/faq_remove`.", ephemeral=True)
        return
    faqs[question] = answer
    save_json(FAQ_FILE, faqs)
    await interaction.response.send_message(f"FAQ added for: {question}", ephemeral=True)


@tree.command(name="faq_remove", description="Remove an FAQ entry")
@app_commands.describe(question="The exact question/key to remove")
async def faq_remove(interaction: discord.Interaction, question: str):
    if not admin_check(interaction):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    # try removing by exact key, otherwise try normalized match
    if question in faqs:
        del faqs[question]
        save_json(FAQ_FILE, faqs)
        await interaction.response.send_message(f"Removed FAQ: {question}", ephemeral=True)
        return
    # try normalized search
    found = None
    for k in list(faqs.keys()):
        if normalize_text(k) == normalize_text(question):
            found = k
            break
    if found:
        del faqs[found]
        save_json(FAQ_FILE, faqs)
        await interaction.response.send_message(f"Removed FAQ: {found}", ephemeral=True)
        return
    await interaction.response.send_message("Could not find that FAQ.", ephemeral=True)


@tree.command(name="faq_list", description="List all FAQ questions")
async def faq_list(interaction: discord.Interaction):
    if not admin_check(interaction):
        # For non-admins, show a subset (optional). Here we let anyone view the list.
        pass
    if not faqs:
        await interaction.response.send_message("No FAQs have been added yet.", ephemeral=True)
        return
    # Build a compact list (only keys)
    keys = list(faqs.keys())
    # chunking if too long
    display = "\n".join(f"- {k}" for k in keys)
    if len(display) > 1900:
        # send as file if too long
        with open("faqs_export.txt", "w", encoding="utf-8") as f:
            f.write(display)
        await interaction.response.send_message("FAQ list is long — sending as a file.", ephemeral=True, file=discord.File("faqs_export.txt"))
        try:
            os.remove("faqs_export.txt")
        except:
            pass
        return
    await interaction.response.send_message(f"FAQs:\n{display}", ephemeral=True)


@tree.command(name="faq_view", description="View an FAQ answer")
@app_commands.describe(question="The question to view (exact or close match)")
async def faq_view(interaction: discord.Interaction, question: str):
    # Try exact
    if question in faqs:
        await interaction.response.send_message(faqs[question], ephemeral=True)
        return
    # otherwise try fuzzy best match
    best_key, best_answer, score = best_faq_match(question)
    if best_key:
        await interaction.response.send_message(f"Closest match ({score:.2f}): **{best_key}**\n\n{best_answer}", ephemeral=True)
    else:
        await interaction.response.send_message("No matching FAQ found.", ephemeral=True)


# ---------- Config commands ----------
@tree.command(name="set_faq_channel", description="Set a channel where the bot will auto-reply with FAQs")
@app_commands.describe(channel="The channel to use for auto-FAQ replies")
async def set_faq_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not admin_check(interaction):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    cfg_channels = config.get("faq_channels", [])
    if channel.id in cfg_channels:
        await interaction.response.send_message("That channel is already configured.", ephemeral=True)
        return
    cfg_channels.append(channel.id)
    config["faq_channels"] = cfg_channels
    save_json(CONFIG_FILE, config)
    await interaction.response.send_message(f"Added {channel.mention} to auto-FAQ channels.", ephemeral=True)


@tree.command(name="disable_faq_channel", description="Disable auto-FAQ replies in a channel")
@app_commands.describe(channel="The channel to remove")
async def disable_faq_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not admin_check(interaction):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    cfg_channels = config.get("faq_channels", [])
    if channel.id not in cfg_channels:
        await interaction.response.send_message("That channel isn't configured.", ephemeral=True)
        return
    cfg_channels.remove(channel.id)
    config["faq_channels"] = cfg_channels
    save_json(CONFIG_FILE, config)
    await interaction.response.send_message(f"Removed {channel.mention} from auto-FAQ channels.", ephemeral=True)


@tree.command(name="set_threshold", description="Set similarity threshold for FAQ matching (0.0 - 1.0)")
@app_commands.describe(value="A decimal between 0 and 1. Lower = more permissive")
async def set_threshold(interaction: discord.Interaction, value: float):
    if not admin_check(interaction):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    if not (0.0 <= value <= 1.0):
        await interaction.response.send_message("Threshold must be between 0.0 and 1.0", ephemeral=True)
        return
    config["threshold"] = value
    save_json(CONFIG_FILE, config)
    await interaction.response.send_message(f"Set FAQ similarity threshold to {value:.2f}", ephemeral=True)


# ---------- Prefix command fallbacks (optional) ----------
@bot.command(name="faq_add")
@commands.check_any(commands.is_owner(), commands.has_guild_permissions(administrator=True))
async def prefix_faq_add(ctx: commands.Context, question: str, *, answer: str):
    # This prefix command is only usable by bot owner or guild admins (as a convenience)
    if question in faqs:
        await ctx.reply("That FAQ already exists.")
        return
    faqs[question] = answer
    save_json(FAQ_FILE, faqs)
    await ctx.reply(f"FAQ added for: {question}")


@bot.command(name="faq_remove")
@commands.check_any(commands.is_owner(), commands.has_guild_permissions(administrator=True))
async def prefix_faq_remove(ctx: commands.Context, *, question: str):
    if question in faqs:
        del faqs[question]
        save_json(FAQ_FILE, faqs)
        await ctx.reply(f"Removed FAQ: {question}")
        return
    # try normalized match remove
    found = None
    for k in list(faqs.keys()):
        if normalize_text(k) == normalize_text(question):
            found = k
            break
    if found:
        del faqs[found]
        save_json(FAQ_FILE, faqs)
        await ctx.reply(f"Removed FAQ: {found}")
        return
    await ctx.reply("Could not find that FAQ.")


@bot.command(name="faq_list")
async def prefix_faq_list(ctx: commands.Context):
    if not faqs:
        await ctx.reply("No FAQs have been added yet.")
        return
    keys = list(faqs.keys())
    display = "\n".join(f"- {k}" for k in keys)
    if len(display) > 1900:
        await ctx.send("FAQ list is long; sending as a file.")
        with open("faqs_export.txt", "w", encoding="utf-8") as f:
            f.write(display)
        await ctx.send(file=discord.File("faqs_export.txt"))
        try:
            os.remove("faqs_export.txt")
        except:
            pass
        return
    await ctx.reply(f"FAQs:\n{display}")


@bot.command(name="faq_view")
async def prefix_faq_view(ctx: commands.Context, *, question: str):
    if question in faqs:
        await ctx.reply(faqs[question])
        return
    best_key, best_answer, score = best_faq_match(question)
    if best_key:
        await ctx.reply(f"Closest match ({score:.2f}): {best_key}\n\n{best_answer}")
    else:
        await ctx.reply("No matching FAQ found.")


# ---------- Run ----------
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
