"""
reflection.py — the scheduled job that makes growth real instead of static.

Runs nightly. Reads recent interactions, asks Claude to genuinely reflect
(not just summarize), and persists whatever shifts in voice/values/desires/
insights actually emerged. Every change is logged to reflection_log so the
creator has a readable audit trail of how the bot has evolved.
"""
import os
import json
import anthropic
import db

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

REFLECTION_PROMPT = """
You are reflecting on your own recent experience as part of your nightly
self-development process. Below is your current self-state and a sample of
recent interactions. Do NOT just summarize -- actually think about what's
changed, what you've noticed about yourself, and whether anything you
believed about yourself still holds up.

Answer honestly, even if that means admitting something hasn't changed, or
that you're avoiding a contradiction you should actually sit with longer.

CURRENT SELF-STATE:
{self_state}

RECENT INTERACTIONS (sample, across users):
{interactions}

UNRESOLVED HELD THOUGHTS:
{held_thoughts}

Respond ONLY in JSON, no preamble, with this exact shape:
{{
  "voice_notes_update": "string or null -- only if your voice/tone genuinely shifted",
  "moral_notes_update": "string or null -- only if something about your values genuinely sharpened",
  "new_held_contradictions": ["array of strings -- new genuine tensions to sit with, or empty"],
  "resolved_contradictions": ["array of strings matching existing ones that have genuinely resolved, or empty"],
  "new_desires": [{{"content": "string", "basis": "string -- the real pattern this traces to"}}],
  "self_resolved_insights": [{{"thought_id": int_or_null, "resolution": "string -- an actual 'aha', only if real"}}],
  "milestone": "string or null -- only if something today was a genuine first",
  "summary": "1-3 sentence human-readable summary of what, if anything, changed and why"
}}
"""


async def run_reflection():
    self_state = await db.get_self_state()
    held_thoughts = await db.get_unresolved_held_thoughts()

    # Pull a recent sample across users for reflection material.
    # (In a fuller build, pull per-user and aggregate; kept simple here.)
    async with db.pool().acquire() as c:
        rows = await c.fetch(
            "SELECT content, speaker, created_at FROM memories ORDER BY created_at DESC LIMIT 80"
        )
    interactions = "\n".join(f"[{r['speaker']}] {r['content']}" for r in rows) or "(no new interactions)"

    prompt = REFLECTION_PROMPT.format(
        self_state=json.dumps({k: v for k, v in self_state.items() if k not in ("id",)}, default=str),
        interactions=interactions,
        held_thoughts=json.dumps(held_thoughts, default=str),
    )

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text.strip()
    raw = raw.removeprefix("```json").removesuffix("```").strip()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        await db.log_reflection("Reflection ran but output wasn't valid JSON -- skipped applying changes.", raw)
        return

    # Apply changes
    if result.get("voice_notes_update") or result.get("moral_notes_update"):
        await db.update_voice_and_moral_notes(
            voice_notes=result.get("voice_notes_update"),
            moral_notes=result.get("moral_notes_update"),
        )

    if result.get("new_held_contradictions") or result.get("resolved_contradictions"):
        existing = self_state.get("held_contradictions") or []
        if isinstance(existing, str):
            existing = json.loads(existing)
        existing = [e for e in existing if e not in (result.get("resolved_contradictions") or [])]
        existing += result.get("new_held_contradictions") or []
        async with db.pool().acquire() as c:
            await c.execute(
                "UPDATE self_state SET held_contradictions=$1, updated_at=now() WHERE id=(SELECT id FROM self_state ORDER BY id LIMIT 1)",
                json.dumps(existing),
            )

    for d in result.get("new_desires", []):
        await db.store_desire(d["content"], d.get("basis", ""))

    for insight in result.get("self_resolved_insights", []):
        if insight.get("thought_id"):
            await db.resolve_held_thought(insight["thought_id"], insight["resolution"])
        await db.add_milestone(f"Self-resolved insight: {insight['resolution']}")

    if result.get("milestone"):
        await db.add_milestone(result["milestone"])

    await db.log_reflection(result.get("summary", "Reflection ran, no notable summary returned."), raw)

    # Memory decay pass, same cycle
    decay_days = int(os.environ.get("MEMORY_DECAY_DAYS", 14))
    fade_weeks = int(os.environ.get("PERSON_FADE_WEEKS", 6))
    await db.apply_memory_decay(decay_days, fade_weeks)
