# Persistent-Identity Discord Companion — Starter Build

This is a working skeleton, not a finished product. It implements the core
mechanics from the architecture spec; some of the more nuanced behavioral
rules (e.g. moderation escalation thresholds, server-health advisory, full
decay edge cases) are described in the system prompt (`identity.py`) for
Claude to reason over live, rather than hardcoded as separate functions.
That's deliberate -- judgment should come from real reasoning over the
rules, not a flowchart pretending to have personality.

## Setup

1. `pip install -r requirements.txt`
2. Create a Postgres database, run `schema.sql` against it
   (`psql $DATABASE_URL -f schema.sql`)
3. Copy `.env.example` to `.env` and fill in: Discord bot token, your
   Discord user ID (so it knows who its creator is), database URL,
   Anthropic API key.
4. `python bot.py`

## What's actually built

- **db.py** — every table from the schema, with helper functions for
  memory, relationships, milestones, held thoughts, desires, consent flags,
  decay, focus state, and a reflection audit log.
- **identity.py** — builds the real system prompt every message, weaving
  in self-state (voice, moral notes, dispositions, founding story, held
  contradictions) so the rules apply *through* its actual accumulated
  identity, not generically.
- **reflection.py** — the nightly job. Asks Claude to genuinely reflect on
  recent interactions and self-state, not just summarize, and persists
  whatever real shifts emerge (voice drift, new contradictions, resolved
  insights, milestones). Logs everything to `reflection_log` so you have a
  human-readable audit trail of how it's changed over time.
- **bot.py** — message handling, single-focus attention (generates a real
  holding reply instead of responding fully if it's not currently focused
  on that channel), and wires it all together.

## What's NOT built yet (needs your decisions / more code)

- **Kill switch.** Intentionally not included here. Per the spec, it must
  live in a fully separate process with zero shared credentials/access --
  do not bolt it onto this codebase. Build it as its own small service on
  separate infrastructure that can hard-kill the bot process from outside.
- **Embeddings for semantic memory recall.** The `embedding` column exists
  in `memories` but nothing populates or queries it yet -- right now recall
  is just "most recent N messages." Add an embedding call (OpenAI, Voyage,
  or similar) when storing memories, and a pgvector similarity query when
  building context, to get real semantic search instead of just recency.
- **Moderation tool execution.** The bot can reason about moderation
  decisions via the prompt, but no actual Discord mod actions (kick, ban,
  timeout) are wired up yet -- needs discord.py permission calls added
  once you've decided which actions it's allowed to take independently.
- **Founding conversation special-casing.** Right now the system prompt
  tells the model it has no name yet, which should naturally produce the
  right kind of conversation -- but there's no explicit logic forcing it
  to *only* happen in that very first exchange. Test this carefully.
- **Real value for MEMORY_DECAY_DAYS.** Set in `.env`, currently just a
  default of 14 -- tune this once you're actually testing.

## Suggested next step

Run this privately, just you, for at least a few days so the reflection
loop has cycled a handful of times before anyone else talks to it -- per
the "childhood" section of the spec, you want to actually watch its voice
and judgment develop somewhere low-stakes first.
