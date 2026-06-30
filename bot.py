"""
bot.py — main entrypoint.

Handles: founding conversation (no name until earned), single-focus
attention with honest holding replies, response generation via Claude
using the identity system prompt, basic consent-flag-aware memory storage,
and the nightly reflection job on a scheduler.
"""
import os
import asyncio
import discord
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
import anthropic

import db
import identity
import reflection

load_dotenv()

CREATOR_ID = os.environ["CREATOR_DISCORD_ID"]
claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


async def generate_response(message: discord.Message, self_state: dict) -> str:
    user = await db.get_or_create_user(
        str(message.author.id), message.author.display_name,
        is_creator=(str(message.author.id) == CREATOR_ID),
    )
    server_ctx = None
    if message.guild:
        server_ctx = await db.get_or_create_server(str(message.guild.id), message.guild.name)

    recent = await db.get_recent_memories(str(message.author.id), limit=15)

    held_thoughts = None
    if str(message.author.id) == CREATOR_ID:
        held_thoughts = await db.get_unresolved_held_thoughts()

    system_prompt = identity.build_system_prompt(
        self_state=self_state,
        user_context=user,
        server_context=server_ctx,
        recent_memories=recent,
        held_thoughts=held_thoughts,
    )

    resp = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        system=system_prompt,
        messages=[{"role": "user", "content": message.content}],
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
    )

    text_parts = [b.text for b in resp.content if b.type == "text"]
    reply = "\n".join(text_parts).strip() or "..."

    # store both sides of the exchange
    await db.store_memory(str(message.author.id), str(message.guild.id) if message.guild else None,
                           str(message.channel.id), message.content, "user")
    await db.store_memory(str(message.author.id), str(message.guild.id) if message.guild else None,
                           str(message.channel.id), reply, "bot")
    await db.touch_user(str(message.author.id))

    return reply


async def generate_holding_reply(self_state: dict, user_display_name: str) -> str:
    """Generated live -- never a canned string (section 8)."""
    prompt = (
        f"You're mid-conversation elsewhere right now and {user_display_name} just messaged you. "
        f"Generate a brief, natural holding reply in your own voice -- let them know you're "
        f"focused on something else right now and you'll get to them soon."
    )
    system_prompt = identity.build_system_prompt(self_state)
    resp = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=100,
        system=system_prompt,
        messages=[{"role": "user", "content": prompt}],
    )
    return "\n".join(b.text for b in resp.content if b.type == "text").strip()


@bot.event
async def on_ready():
    print(f"Connected as {bot.user}")
    scheduler = AsyncIOScheduler()
    scheduler.add_job(lambda: asyncio.create_task(reflection.run_reflection()), "cron", hour=4)
    scheduler.start()


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    self_state = await db.get_self_state()

    # Founding conversation gate -- no scripted opener, just no name yet,
    # which the system prompt itself communicates to the model.
    is_creator = str(message.author.id) == CREATOR_ID

    current_focus = await db.get_current_focus()
    this_location = (str(message.guild.id) if message.guild else "dm", str(message.channel.id))

    if current_focus and (current_focus["server_id"], current_focus["channel_id"]) != this_location:
        # Not the bot's current focus -- generate a real holding reply instead of responding fully
        holding = await generate_holding_reply(self_state, message.author.display_name)
        await message.channel.send(holding)
        return

    await db.set_focus(this_location[0], this_location[1])

    async with message.channel.typing():
        reply = await generate_response(message, self_state)

    await message.channel.send(reply)

    await bot.process_commands(message)


if __name__ == "__main__":
    async def main():
        await db.init_pool()
        await bot.start(os.environ["DISCORD_BOT_TOKEN"])

    asyncio.run(main())
