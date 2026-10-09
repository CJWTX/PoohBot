"""
One-off backfill: fills the message counts behind /leaderboard and
/channelstats from the last 7 days of a server's history, in poohbot.py's
SQLite database (poohbot.db).

Usage:
    venv/bin/python backfill_messages.py [--guild ID] [--db poohbot.db]

Safe to run while the bot is up, and safe to re-run. It talks to Discord's
HTTP API only (no gateway connection) and reads history up to the moment the
bot last started counting live, then swaps in what it read for everything
counted before that moment. Messages after it are left to the bot's live
counting, so nothing is counted twice and one run right after a restart
covers everything. Counts are written in one go at the end, so stopping it
early (Ctrl+C) or an error leaves the database untouched.
"""

import argparse
import asyncio
import os
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone

import discord

# Keep these in sync with poohbot.py: LEADERBOARD_DAYS, message_hour(),
# stats_channel_id(), mark_live_counting_started(), and init_db()'s
# "channel_message_counts" table.
LEADERBOARD_DAYS = 7
CREATE_MESSAGE_COUNTS_TABLE = """
CREATE TABLE IF NOT EXISTS channel_message_counts (
    guild_id INTEGER,
    channel_id INTEGER,
    user_id INTEGER,
    hour TEXT,
    count INTEGER NOT NULL DEFAULT 0,
    pre_live INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, channel_id, user_id, hour)
)
"""


def message_hour(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H")


def stats_channel_id(channel) -> int:
    """The channel a message counts toward: a thread's parent, otherwise itself."""
    return channel.parent_id if isinstance(channel, discord.Thread) else channel.id


def get_setting(conn, key: str):
    row = conn.execute("SELECT value FROM bot_settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def load_token() -> str:
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if token:
        return token
    with open("token.env", encoding="utf-8") as f:
        for line in f:
            key, _, value = line.strip().partition("=")
            if key == "DISCORD_BOT_TOKEN":
                return value.strip().strip('"').strip("'")
    raise SystemExit("DISCORD_BOT_TOKEN isn't set and isn't in token.env.")


async def message_channels(guild: discord.Guild):
    """Every channel and thread whose history can hold messages: text, voice
    and stage chats, active threads, and archived threads (public and private)."""
    channels = await guild.fetch_channels()
    for channel in channels:
        if isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel)):
            yield channel
    for thread in await guild.active_threads():
        yield thread
    for channel in channels:
        if isinstance(channel, discord.TextChannel):
            sources = [channel.archived_threads(limit=None), channel.archived_threads(limit=None, private=True)]
        elif isinstance(channel, discord.ForumChannel):
            sources = [channel.archived_threads(limit=None)]
        else:
            continue
        for source in sources:
            try:
                async for thread in source:
                    yield thread
            except (discord.Forbidden, discord.HTTPException):
                pass  # e.g. private archived threads need Manage Threads


async def backfill(client: discord.Client, args, conn) -> None:
    if args.guild:
        guild = await client.fetch_guild(args.guild)
    else:
        guilds = [g async for g in client.fetch_guilds(limit=None)]
        if len(guilds) != 1:
            names = ", ".join(f"{g.name} ({g.id})" for g in guilds)
            raise SystemExit(f"The bot is in {len(guilds)} servers; pick one with --guild. Servers: {names}")
        guild = await client.fetch_guild(guilds[0].id)

    # Everything before the bot started counting live comes from history;
    # everything after stays with the live count.
    live_since = get_setting(conn, "message_counts_live_since")
    if live_since is None:
        raise SystemExit("The bot hasn't started counting yet. Restart it with the latest poohbot.py first.")
    cutoff = datetime.fromisoformat(live_since)
    after = (datetime.now(timezone.utc) - timedelta(days=LEADERBOARD_DAYS)).replace(minute=0, second=0, microsecond=0)
    print(f"Backfilling {guild.name}: {after:%Y-%m-%d %H:%M} UTC to {cutoff:%Y-%m-%d %H:%M:%S} UTC, when the bot started counting.")

    counts = Counter()
    seen_ids = set()
    scanned = channels_done = 0
    skipped = []
    async for channel in message_channels(guild):
        if channel.id in seen_ids:
            continue
        seen_ids.add(channel.id)
        try:
            async for message in channel.history(limit=None, before=cutoff, after=after):
                scanned += 1
                if not message.author.bot:  # same rule as the live count
                    counts[(stats_channel_id(channel), message.author.id, message_hour(message.created_at))] += 1
        except discord.Forbidden:
            skipped.append(channel.name)
            continue
        channels_done += 1
        print(f"  #{channel.name} done ({scanned:,} messages scanned so far)")

    cutoff_hour = message_hour(cutoff)
    with conn:  # one transaction: all of it lands, or none of it
        if get_setting(conn, "message_counts_live_since") != live_since:
            raise SystemExit("The bot restarted while this was running. Nothing was written; run it again.")
        # Whole hours before the cutoff are replaced outright.
        conn.execute(
            "DELETE FROM channel_message_counts WHERE guild_id = ? AND hour < ?", (guild.id, cutoff_hour)
        )
        conn.executemany(
            "INSERT INTO channel_message_counts (guild_id, channel_id, user_id, hour, count) VALUES (?, ?, ?, ?, ?)",
            [
                (guild.id, channel_id, user_id, hour, n)
                for (channel_id, user_id, hour), n in counts.items()
                if hour < cutoff_hour
            ],
        )
        # The cutoff's own hour also has live counts from after it: swap out
        # only its before-the-cutoff part (pre_live) for what history says.
        conn.execute(
            "UPDATE channel_message_counts SET count = count - pre_live, pre_live = 0 WHERE guild_id = ? AND hour = ?",
            (guild.id, cutoff_hour),
        )
        conn.executemany(
            "INSERT INTO channel_message_counts (guild_id, channel_id, user_id, hour, count, pre_live) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(guild_id, channel_id, user_id, hour) "
            "DO UPDATE SET count = count + excluded.count, pre_live = excluded.pre_live",
            [
                (guild.id, channel_id, user_id, hour, n, n)
                for (channel_id, user_id, hour), n in counts.items()
                if hour == cutoff_hour
            ],
        )
        conn.execute(
            "DELETE FROM channel_message_counts WHERE guild_id = ? AND hour = ? AND count <= 0",
            (guild.id, cutoff_hour),
        )

    members = len({user_id for _, user_id, _ in counts})
    print(
        f"Done. Scanned {scanned:,} messages in {channels_done} channel(s)/thread(s) and counted "
        f"{sum(counts.values()):,} message(s) from {members} member(s)."
    )
    if skipped:
        print(f"Skipped {len(skipped)} the bot can't read: {', '.join('#' + name for name in skipped)}")


async def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--guild", type=int, help="Server ID (only needed if the bot is in more than one)")
    parser.add_argument("--db", default="poohbot.db", help="Path to poohbot.db (default: poohbot.db)")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db, timeout=30)  # the live bot writes to the same file
    conn.execute(CREATE_MESSAGE_COUNTS_TABLE)
    client = discord.Client(intents=discord.Intents.none())
    try:
        await client.login(load_token())
        await backfill(client, args, conn)
    finally:
        await client.close()
        conn.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped. Nothing was written to the database.")
