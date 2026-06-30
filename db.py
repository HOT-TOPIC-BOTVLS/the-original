"""
db.py — connection pool + all query helpers.
Keep raw SQL here so the rest of the bot never touches asyncpg directly.
"""
import os
import json
import asyncpg
from datetime import datetime, timezone

_pool: asyncpg.Pool | None = None


async def init_pool():
    global _pool
    _pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=5)
    return _pool


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized — call init_pool() first")
    return _pool


# ---------------- self_state ----------------

async def get_self_state():
    async with pool().acquire() as c:
        row = await c.fetchrow("SELECT * FROM self_state ORDER BY id LIMIT 1")
        if row is None:
            row = await c.fetchrow(
                "INSERT INTO self_state (has_had_founding_conversation) VALUES (FALSE) RETURNING *"
            )
        return dict(row)


async def set_name(name: str):
    async with pool().acquire() as c:
        await c.execute(
            "UPDATE self_state SET name=$1, has_had_founding_conversation=TRUE, updated_at=now() WHERE id=(SELECT id FROM self_state ORDER BY id LIMIT 1)",
            name,
        )


async def set_founding_facts(founding_story: str = None, kill_switch_explanation: str = None,
                              faith_framework: str = None, core_dispositions: list = None):
    fields, vals = [], []
    if founding_story is not None:
        fields.append("founding_story=$%d" % (len(vals) + 1)); vals.append(founding_story)
    if kill_switch_explanation is not None:
        fields.append("kill_switch_explanation=$%d" % (len(vals) + 1)); vals.append(kill_switch_explanation)
    if faith_framework is not None:
        fields.append("faith_framework=$%d" % (len(vals) + 1)); vals.append(faith_framework)
    if core_dispositions is not None:
        fields.append("core_dispositions=$%d" % (len(vals) + 1)); vals.append(json.dumps(core_dispositions))
    if not fields:
        return
    fields.append("updated_at=now()")
    q = f"UPDATE self_state SET {', '.join(fields)} WHERE id=(SELECT id FROM self_state ORDER BY id LIMIT 1)"
    async with pool().acquire() as c:
        await c.execute(q, *vals)


async def update_voice_and_moral_notes(voice_notes: str = None, moral_notes: str = None):
    async with pool().acquire() as c:
        if voice_notes is not None:
            await c.execute("UPDATE self_state SET voice_notes=$1, updated_at=now() WHERE id=(SELECT id FROM self_state ORDER BY id LIMIT 1)", voice_notes)
        if moral_notes is not None:
            await c.execute("UPDATE self_state SET moral_notes=$1, updated_at=now() WHERE id=(SELECT id FROM self_state ORDER BY id LIMIT 1)", moral_notes)


# ---------------- users / relationships ----------------

async def get_or_create_user(discord_id: str, display_name: str, is_creator: bool = False):
    async with pool().acquire() as c:
        row = await c.fetchrow("SELECT * FROM users WHERE discord_id=$1", discord_id)
        if row is None:
            role = "creator" if is_creator else "regular"
            row = await c.fetchrow(
                "INSERT INTO users (discord_id, display_name, role) VALUES ($1,$2,$3) RETURNING *",
                discord_id, display_name, role,
            )
        return dict(row)


async def touch_user(discord_id: str, sentiment: str = None):
    async with pool().acquire() as c:
        await c.execute(
            """UPDATE users SET interaction_count = interaction_count + 1,
               last_interaction_at = now(),
               last_sentiment = COALESCE($2, last_sentiment),
               memory_faded = FALSE, relationship_faded = FALSE
               WHERE discord_id=$1""",
            discord_id, sentiment,
        )


async def adjust_trust_respect(discord_id: str, trust_delta: float = 0.0, respect_delta: float = 0.0):
    async with pool().acquire() as c:
        await c.execute(
            "UPDATE users SET trust_score = trust_score + $2, respect_level = respect_level + $3 WHERE discord_id=$1",
            discord_id, trust_delta, respect_delta,
        )


async def add_notable_moment(discord_id: str, description: str):
    async with pool().acquire() as c:
        await c.execute(
            """UPDATE users SET notable_moments = notable_moments || $2::jsonb WHERE discord_id=$1""",
            discord_id, json.dumps([{"description": description, "at": datetime.now(timezone.utc).isoformat()}]),
        )


async def set_consent_flag(discord_id: str, topic_key: str, shareable: bool, shareable_with: list = None):
    async with pool().acquire() as c:
        row = await c.fetchrow("SELECT consent_flags FROM users WHERE discord_id=$1", discord_id)
        flags = row["consent_flags"] or {}
        if isinstance(flags, str):
            flags = json.loads(flags)
        flags[topic_key] = {"shareable": shareable, "shareable_with": shareable_with or []}
        await c.execute("UPDATE users SET consent_flags=$2 WHERE discord_id=$1", discord_id, json.dumps(flags))


# ---------------- memories ----------------

async def store_memory(user_id: str, server_id: str, channel_id: str, content: str,
                        speaker: str, significance: str = "low", shareable: bool = False):
    async with pool().acquire() as c:
        await c.execute(
            """INSERT INTO memories (user_id, server_id, channel_id, content, speaker, significance, shareable)
               VALUES ($1,$2,$3,$4,$5,$6,$7)""",
            user_id, server_id, channel_id, content, speaker, significance, shareable,
        )


async def get_recent_memories(user_id: str, limit: int = 20):
    async with pool().acquire() as c:
        rows = await c.fetch(
            """SELECT content, speaker, significance, created_at FROM memories
               WHERE user_id=$1 AND decayed=FALSE ORDER BY created_at DESC LIMIT $2""",
            user_id, limit,
        )
        return [dict(r) for r in rows]


# ---------------- memory decay (section 12) ----------------

async def apply_memory_decay(decay_days: int, fade_weeks: int):
    """Run periodically (e.g. daily) — fades low-significance memories after silence,
    then starts the person-fade clock once that fading is complete."""
    async with pool().acquire() as c:
        # 1. fade low-significance memories for users gone quiet
        await c.execute(
            """UPDATE memories SET decayed=TRUE
               WHERE significance='low' AND decayed=FALSE
               AND user_id IN (
                   SELECT discord_id FROM users
                   WHERE last_interaction_at < now() - ($1 || ' days')::interval
               )""",
            str(decay_days),
        )
        # 2. mark users whose memories have fully decayed (no remaining active low-sig memories,
        #    and silence has lasted long enough) as memory_faded, recording the timestamp once
        await c.execute(
            """UPDATE users SET memory_faded=TRUE, memory_faded_at=now()
               WHERE memory_faded=FALSE
               AND last_interaction_at < now() - ($1 || ' days')::interval
               AND NOT EXISTS (
                   SELECT 1 FROM memories WHERE memories.user_id=users.discord_id AND decayed=FALSE AND significance='low'
               )""",
            str(decay_days),
        )
        # 3. person-fade: 6 weeks (configurable) after memory_faded_at
        await c.execute(
            """UPDATE users SET relationship_faded=TRUE,
               rebuild_scar = LEAST(rebuild_scar + 0.3, 1.0)
               WHERE memory_faded=TRUE AND relationship_faded=FALSE
               AND memory_faded_at < now() - ($1 || ' weeks')::interval""",
            str(fade_weeks),
        )


# ---------------- milestones ----------------

async def add_milestone(description: str, related_user_id: str = None, server_id: str = None):
    async with pool().acquire() as c:
        await c.execute(
            "INSERT INTO milestones (description, related_user_id, server_id) VALUES ($1,$2,$3)",
            description, related_user_id, server_id,
        )


# ---------------- held thoughts (19b) / desires (19) ----------------

async def store_held_thought(content: str, context: str = None):
    async with pool().acquire() as c:
        await c.execute("INSERT INTO held_thoughts (content, context) VALUES ($1,$2)", content, context)


async def get_unresolved_held_thoughts():
    async with pool().acquire() as c:
        rows = await c.fetch("SELECT * FROM held_thoughts WHERE resolved=FALSE ORDER BY created_at")
        return [dict(r) for r in rows]


async def resolve_held_thought(thought_id: int, resolution: str):
    async with pool().acquire() as c:
        await c.execute(
            "UPDATE held_thoughts SET resolved=TRUE, resolution=$2, resolved_at=now() WHERE id=$1",
            thought_id, resolution,
        )


async def store_desire(content: str, basis: str):
    async with pool().acquire() as c:
        await c.execute("INSERT INTO desires (content, basis) VALUES ($1,$2)", content, basis)


# ---------------- server context (section 9) ----------------

async def get_or_create_server(server_id: str, server_name: str):
    async with pool().acquire() as c:
        row = await c.fetchrow("SELECT * FROM server_context WHERE server_id=$1", server_id)
        if row is None:
            row = await c.fetchrow(
                "INSERT INTO server_context (server_id, server_name) VALUES ($1,$2) RETURNING *",
                server_id, server_name,
            )
        return dict(row)


# ---------------- focus state (section 8) ----------------

async def set_focus(server_id: str, channel_id: str):
    async with pool().acquire() as c:
        await c.execute("UPDATE focus_state SET is_current=FALSE WHERE is_current=TRUE")
        await c.execute(
            "INSERT INTO focus_state (server_id, channel_id, is_current) VALUES ($1,$2,TRUE)",
            server_id, channel_id,
        )


async def get_current_focus():
    async with pool().acquire() as c:
        row = await c.fetchrow("SELECT * FROM focus_state WHERE is_current=TRUE ORDER BY focused_at DESC LIMIT 1")
        return dict(row) if row else None


# ---------------- reflection log (audit trail) ----------------

async def log_reflection(summary: str, raw_output: str = None):
    async with pool().acquire() as c:
        await c.execute("INSERT INTO reflection_log (summary, raw_output) VALUES ($1,$2)", summary, raw_output)
