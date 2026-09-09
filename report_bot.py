"""
Discord Report Bot (full-featured)
-----------------------------------
Core flow: right-click a message -> Apps -> "Report Message" (or use
/report for reports with no specific message) -> mods get a case in the
report channel with action buttons.

FEATURES
  - Report via right-click context menu OR /report (with optional @user)
  - Case numbers, stored in SQLite (reportbot.db, created automatically)
  - Duplicate detection: re-reporting the same message bumps a counter
    instead of spamming a new case
  - Auto-thread per case for mod discussion
  - Rate limiting (default: 3 reports / 5 min per user per server)
  - Blocks: reporting bots, reporting yourself, accounts younger than
    MIN_ACCOUNT_AGE_DAYS
  - DM confirmation to the reporter with their case number
  - Mod action buttons on every report: Resolve, Dismiss, Escalate,
    Delete Message, Timeout, Warn
  - /reports @user - moderation history (times reported + warnings)
  - Quote board: react 💬 to any message to save it as a numbered quote,
    then recall it with ".q 12" (or /quote), browse with /quotes,
    search text with ".q s <keyword>"
  - /setreportchannel, /setoncallrole, /setnoquoterole - per-server config
  - /capybara - posts a random capybara gif (needs KLIPY_API_KEY)

SETUP
1. pip install -U discord.py     (sqlite3 is in the Python standard library)
2. Create a bot at https://discord.com/developers/applications
   - Invite with scopes: bot, applications.commands
   - Permissions needed: View Channels, Send Messages, Embed Links,
     Manage Messages (for the Delete button), Moderate Members (for
     the Timeout button), Create Public Threads
   - IMPORTANT: on the Bot page, turn ON "Message Content Intent".
     It's required for the ".q" text command to be visible to the bot.
     (Everything else works without it; slash commands are unaffected.)
3. export DISCORD_BOT_TOKEN="your-token-here"
   (optional) export TEST_GUILD_ID="your-server-id"   for instant command sync while testing
   (optional) export KLIPY_API_KEY="your-klipy-key"   required for /capybara
4. Run: python report_bot.py
5. In Discord: /setreportchannel #mod-reports and /setoncallrole @Mods
   (both require Manage Server permission), then try Report Message.

All mod-only buttons/commands require the "Manage Messages" (or,
for server config, "Manage Server") permission on the person clicking.
"""

import os
import random
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

# ---------- tunables ----------

DB_FILE = "reportbot.db"
MAX_REPORTS_PER_WINDOW = 3
RATE_LIMIT_WINDOW_SECONDS = 300  # 5 minutes
MIN_ACCOUNT_AGE_DAYS = 1
KLIPY_API_KEY = os.environ.get("KLIPY_API_KEY")

# ---------- database ----------

def db_connect():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db_connect()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS guild_config (
            guild_id INTEGER PRIMARY KEY,
            report_channel_id INTEGER,
            oncall_role_id INTEGER
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS reports (
            case_id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER,
            message_id INTEGER,
            channel_id INTEGER,
            message_author_id INTEGER,
            reporter_id INTEGER,
            reason TEXT,
            status TEXT DEFAULT 'open',
            jump_url TEXT,
            report_msg_id INTEGER,
            report_channel_id INTEGER,
            thread_id INTEGER,
            duplicate_count INTEGER DEFAULT 1,
            created_at TEXT,
            resolved_by INTEGER,
            resolved_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS warnings (
            warning_id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER,
            user_id INTEGER,
            moderator_id INTEGER,
            reason TEXT,
            created_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS pin_requests (
            request_id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER,
            message_id INTEGER,
            channel_id INTEGER,
            requestor_id INTEGER,
            request_msg_id INTEGER,
            status TEXT DEFAULT 'open',
            created_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS bot_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS dm_threads (
            thread_id INTEGER PRIMARY KEY,
            guild_id INTEGER,
            user_id INTEGER,
            created_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS quotes (
            quote_id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER,
            quote_number INTEGER,
            message_id INTEGER,
            channel_id INTEGER,
            author_id INTEGER,
            author_name TEXT,
            content TEXT,
            image_url TEXT,
            jump_url TEXT,
            saved_by_id INTEGER,
            message_created_at TEXT,
            created_at TEXT
        )"""
    )
    # quote_number is per-server (each guild counts from #1), and a given
    # message can only ever be quoted once.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_quotes_guild_number ON quotes (guild_id, quote_number)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_quotes_guild_message ON quotes (guild_id, message_id)"
    )
    conn.commit()
    conn.close()
    _migrate_add_column("guild_config", "pin_request_channel_id", "INTEGER")
    _migrate_add_column("guild_config", "dm_channel_id", "INTEGER")
    _migrate_add_column("guild_config", "simonsays_role_id", "INTEGER")
    _migrate_add_column("guild_config", "simonsays_log_channel_id", "INTEGER")
    _migrate_add_column("guild_config", "no_quote_role_id", "INTEGER")


def _migrate_add_column(table: str, column: str, col_type: str):
    """Adds a column to an existing table if it's missing (for upgrades)."""
    conn = db_connect()
    existing = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        conn.commit()
    conn.close()


def get_config(guild_id: int):
    conn = db_connect()
    row = conn.execute(
        "SELECT * FROM guild_config WHERE guild_id = ?", (guild_id,)
    ).fetchone()
    conn.close()
    return row


def get_setting(key: str, default=None):
    conn = db_connect()
    row = conn.execute("SELECT value FROM bot_settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row is not None else default


def set_setting(key: str, value: str):
    conn = db_connect()
    conn.execute(
        "INSERT INTO bot_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def get_dm_thread_for_user(guild_id: int, user_id: int):
    conn = db_connect()
    row = conn.execute(
        "SELECT * FROM dm_threads WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
    ).fetchone()
    conn.close()
    return row


def get_dm_thread_by_thread(thread_id: int):
    conn = db_connect()
    row = conn.execute("SELECT * FROM dm_threads WHERE thread_id = ?", (thread_id,)).fetchone()
    conn.close()
    return row


def get_dm_threads_for_user(user_id: int):
    conn = db_connect()
    rows = conn.execute("SELECT * FROM dm_threads WHERE user_id = ?", (user_id,)).fetchall()
    conn.close()
    return rows


def create_dm_thread(guild_id: int, user_id: int, thread_id: int):
    conn = db_connect()
    conn.execute(
        "INSERT INTO dm_threads (thread_id, guild_id, user_id, created_at) VALUES (?, ?, ?, ?)",
        (thread_id, guild_id, user_id, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def set_report_channel(guild_id: int, channel_id: int):
    conn = db_connect()
    conn.execute(
        "INSERT INTO guild_config (guild_id, report_channel_id) VALUES (?, ?) "
        "ON CONFLICT(guild_id) DO UPDATE SET report_channel_id = excluded.report_channel_id",
        (guild_id, channel_id),
    )
    conn.commit()
    conn.close()


def set_oncall_role(guild_id: int, role_id: int):
    conn = db_connect()
    conn.execute(
        "INSERT INTO guild_config (guild_id, oncall_role_id) VALUES (?, ?) "
        "ON CONFLICT(guild_id) DO UPDATE SET oncall_role_id = excluded.oncall_role_id",
        (guild_id, role_id),
    )
    conn.commit()
    conn.close()


def set_pin_request_channel(guild_id: int, channel_id: int):
    conn = db_connect()
    conn.execute(
        "INSERT INTO guild_config (guild_id, pin_request_channel_id) VALUES (?, ?) "
        "ON CONFLICT(guild_id) DO UPDATE SET pin_request_channel_id = excluded.pin_request_channel_id",
        (guild_id, channel_id),
    )
    conn.commit()
    conn.close()


def set_dm_channel(guild_id: int, channel_id: int):
    conn = db_connect()
    conn.execute(
        "INSERT INTO guild_config (guild_id, dm_channel_id) VALUES (?, ?) "
        "ON CONFLICT(guild_id) DO UPDATE SET dm_channel_id = excluded.dm_channel_id",
        (guild_id, channel_id),
    )
    conn.commit()
    conn.close()


def set_simonsays_role(guild_id: int, role_id: int):
    conn = db_connect()
    conn.execute(
        "INSERT INTO guild_config (guild_id, simonsays_role_id) VALUES (?, ?) "
        "ON CONFLICT(guild_id) DO UPDATE SET simonsays_role_id = excluded.simonsays_role_id",
        (guild_id, role_id),
    )
    conn.commit()
    conn.close()


def set_no_quote_role(guild_id: int, role_id: int):
    conn = db_connect()
    conn.execute(
        "INSERT INTO guild_config (guild_id, no_quote_role_id) VALUES (?, ?) "
        "ON CONFLICT(guild_id) DO UPDATE SET no_quote_role_id = excluded.no_quote_role_id",
        (guild_id, role_id),
    )
    conn.commit()
    conn.close()


def set_simonsays_log_channel(guild_id: int, channel_id: int):
    conn = db_connect()
    conn.execute(
        "INSERT INTO guild_config (guild_id, simonsays_log_channel_id) VALUES (?, ?) "
        "ON CONFLICT(guild_id) DO UPDATE SET simonsays_log_channel_id = excluded.simonsays_log_channel_id",
        (guild_id, channel_id),
    )
    conn.commit()
    conn.close()


def find_open_report(guild_id: int, message_id: int):
    if not message_id:
        return None
    conn = db_connect()
    row = conn.execute(
        "SELECT * FROM reports WHERE guild_id = ? AND message_id = ? AND status = 'open'",
        (guild_id, message_id),
    ).fetchone()
    conn.close()
    return row


def create_report(**fields) -> int:
    conn = db_connect()
    cur = conn.execute(
        """INSERT INTO reports
           (guild_id, message_id, channel_id, message_author_id, reporter_id,
            reason, jump_url, report_channel_id, created_at)
           VALUES (:guild_id, :message_id, :channel_id, :message_author_id,
                   :reporter_id, :reason, :jump_url, :report_channel_id, :created_at)""",
        fields,
    )
    conn.commit()
    case_id = cur.lastrowid
    conn.close()
    return case_id


def update_report(case_id: int, **fields):
    if not fields:
        return
    conn = db_connect()
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    fields["case_id"] = case_id
    conn.execute(f"UPDATE reports SET {set_clause} WHERE case_id = :case_id", fields)
    conn.commit()
    conn.close()


def get_report_by_case(case_id: int):
    conn = db_connect()
    row = conn.execute("SELECT * FROM reports WHERE case_id = ?", (case_id,)).fetchone()
    conn.close()
    return row


def get_report_by_report_msg(report_msg_id: int):
    conn = db_connect()
    row = conn.execute(
        "SELECT * FROM reports WHERE report_msg_id = ?", (report_msg_id,)
    ).fetchone()
    conn.close()
    return row


def add_warning(guild_id: int, user_id: int, moderator_id: int, reason: str):
    conn = db_connect()
    conn.execute(
        "INSERT INTO warnings (guild_id, user_id, moderator_id, reason, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (guild_id, user_id, moderator_id, reason, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def find_pin_request_for_message(guild_id: int, message_id: int):
    """Returns any existing pin request for this message, regardless of status —
    used to prevent re-requesting a message that's already been approved/denied."""
    conn = db_connect()
    row = conn.execute(
        "SELECT * FROM pin_requests WHERE guild_id = ? AND message_id = ?",
        (guild_id, message_id),
    ).fetchone()
    conn.close()
    return row


def create_pin_request(**fields) -> int:
    conn = db_connect()
    cur = conn.execute(
        """INSERT INTO pin_requests (guild_id, message_id, channel_id, requestor_id, created_at)
           VALUES (:guild_id, :message_id, :channel_id, :requestor_id, :created_at)""",
        fields,
    )
    conn.commit()
    request_id = cur.lastrowid
    conn.close()
    return request_id


def update_pin_request(request_id: int, **fields):
    if not fields:
        return
    conn = db_connect()
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    fields["request_id"] = request_id
    conn.execute(f"UPDATE pin_requests SET {set_clause} WHERE request_id = :request_id", fields)
    conn.commit()
    conn.close()


def get_pin_request_by_request_msg(request_msg_id: int):
    conn = db_connect()
    row = conn.execute(
        "SELECT * FROM pin_requests WHERE request_msg_id = ?", (request_msg_id,)
    ).fetchone()
    conn.close()
    return row


def get_all_reports(guild_id: int):
    conn = db_connect()
    rows = conn.execute(
        "SELECT case_id, report_msg_id, report_channel_id, thread_id FROM reports WHERE guild_id = ?",
        (guild_id,),
    ).fetchall()
    conn.close()
    return rows


def clear_all_reports(guild_id: int):
    conn = db_connect()
    conn.execute("DELETE FROM reports WHERE guild_id = ?", (guild_id,))
    conn.commit()
    conn.close()


def clear_all_warnings(guild_id: int):
    conn = db_connect()
    conn.execute("DELETE FROM warnings WHERE guild_id = ?", (guild_id,))
    conn.commit()
    conn.close()


def get_old_closed_reports(guild_id: int, cutoff_iso: str):
    conn = db_connect()
    rows = conn.execute(
        "SELECT case_id, report_msg_id, report_channel_id FROM reports "
        "WHERE guild_id = ? AND status IN ('resolved', 'dismissed') AND created_at < ?",
        (guild_id, cutoff_iso),
    ).fetchall()
    conn.close()
    return rows


def delete_reports(case_ids: list[int]):
    if not case_ids:
        return
    conn = db_connect()
    placeholders = ",".join("?" * len(case_ids))
    conn.execute(f"DELETE FROM reports WHERE case_id IN ({placeholders})", case_ids)
    conn.commit()
    conn.close()


def get_history(guild_id: int, user_id: int) -> dict:
    conn = db_connect()
    reported_count = conn.execute(
        "SELECT COUNT(*) FROM reports WHERE guild_id = ? AND message_author_id = ?",
        (guild_id, user_id),
    ).fetchone()[0]
    recent_reports = conn.execute(
        "SELECT case_id, reason, status, created_at FROM reports "
        "WHERE guild_id = ? AND message_author_id = ? ORDER BY case_id DESC LIMIT 5",
        (guild_id, user_id),
    ).fetchall()
    warnings = conn.execute(
        "SELECT warning_id, reason, created_at FROM warnings "
        "WHERE guild_id = ? AND user_id = ? ORDER BY warning_id DESC",
        (guild_id, user_id),
    ).fetchall()
    conn.close()
    return {"reported_count": reported_count, "recent_reports": recent_reports, "warnings": warnings}


# ---------- quotes ----------

QUOTE_INSERT_SQL = """INSERT INTO quotes
    (guild_id, quote_number, message_id, channel_id, author_id, author_name,
     content, image_url, jump_url, saved_by_id, message_created_at, created_at)
    VALUES (:guild_id, :quote_number, :message_id, :channel_id, :author_id,
            :author_name, :content, :image_url, :jump_url, :saved_by_id,
            :message_created_at, :created_at)"""


def create_quote(**fields):
    """Saves a message as a quote. Returns (quote_number, created); created is
    False when the message was already quoted, in which case the existing
    number comes back instead."""
    conn = db_connect()
    try:
        def already_quoted():
            return conn.execute(
                "SELECT quote_number FROM quotes WHERE guild_id = ? AND message_id = ?",
                (fields["guild_id"], fields["message_id"]),
            ).fetchone()

        existing = already_quoted()
        if existing is not None:
            return existing["quote_number"], False

        # Retry loop: two people can react at the same instant and race for the
        # same quote number — the unique index rejects the loser, who retries.
        for _ in range(5):
            next_number = conn.execute(
                "SELECT COALESCE(MAX(quote_number), 0) + 1 FROM quotes WHERE guild_id = ?",
                (fields["guild_id"],),
            ).fetchone()[0]
            try:
                conn.execute(QUOTE_INSERT_SQL, {**fields, "quote_number": next_number})
                conn.commit()
                return next_number, True
            except sqlite3.IntegrityError:
                conn.rollback()
                existing = already_quoted()
                if existing is not None:
                    return existing["quote_number"], False
        return None, False
    finally:
        conn.close()


def get_quote(guild_id: int, quote_number: int):
    conn = db_connect()
    row = conn.execute(
        "SELECT * FROM quotes WHERE guild_id = ? AND quote_number = ?", (guild_id, quote_number)
    ).fetchone()
    conn.close()
    return row


def get_quotes(guild_id: int, author_id: int = None):
    conn = db_connect()
    if author_id is None:
        rows = conn.execute(
            "SELECT * FROM quotes WHERE guild_id = ? ORDER BY quote_number", (guild_id,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM quotes WHERE guild_id = ? AND author_id = ? ORDER BY quote_number",
            (guild_id, author_id),
        ).fetchall()
    conn.close()
    return rows


def search_quotes(guild_id: int, keyword: str):
    conn = db_connect()
    # Escape LIKE wildcards in the keyword so e.g. searching for "50%" doesn't
    # match everything.
    escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = conn.execute(
        "SELECT * FROM quotes WHERE guild_id = ? AND content LIKE ? ESCAPE '\\' ORDER BY quote_number",
        (guild_id, f"%{escaped}%"),
    ).fetchall()
    conn.close()
    return rows


def get_random_quote(guild_id: int, author_id: int = None):
    rows = get_quotes(guild_id, author_id)
    return random.choice(rows) if rows else None


def delete_quote(guild_id: int, quote_number: int) -> bool:
    conn = db_connect()
    cur = conn.execute(
        "DELETE FROM quotes WHERE guild_id = ? AND quote_number = ?", (guild_id, quote_number)
    )
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def renumber_quotes(guild_id: int) -> int:
    """Closes gaps in quote numbering (e.g. after deletes) so numbers run
    1..N in their existing order. Returns how many quotes actually moved."""
    conn = db_connect()
    rows = conn.execute(
        "SELECT quote_id, quote_number FROM quotes WHERE guild_id = ? ORDER BY quote_number",
        (guild_id,),
    ).fetchall()

    # Two-phase renumber: first shove everything onto negative, per-row-unique
    # numbers so the (guild_id, quote_number) unique index never collides
    # while numbers are shifting, then assign the final sequential values.
    for row in rows:
        conn.execute(
            "UPDATE quotes SET quote_number = ? WHERE quote_id = ?", (-row["quote_id"], row["quote_id"])
        )

    changed = 0
    for i, row in enumerate(rows, start=1):
        if row["quote_number"] != i:
            changed += 1
        conn.execute("UPDATE quotes SET quote_number = ? WHERE quote_id = ?", (i, row["quote_id"]))

    conn.commit()
    conn.close()
    return changed


# ---------- rate limiting (in-memory) ----------

_report_timestamps: dict = {}


def is_rate_limited(guild_id: int, user_id: int) -> bool:
    key = (guild_id, user_id)
    now = time.time()
    timestamps = [t for t in _report_timestamps.get(key, []) if now - t < RATE_LIMIT_WINDOW_SECONDS]
    _report_timestamps[key] = timestamps
    return len(timestamps) >= MAX_REPORTS_PER_WINDOW


def record_report_attempt(guild_id: int, user_id: int):
    key = (guild_id, user_id)
    _report_timestamps.setdefault(key, []).append(time.time())


# ---------- "good bot" easter egg ----------

GOOD_BOT_COOLDOWN_SECONDS = 300  # 5 minutes
_good_bot_last_reply: dict = {}  # channel_id -> last reply timestamp


def _is_good_bot(content: str) -> bool:
    return re.fullmatch(r"good bot[!.?]*", content.strip(), re.IGNORECASE) is not None


async def maybe_reply_good_bot(message: discord.Message):
    if not _is_good_bot(message.content):
        return
    now = time.time()
    last = _good_bot_last_reply.get(message.channel.id, 0)
    if now - last < GOOD_BOT_COOLDOWN_SECONDS:
        return
    _good_bot_last_reply[message.channel.id] = now
    try:
        await message.channel.send("no u")
    except (discord.Forbidden, discord.HTTPException):
        pass


# ---------- bot setup ----------

intents = discord.Intents.default()
# Required for text commands like ".q 12" — the bot can't see message text
# without it. Enable "Message Content Intent" on the bot's page in the
# Discord developer portal or login will fail with PrivilegedIntentsRequired.
intents.message_content = True
bot = commands.Bot(command_prefix=(".", "!"), intents=intents, case_insensitive=True)


def is_mod(interaction: discord.Interaction) -> bool:
    perms = interaction.user.guild_permissions
    return perms.manage_messages or perms.manage_guild


def report_channel_for(guild_id: int):
    row = get_config(guild_id)
    if row is None or row["report_channel_id"] is None:
        return None
    return bot.get_channel(row["report_channel_id"])


def pin_request_channel_for(guild_id: int):
    """Returns the dedicated pin-request channel if one's set, otherwise
    falls back to the regular report channel."""
    row = get_config(guild_id)
    if row is None:
        return None
    if row["pin_request_channel_id"] is not None:
        channel = bot.get_channel(row["pin_request_channel_id"])
        if channel is not None:
            return channel
    if row["report_channel_id"] is not None:
        return bot.get_channel(row["report_channel_id"])
    return None


def dm_channel_for(guild_id: int):
    """Returns the dedicated DM-conversation channel if one's set, otherwise
    falls back to the regular report channel."""
    row = get_config(guild_id)
    if row is None:
        return None
    if row["dm_channel_id"] is not None:
        channel = bot.get_channel(row["dm_channel_id"])
        if channel is not None:
            return channel
    if row["report_channel_id"] is not None:
        return bot.get_channel(row["report_channel_id"])
    return None


def no_quote_role_id_for(guild_id: int):
    """Returns the role id whose members' messages are exempt from 💬
    quote-saving, or None if no such role is configured."""
    row = get_config(guild_id)
    if row is None:
        return None
    return row["no_quote_role_id"]


def simonsays_log_channel_for(guild_id: int):
    """Returns the configured /simonsays log channel, or None if it hasn't
    been set — logging is opt-in, so unlike other channels there's no
    fallback to the report channel."""
    row = get_config(guild_id)
    if row is None or row["simonsays_log_channel_id"] is None:
        return None
    return bot.get_channel(row["simonsays_log_channel_id"])


# ---------- embed building ----------

def build_report_embed(case_id: int, row_data: dict, duplicate_count: int = 1) -> discord.Embed:
    embed = discord.Embed(
        title=f"🚩 Report #{case_id}",
        color=discord.Color.red(),
        timestamp=datetime.now(timezone.utc),
    )
    if row_data.get("content") is not None:
        embed.add_field(
            name="Reported message",
            value=row_data["content"][:1024] if row_data["content"] else "*[no text content]*",
            inline=False,
        )
    if row_data.get("author_mention"):
        embed.add_field(name="Author", value=row_data["author_mention"], inline=True)
    if row_data.get("channel_mention"):
        embed.add_field(name="Channel", value=row_data["channel_mention"], inline=True)
    embed.add_field(name="Reported by", value=row_data["reporter_mention"], inline=True)
    if row_data.get("reason"):
        embed.add_field(name="Reason", value=row_data["reason"], inline=False)
    if row_data.get("jump_url"):
        embed.add_field(name="Jump to message", value=f"[Click here]({row_data['jump_url']})", inline=False)
    if duplicate_count > 1:
        embed.add_field(name="Reported by", value=f"{duplicate_count} users total", inline=False)
    embed.add_field(name="Status", value="🟡 Open", inline=False)
    embed.set_footer(text=f"Case #{case_id}")
    return embed


def _format_short_datetime(dt: datetime) -> str:
    """e.g. 5/8/26, 6:58 PM — avoids strftime's non-portable no-pad flags (%-d etc)."""
    hour = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{dt.month}/{dt.day}/{dt.year % 100}, {hour}:{dt.minute:02d} {ampm}"


def build_quote_embed(row) -> discord.Embed:
    content = row["content"] or "*[no text content]*"

    mention = f"<@{row['author_id']}>" if row["author_id"] else (row["author_name"] or "Unknown user")
    line = f"• {mention}"
    if row["jump_url"]:
        line += f" ([Jump]({row['jump_url']}))"

    embed = discord.Embed(
        title=f"#{row['quote_number']}",
        description=f"{content[:3900]}\n{line}",
    )
    if row["image_url"]:
        embed.set_image(url=row["image_url"])

    if row["message_created_at"]:
        try:
            timestamp = datetime.fromisoformat(row["message_created_at"])
            embed.set_footer(text=_format_short_datetime(timestamp))
        except ValueError:
            pass
    return embed


# ---------- persistent mod-action view ----------

class ReportActionView(discord.ui.View):
    """Attached to every report embed. Fixed custom_ids so it survives restarts
    via bot.add_view(); the case is looked up from the message the buttons live on."""

    def __init__(self):
        super().__init__(timeout=None)

    async def _get_case(self, interaction: discord.Interaction):
        row = get_report_by_report_msg(interaction.message.id)
        if row is None:
            await interaction.response.send_message(
                "Couldn't find this report in the database.", ephemeral=True
            )
        return row

    async def _require_mod(self, interaction: discord.Interaction) -> bool:
        if not is_mod(interaction):
            await interaction.response.send_message(
                "You need the 'Manage Messages' permission to do that.", ephemeral=True
            )
            return False
        return True

    async def _mark_status(self, interaction: discord.Interaction, case, status: str, label: str, color: discord.Color):
        update_report(case["case_id"], status=status, resolved_by=interaction.user.id, resolved_at=datetime.now(timezone.utc).isoformat())
        embed = interaction.message.embeds[0]
        embed.color = color
        for i, field in enumerate(embed.fields):
            if field.name == "Status":
                embed.set_field_at(i, name="Status", value=f"{label} by {interaction.user.mention}", inline=False)
                break
        await interaction.response.edit_message(embed=embed, view=None)
        await self._close_thread(interaction, case)

    async def _close_thread(self, interaction: discord.Interaction, case):
        if not case["thread_id"]:
            return
        try:
            thread = interaction.guild.get_thread(case["thread_id"]) or await interaction.guild.fetch_channel(case["thread_id"])
            await thread.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, AttributeError):
            pass  # thread may already be gone or deletion failed; not critical

    @discord.ui.button(label="Resolve", style=discord.ButtonStyle.success, custom_id="report_resolve")
    async def resolve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._require_mod(interaction):
            return
        case = await self._get_case(interaction)
        if case is None:
            return
        await self._mark_status(interaction, case, "resolved", "✅ Resolved", discord.Color.green())

    @discord.ui.button(label="Dismiss", style=discord.ButtonStyle.secondary, custom_id="report_dismiss")
    async def dismiss(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._require_mod(interaction):
            return
        case = await self._get_case(interaction)
        if case is None:
            return
        await self._mark_status(interaction, case, "dismissed", "⚪ Dismissed", discord.Color.light_grey())

    @discord.ui.button(label="Escalate", style=discord.ButtonStyle.danger, custom_id="report_escalate")
    async def escalate(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._require_mod(interaction):
            return
        case = await self._get_case(interaction)
        if case is None:
            return
        config = get_config(interaction.guild_id)
        if config is None or config["oncall_role_id"] is None:
            await interaction.response.send_message(
                "No on-call role set. A mod can set one with /setoncallrole.", ephemeral=True
            )
            return
        role = interaction.guild.get_role(config["oncall_role_id"])
        if role is None:
            await interaction.response.send_message("The configured on-call role no longer exists.", ephemeral=True)
            return
        await interaction.channel.send(f"{role.mention} case #{case['case_id']} needs attention.")
        await interaction.response.send_message("Escalated.", ephemeral=True)

    @discord.ui.button(label="Delete Message", style=discord.ButtonStyle.danger, custom_id="report_delete_msg")
    async def delete_message(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._require_mod(interaction):
            return
        case = await self._get_case(interaction)
        if case is None:
            return
        if not case["message_id"]:
            await interaction.response.send_message("This report has no linked message to delete.", ephemeral=True)
            return
        try:
            channel = interaction.guild.get_channel(case["channel_id"]) or await interaction.guild.fetch_channel(case["channel_id"])
            msg = await channel.fetch_message(case["message_id"])
            await msg.delete()
            update_report(case["case_id"], status="resolved", resolved_by=interaction.user.id, resolved_at=datetime.now(timezone.utc).isoformat())
            await self._close_thread(interaction, case)
            await interaction.response.send_message(f"Deleted the reported message for case #{case['case_id']} and removed the thread.", ephemeral=True)
        except discord.NotFound:
            await interaction.response.send_message("That message was already deleted or is no longer accessible.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("I don't have permission to delete that message.", ephemeral=True)

    @discord.ui.button(label="Timeout", style=discord.ButtonStyle.danger, custom_id="report_timeout")
    async def timeout_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._require_mod(interaction):
            return
        case = await self._get_case(interaction)
        if case is None:
            return
        if not case["message_author_id"]:
            await interaction.response.send_message("This report has no linked user to time out.", ephemeral=True)
            return
        await interaction.response.send_modal(TimeoutModal(case["case_id"], case["message_author_id"]))

    @discord.ui.button(label="Warn", style=discord.ButtonStyle.primary, custom_id="report_warn")
    async def warn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._require_mod(interaction):
            return
        case = await self._get_case(interaction)
        if case is None:
            return
        if not case["message_author_id"]:
            await interaction.response.send_message("This report has no linked user to warn.", ephemeral=True)
            return
        await interaction.response.send_modal(WarnModal(case["case_id"], case["message_author_id"]))


class PinRequestView(discord.ui.View):
    """Sent to the report channel when someone reacts with 📌. Fixed custom_ids
    so it survives restarts; the request is looked up from the message the
    buttons live on, same pattern as ReportActionView."""

    def __init__(self):
        super().__init__(timeout=None)

    async def _get_request(self, interaction: discord.Interaction):
        row = get_pin_request_by_request_msg(interaction.message.id)
        if row is None:
            await interaction.response.send_message("Couldn't find this pin request in the database.", ephemeral=True)
        return row

    # No permission gate here on purpose: these buttons only ever appear on a
    # message posted in the pin-request channel, so being able to click them
    # already means the user can see that channel. Actual pinning still
    # requires the bot itself to have Manage Messages in the target channel.

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="pin_approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        req = await self._get_request(interaction)
        if req is None:
            return

        embed = interaction.message.embeds[0]
        try:
            channel = interaction.guild.get_channel(req["channel_id"]) or await interaction.guild.fetch_channel(req["channel_id"])
            msg = await channel.fetch_message(req["message_id"])
            await msg.pin(reason=f"Pin request approved by {interaction.user}")
            update_pin_request(req["request_id"], status="approved")
            embed.color = discord.Color.green()
            self._set_status_field(embed, f"✅ Approved by {interaction.user.mention}")
            await interaction.response.edit_message(embed=embed, view=None)
        except discord.NotFound:
            update_pin_request(req["request_id"], status="approved")
            await interaction.response.send_message("That message no longer exists.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("I don't have permission to pin messages in that channel.", ephemeral=True)
        except discord.HTTPException:
            await interaction.response.send_message(
                "Couldn't pin that message — the channel may already have 50 pins (Discord's limit).", ephemeral=True
            )

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.secondary, custom_id="pin_deny")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        req = await self._get_request(interaction)
        if req is None:
            return
        update_pin_request(req["request_id"], status="denied")
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.light_grey()
        self._set_status_field(embed, f"⚪ Denied by {interaction.user.mention}")
        await interaction.response.edit_message(embed=embed, view=None)

    @staticmethod
    def _set_status_field(embed: discord.Embed, value: str):
        for i, field in enumerate(embed.fields):
            if field.name == "Status":
                embed.set_field_at(i, name="Status", value=value, inline=False)
                return
        embed.add_field(name="Status", value=value, inline=False)




class TimeoutModal(discord.ui.Modal, title="Timeout User"):
    minutes = discord.ui.TextInput(label="Duration in minutes", placeholder="e.g. 60", max_length=6)

    def __init__(self, case_id: int, user_id: int):
        super().__init__()
        self.case_id = case_id
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            mins = int(self.minutes.value)
            if mins <= 0 or mins > 40320:  # Discord's timeout cap is 28 days
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Enter a whole number of minutes (1-40320).", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            member = await interaction.guild.fetch_member(self.user_id)
            await member.timeout(discord.utils.utcnow() + timedelta(minutes=mins), reason=f"Case #{self.case_id}")
            await interaction.followup.send(f"Timed out {member.mention} for {mins} minutes.", ephemeral=True)
        except discord.NotFound:
            await interaction.followup.send("That user is no longer in the server.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("I don't have permission to time out that user.", ephemeral=True)


class WarnModal(discord.ui.Modal, title="Warn User"):
    reason = discord.ui.TextInput(label="Warning reason", style=discord.TextStyle.paragraph, max_length=500)

    def __init__(self, case_id: int, user_id: int):
        super().__init__()
        self.case_id = case_id
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        add_warning(interaction.guild_id, self.user_id, interaction.user.id, self.reason.value)
        try:
            user = await bot.fetch_user(self.user_id)
            await user.send(f"You've received a warning in **{interaction.guild.name}**: {self.reason.value}")
            dm_note = ""
        except (discord.Forbidden, discord.HTTPException):
            dm_note = " (couldn't DM them — their DMs may be closed)"
        await interaction.followup.send(f"Warning logged for case #{self.case_id}.{dm_note}", ephemeral=True)


# ---------- report submission modal (from context menu) ----------

class ReportReasonModal(discord.ui.Modal, title="Report Message"):
    reason = discord.ui.TextInput(
        label="Why are you reporting this message?",
        style=discord.TextStyle.paragraph,
        placeholder="Optional, but helps mods act faster.",
        required=False,
        max_length=500,
    )

    def __init__(self, reported_message: discord.Message):
        super().__init__()
        self.reported_message = reported_message

    async def on_submit(self, interaction: discord.Interaction):
        message = self.reported_message
        await submit_report(
            interaction,
            message_id=message.id,
            channel_id=message.channel.id,
            message_author_id=message.author.id,
            content=message.content,
            author_mention=message.author.mention,
            channel_mention=message.channel.mention,
            jump_url=message.jump_url,
            reason=self.reason.value or None,
        )


async def submit_report(interaction: discord.Interaction, *, message_id: int, channel_id: int,
                         message_author_id: int, content, author_mention, channel_mention,
                         jump_url, reason):
    guild_id = interaction.guild_id
    report_channel = report_channel_for(guild_id)
    if report_channel is None:
        await interaction.response.send_message(
            "This server hasn't set up a report channel yet. Ask a mod to run /setreportchannel.",
            ephemeral=True,
        )
        return

    # duplicate check
    existing = find_open_report(guild_id, message_id)
    if existing is not None:
        new_count = existing["duplicate_count"] + 1
        update_report(existing["case_id"], duplicate_count=new_count)
        try:
            report_msg = await report_channel.fetch_message(existing["report_msg_id"])
            embed = report_msg.embeds[0]
            for i, field in enumerate(embed.fields):
                if field.name == "Reported by" and "users total" in field.value:
                    embed.set_field_at(i, name="Reported by", value=f"{new_count} users total", inline=False)
                    break
            else:
                embed.add_field(name="Reported by", value=f"{new_count} users total", inline=False)
            await report_msg.edit(embed=embed)
            if existing["thread_id"]:
                thread = interaction.guild.get_thread(existing["thread_id"])
                if thread:
                    await thread.send(f"Also reported by {interaction.user.mention}" + (f": {reason}" if reason else "."))
        except (discord.NotFound, discord.HTTPException):
            pass
        await interaction.response.send_message(
            f"This was already reported — added your report to case #{existing['case_id']}.",
            ephemeral=True,
        )
        return

    case_id = create_report(
        guild_id=guild_id,
        message_id=message_id or 0,
        channel_id=channel_id or 0,
        message_author_id=message_author_id or 0,
        reporter_id=interaction.user.id,
        reason=reason,
        jump_url=jump_url,
        report_channel_id=report_channel.id,
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    embed = build_report_embed(case_id, {
        "content": content,
        "author_mention": author_mention,
        "channel_mention": channel_mention,
        "reporter_mention": interaction.user.mention,
        "reason": reason,
        "jump_url": jump_url,
    })

    report_msg = await report_channel.send(embed=embed, view=ReportActionView())
    update_report(case_id, report_msg_id=report_msg.id)

    try:
        thread = await report_msg.create_thread(name=f"Report #{case_id}")
        update_report(case_id, thread_id=thread.id)
    except discord.HTTPException:
        pass  # thread creation isn't critical to the report existing

    await interaction.response.send_message(
        f"Thanks — this has been reported to the mods. Your reference is **case #{case_id}**.",
        ephemeral=True,
    )


# ---------- report gating (rate limit, self-report, bot, account age) ----------

async def can_report(interaction: discord.Interaction, target_user) -> bool:
    if target_user is not None:
        if target_user.bot:
            await interaction.response.send_message("You can't report a bot.", ephemeral=True)
            return False
        if target_user.id == interaction.user.id:
            await interaction.response.send_message("You can't report yourself.", ephemeral=True)
            return False

    account_age_days = (datetime.now(timezone.utc) - interaction.user.created_at).days
    if account_age_days < MIN_ACCOUNT_AGE_DAYS:
        await interaction.response.send_message(
            "Your account is too new to file reports. Contact a mod directly if this is urgent.",
            ephemeral=True,
        )
        return False

    if is_rate_limited(interaction.guild_id, interaction.user.id):
        await interaction.response.send_message(
            "You're reporting too quickly — please wait a few minutes and try again.",
            ephemeral=True,
        )
        return False

    return True


# ---------- context menu command ----------

@bot.tree.context_menu(name="Report Message")
async def report_message(interaction: discord.Interaction, message: discord.Message):
    if not await can_report(interaction, message.author):
        return
    record_report_attempt(interaction.guild_id, interaction.user.id)
    await interaction.response.send_modal(ReportReasonModal(message))


async def _can_use_simonsays(interaction: discord.Interaction) -> bool:
    if interaction.user.guild_permissions.administrator:
        return True
    row = get_config(interaction.guild_id)
    if row is not None and row["simonsays_role_id"] is not None:
        role = interaction.guild.get_role(row["simonsays_role_id"])
        if role is not None and role in interaction.user.roles:
            return True
    return False


@bot.tree.command(name="simonsays", description="Make the bot say something in this channel. Requires admin or the configured role.")
@app_commands.describe(text="What the bot should say")
@app_commands.check(_can_use_simonsays)
async def simon_says(interaction: discord.Interaction, text: str):
    await interaction.channel.send(text)
    await interaction.response.send_message("Said it.", ephemeral=True)

    log_channel = simonsays_log_channel_for(interaction.guild_id)
    if log_channel is not None:
        embed = discord.Embed(
            title="🗣️ /simonsays used",
            description=text,
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Used by", value=interaction.user.mention, inline=True)
        embed.add_field(name="Channel", value=interaction.channel.mention, inline=True)
        try:
            await log_channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass  # logging is best-effort — don't fail the command over it


@simon_says.error
async def simon_says_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
    else:
        raise error


@bot.tree.command(name="setsimonsaysrole", description="Set the role allowed to use /simonsays (admins can always use it).")
@app_commands.describe(role="The role that should be allowed to use /simonsays")
@app_commands.checks.has_permissions(manage_guild=True)
async def setsimonsaysrole(interaction: discord.Interaction, role: discord.Role):
    set_simonsays_role(interaction.guild_id, role.id)
    await interaction.response.send_message(f"{role.mention} can now use /simonsays.", ephemeral=True)


@bot.tree.command(name="setsimonsayslogchannel", description="Set a channel to log every /simonsays use (who, what, where). Unset = no logging.")
@app_commands.describe(channel="The channel to log /simonsays usage to")
@app_commands.checks.has_permissions(manage_guild=True)
async def setsimonsayslogchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    set_simonsays_log_channel(interaction.guild_id, channel.id)
    await interaction.response.send_message(f"/simonsays usage will now be logged to {channel.mention}.", ephemeral=True)


# ---------- /report slash command (no specific message required) ----------

@bot.tree.command(name="report", description="Report a user or situation to the mods (no specific message needed).")
@app_commands.describe(reason="What's going on?", user="Optional: the user this report is about")
async def report_slash(interaction: discord.Interaction, reason: str, user: discord.Member = None):
    if not await can_report(interaction, user):
        return
    record_report_attempt(interaction.guild_id, interaction.user.id)
    await submit_report(
        interaction,
        message_id=0,
        channel_id=interaction.channel_id,
        message_author_id=user.id if user else 0,
        content=None,
        author_mention=user.mention if user else None,
        channel_mention=interaction.channel.mention,
        jump_url=None,
        reason=reason,
    )


# ---------- /reports lookup ----------

@bot.tree.command(name="reports", description="View moderation history for a user.")
@app_commands.describe(user="The user to look up")
@app_commands.checks.has_permissions(manage_messages=True)
async def reports_lookup(interaction: discord.Interaction, user: discord.Member):
    history = get_history(interaction.guild_id, user.id)
    embed = discord.Embed(title=f"Moderation history — {user.display_name}", color=discord.Color.orange())
    embed.add_field(name="Times reported", value=str(history["reported_count"]), inline=True)
    embed.add_field(name="Warnings", value=str(len(history["warnings"])), inline=True)
    if history["recent_reports"]:
        lines = [f"#{r['case_id']} ({r['status']}): {r['reason'] or '*no reason given*'}" for r in history["recent_reports"]]
        embed.add_field(name="Recent reports", value="\n".join(lines)[:1024], inline=False)
    if history["warnings"]:
        lines = [f"{w['created_at'][:10]}: {w['reason']}" for w in history["warnings"][:5]]
        embed.add_field(name="Recent warnings", value="\n".join(lines)[:1024], inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------- config commands ----------

@bot.tree.command(name="setreportchannel", description="Set the channel where reported messages are sent.")
@app_commands.describe(channel="The channel mods will see reports in")
@app_commands.checks.has_permissions(manage_guild=True)
async def setreportchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    set_report_channel(interaction.guild_id, channel.id)
    await interaction.response.send_message(f"Reports will now be sent to {channel.mention}.", ephemeral=True)


@bot.tree.command(name="setoncallrole", description="Set the role pinged when a report is escalated.")
@app_commands.describe(role="The role to ping on escalation")
@app_commands.checks.has_permissions(manage_guild=True)
async def setoncallrole(interaction: discord.Interaction, role: discord.Role):
    set_oncall_role(interaction.guild_id, role.id)
    await interaction.response.send_message(f"Escalations will now ping {role.mention}.", ephemeral=True)


@bot.tree.command(name="setnoquoterole", description="Members with this role are exempt from having their messages saved via the 💬 quote reaction.")
@app_commands.describe(role="The role whose members' messages can't be quoted")
@app_commands.checks.has_permissions(manage_guild=True)
async def setnoquoterole(interaction: discord.Interaction, role: discord.Role):
    set_no_quote_role(interaction.guild_id, role.id)
    await interaction.response.send_message(f"Messages from {role.mention} can no longer be saved as quotes.", ephemeral=True)


@bot.tree.command(name="setpinrequestchannel", description="Set a separate channel for 📌 pin requests (defaults to the report channel if unset).")
@app_commands.describe(channel="The channel pin requests should go to")
@app_commands.checks.has_permissions(manage_guild=True)
async def setpinrequestchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    set_pin_request_channel(interaction.guild_id, channel.id)
    await interaction.response.send_message(f"Pin requests will now be sent to {channel.mention}.", ephemeral=True)


@bot.tree.command(name="setdmchannel", description="Set a separate channel for DM conversation threads (defaults to the report channel if unset).")
@app_commands.describe(channel="The channel DM conversation threads should be created in")
@app_commands.checks.has_permissions(manage_guild=True)
async def setdmchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    set_dm_channel(interaction.guild_id, channel.id)
    await interaction.response.send_message(f"DM conversation threads will now be created in {channel.mention}.", ephemeral=True)


async def _is_owner_check(interaction: discord.Interaction) -> bool:
    return await interaction.client.is_owner(interaction.user)


@bot.tree.command(name="setstatus", description="[Bot owner only] Set the bot's Discord status/activity (applies across all servers).")
@app_commands.describe(activity_type="Type of activity shown", text="The status text, e.g. 'for reports'")
@app_commands.choices(activity_type=[
    app_commands.Choice(name="Playing", value="playing"),
    app_commands.Choice(name="Watching", value="watching"),
    app_commands.Choice(name="Listening to", value="listening"),
    app_commands.Choice(name="Competing in", value="competing"),
])
@app_commands.check(_is_owner_check)
async def setstatus(interaction: discord.Interaction, activity_type: app_commands.Choice[str], text: str):
    set_setting("status_activity_type", activity_type.value)
    set_setting("status_text", text)
    await apply_saved_status()
    await interaction.response.send_message(f"Status updated: {activity_type.name} {text}", ephemeral=True)


@setstatus.error
async def setstatus_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        await interaction.response.send_message("Only the bot's owner can change its status.", ephemeral=True)
    else:
        raise error


async def get_or_create_dm_thread(guild: discord.Guild, user: discord.abc.User):
    """Returns (thread, error_reason). error_reason is None on success."""
    target_channel = dm_channel_for(guild.id)
    if target_channel is None:
        return None, "No DM or report channel is set up for this server. Ask a mod to run /setdmchannel or /setreportchannel."

    existing = get_dm_thread_for_user(guild.id, user.id)
    if existing is not None:
        thread = guild.get_thread(existing["thread_id"])
        if thread is None:
            try:
                thread = await guild.fetch_channel(existing["thread_id"])
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                thread = None
        if thread is not None:
            return thread, None
        # stored thread no longer exists (deleted/archived past retrieval) — fall through and make a new one

    display_name = getattr(user, "display_name", None) or user.name
    try:
        thread = await target_channel.create_thread(
            name=f"DM: {display_name}"[:100],
            type=discord.ChannelType.public_thread,
        )
    except discord.HTTPException as e:
        return None, f"Couldn't create a conversation thread: {e}"

    create_dm_thread(guild.id, user.id, thread.id)
    return thread, None


@bot.tree.command(name="dm", description="Send a direct message to a user on behalf of the moderation team.")
@app_commands.describe(
    user="The user to message",
    message="What to say",
    anonymous="If true (default), the DM won't name which moderator sent it",
)
@app_commands.checks.has_permissions(manage_messages=True)
async def dm_user(interaction: discord.Interaction, user: discord.Member, message: str, anonymous: bool = True):
    await interaction.response.defer(ephemeral=True, thinking=True)

    thread, error = await get_or_create_dm_thread(interaction.guild, user)
    if thread is None:
        await interaction.followup.send(error, ephemeral=True)
        return

    embed = discord.Embed(description=message, color=discord.Color.blurple())
    embed.set_author(name=f"Message from the moderators of {interaction.guild.name}")
    if not anonymous:
        embed.set_footer(text=f"Sent by {interaction.user.display_name}")

    try:
        await user.send(embed=embed)
        await thread.send(f"➡️ **{interaction.user.display_name}** sent: {message}")
        await interaction.followup.send(f"Sent your message to {user.mention} — see {thread.mention} for the conversation.", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(
            f"Couldn't DM {user.mention} — they may have DMs from server members disabled.", ephemeral=True
        )
    except discord.HTTPException as e:
        await interaction.followup.send(f"Something went wrong sending that: {e}", ephemeral=True)


@bot.tree.command(name="dmreply", description="Reply to a user from inside their DM conversation thread.")
@app_commands.describe(message="What to say back")
@app_commands.checks.has_permissions(manage_messages=True)
async def dm_reply(interaction: discord.Interaction, message: str):
    await interaction.response.defer(ephemeral=True, thinking=True)

    row = get_dm_thread_by_thread(interaction.channel_id)
    if row is None:
        await interaction.followup.send(
            "This only works inside a DM conversation thread created by /dm.", ephemeral=True
        )
        return

    try:
        user = await bot.fetch_user(row["user_id"])
    except discord.NotFound:
        await interaction.followup.send("That user no longer exists.", ephemeral=True)
        return

    embed = discord.Embed(description=message, color=discord.Color.blurple())
    embed.set_author(name=f"Message from the moderators of {interaction.guild.name}")

    try:
        await user.send(embed=embed)
        await interaction.channel.send(f"➡️ **{interaction.user.display_name}** sent: {message}")
        await interaction.followup.send("Reply sent.", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(f"Couldn't DM {user.mention} — their DMs may be closed.", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.followup.send(f"Something went wrong sending that: {e}", ephemeral=True)


class ConfirmClearView(discord.ui.View):
    def __init__(self, guild_id: int, requester_id: int, warning_count: int):
        super().__init__(timeout=30)
        self.guild_id = guild_id
        self.requester_id = requester_id
        self.warning_count = warning_count

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Only the person who ran the command can confirm this.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Yes, delete everything", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        rows = get_all_reports(self.guild_id)

        for row in rows:
            try:
                channel = interaction.guild.get_channel(row["report_channel_id"]) or await interaction.guild.fetch_channel(row["report_channel_id"])
                msg = await channel.fetch_message(row["report_msg_id"])
                await msg.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException, AttributeError):
                pass
            if row["thread_id"]:
                try:
                    thread = interaction.guild.get_thread(row["thread_id"]) or await interaction.guild.fetch_channel(row["thread_id"])
                    await thread.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException, AttributeError):
                    pass

        clear_all_reports(self.guild_id)
        clear_all_warnings(self.guild_id)
        for item in self.children:
            item.disabled = True
        await interaction.edit_original_response(
            content=f"Cleared all {len(rows)} report(s) and {self.warning_count} warning(s) for this server.",
            view=self,
        )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Cancelled — nothing was deleted.", view=self)
        self.stop()


@bot.tree.command(name="clearreports", description="Permanently delete ALL reports and warnings for this server, including open cases. Cannot be undone.")
@app_commands.checks.has_permissions(manage_guild=True)
async def clearreports(interaction: discord.Interaction):
    report_count = len(get_all_reports(interaction.guild_id))
    # count warnings directly (get_history is scoped to one user; count all for the guild instead)
    conn = db_connect()
    warning_count = conn.execute(
        "SELECT COUNT(*) FROM warnings WHERE guild_id = ?", (interaction.guild_id,)
    ).fetchone()[0]
    conn.close()
    await interaction.response.send_message(
        f"This will permanently delete all **{report_count}** report(s) and **{warning_count}** warning(s) for this server — "
        f"including open, unresolved cases — plus their messages/threads. This cannot be undone. Continue?",
        view=ConfirmClearView(interaction.guild_id, interaction.user.id, warning_count),
        ephemeral=True,
    )


class ConfirmPurgeChannelView(discord.ui.View):
    def __init__(self, channel: discord.TextChannel, requester_id: int, amount: int):
        super().__init__(timeout=30)
        self.channel = channel
        self.requester_id = requester_id
        self.amount = amount

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Only the person who ran the command can confirm this.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Yes, delete messages", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            deleted = await self.channel.purge(limit=self.amount)
        except discord.Forbidden:
            for item in self.children:
                item.disabled = True
            await interaction.edit_original_response(
                content="I don't have permission to delete messages in that channel (need Manage Messages + Read Message History).",
                view=self,
            )
            return
        except discord.HTTPException as e:
            for item in self.children:
                item.disabled = True
            await interaction.edit_original_response(content=f"Something went wrong partway through: {e}", view=self)
            return

        for item in self.children:
            item.disabled = True
        await interaction.edit_original_response(
            content=f"Deleted {len(deleted)} message(s) in {self.channel.mention}.",
            view=self,
        )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Cancelled — nothing was deleted.", view=self)
        self.stop()


@bot.tree.command(name="purgechannel", description="[Admin only] Bulk-delete recent messages in a channel. Cannot be undone.")
@app_commands.describe(amount="Number of recent messages to delete (max 1000)", channel="Channel to purge (defaults to this one)")
@app_commands.checks.has_permissions(administrator=True)
async def purgechannel(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 1000], channel: discord.TextChannel = None):
    target = channel or interaction.channel
    await interaction.response.send_message(
        f"This will permanently delete up to **{amount}** recent message(s) in {target.mention}. "
        f"Messages older than 14 days can't be bulk-deleted by Discord's API and will be skipped. This cannot be undone. Continue?",
        view=ConfirmPurgeChannelView(target, interaction.user.id, amount),
        ephemeral=True,
    )



@bot.tree.command(name="purgereports", description="Delete old resolved/dismissed reports (database + messages). Open cases are never touched.")
@app_commands.describe(older_than_days="Delete resolved/dismissed cases older than this many days")
@app_commands.checks.has_permissions(manage_messages=True)
async def purgereports(interaction: discord.Interaction, older_than_days: int):
    if older_than_days < 1:
        await interaction.response.send_message("Enter a number of 1 or more.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
    old_reports = get_old_closed_reports(interaction.guild_id, cutoff)

    deleted_messages = 0
    for row in old_reports:
        try:
            channel = interaction.guild.get_channel(row["report_channel_id"]) or await interaction.guild.fetch_channel(row["report_channel_id"])
            msg = await channel.fetch_message(row["report_msg_id"])
            await msg.delete()
            deleted_messages += 1
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, AttributeError):
            pass  # message/channel already gone — DB row still gets cleaned up below

    delete_reports([row["case_id"] for row in old_reports])

    await interaction.followup.send(
        f"Purged {len(old_reports)} resolved/dismissed case(s) older than {older_than_days} day(s) "
        f"({deleted_messages} report message(s) deleted). Open cases were left alone.",
        ephemeral=True,
    )


# ---------- quote commands ----------

QUOTES_PER_PAGE = 10


def resolve_quote(guild_id: int, number: int = None, author_id: int = None):
    """Looks up one quote. Returns (row, error_message) — exactly one is None."""
    if number is not None:
        row = get_quote(guild_id, number)
        if row is not None:
            return row, None
        rows = get_quotes(guild_id)
        if not rows:
            return None, "No quotes saved yet — react to a message with 💬 to save one."
        highest = max(r["quote_number"] for r in rows)
        return None, f"There's no quote #{number} here. Saved quotes go up to #{highest}."

    row = get_random_quote(guild_id, author_id)
    if row is None:
        if author_id is not None:
            return None, "That user doesn't have any saved quotes yet."
        return None, "No quotes saved yet — react to a message with 💬 to save one."
    return row, None


def format_quote_line(row) -> str:
    text = (row["content"] or "").replace("\n", " ").strip() or "*[no text content]*"
    if len(text) > 70:
        text = text[:69] + "…"
    author = f"<@{row['author_id']}>" if row["author_id"] else (row["author_name"] or "Unknown")
    return f"**{row['quote_number']}** — {text} — {author}"


class QuoteListView(discord.ui.View):
    """Paginated quote index. Only the person who ran the command can page it."""

    def __init__(self, rows, requester_id: int, title: str):
        super().__init__(timeout=180)
        self.rows = rows
        self.requester_id = requester_id
        self.title = title
        self.page = 0
        self.page_count = max(1, (len(rows) + QUOTES_PER_PAGE - 1) // QUOTES_PER_PAGE)
        self.message: discord.Message = None  # set by the caller right after sending
        self._sync_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Only the person who ran the command can page through this. Run it yourself to browse.",
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

    def _sync_buttons(self):
        self.previous_page.disabled = self.page <= 0
        self.next_page.disabled = self.page >= self.page_count - 1

    def build_embed(self) -> discord.Embed:
        chunk = self.rows[self.page * QUOTES_PER_PAGE : (self.page + 1) * QUOTES_PER_PAGE]
        embed = discord.Embed(
            title=self.title,
            description="\n".join(format_quote_line(r) for r in chunk) or "No quotes yet.",
            color=discord.Color.blurple(),
        )
        embed.set_footer(
            text=f"Page {self.page + 1}/{self.page_count} · {len(self.rows)} quote(s) · use .q <number> to read one"
        )
        return embed

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self.page_count - 1, self.page + 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


q_help_cooldown = commands.CooldownMapping.from_cooldown(1, 300, commands.BucketType.user)


@bot.command(name="q", aliases=["quote"])
async def quote_prefix(ctx: commands.Context, *, target: str = None):
    """.q -> random quote | .q 12 -> quote #12 | .q <username> -> list quotes by them |
    .q me -> list your own | .q list -> browse all | .q s <keyword> -> search quote text |
    .q delete <number> -> delete a quote you added (or any, with Manage Messages) |
    .q help -> list what everyone can do"""
    if ctx.guild is None:
        await ctx.send("Quotes only work inside a server.")
        return

    target = (target or "").strip()

    if target.lower() == "help":
        retry_after = q_help_cooldown.update_rate_limit(ctx.message)
        if retry_after:
            await ctx.send(f"`.q help` is on cooldown — try again in {retry_after:.0f}s.")
            return
        await ctx.send(
            "**Quote board commands (anyone can use these):**\n"
            "`.q` — show a random quote\n"
            "`.q <number>` — show quote #<number>\n"
            "`.q @user` / `.q <username>` — list quotes from someone\n"
            "`.q me` — list your own quotes\n"
            "`.q list` — browse every quote, paginated\n"
            "`.q s <keyword>` — search quote text\n"
            "`.q delete <number>` — delete a quote you added yourself\n"
            "React 💬 (or 🗨️ / 🗯️) on any message to save it as a new quote.\n"
            "React 📌 on any message to request that it be pinned."
        )
        return

    if target.lower() in ("list", "all"):
        rows = get_quotes(ctx.guild.id)
        if not rows:
            await ctx.send("No quotes saved yet — react to a message with 💬 to save one.")
            return
        view = QuoteListView(rows, ctx.author.id, f"Quotes in {ctx.guild.name}")
        view.message = await ctx.send(embed=view.build_embed(), view=view)
        return

    if target.lower() == "me":
        rows = get_quotes(ctx.guild.id, ctx.author.id)
        if not rows:
            await ctx.send("You don't have any saved quotes yet.")
            return
        view = QuoteListView(rows, ctx.author.id, f"Quotes from {ctx.author.display_name}")
        view.message = await ctx.send(embed=view.build_embed(), view=view)
        return

    parts = target.split(None, 1)
    if parts and parts[0].lower() == "s":
        keyword = parts[1].strip() if len(parts) > 1 else ""
        if not keyword:
            await ctx.send("Give me something to search for, e.g. `.q s pizza`.")
            return
        rows = search_quotes(ctx.guild.id, keyword)
        if not rows:
            await ctx.send(f"No quotes found matching **{keyword}**.")
            return
        view = QuoteListView(rows, ctx.author.id, f'Quotes matching "{keyword}"')
        view.message = await ctx.send(embed=view.build_embed(), view=view)
        return

    if parts and parts[0].lower() in ("delete", "del"):
        number_text = parts[1].strip().lstrip("#") if len(parts) > 1 else ""
        if not number_text.isdigit():
            await ctx.send("Give me a quote number to delete, e.g. `.q delete 12`.")
            return
        number = int(number_text)
        row = get_quote(ctx.guild.id, number)
        if row is None:
            await ctx.send(f"There's no quote #{number} in this server.")
            return
        if row["saved_by_id"] != ctx.author.id and not ctx.author.guild_permissions.manage_messages:
            await ctx.send(f"You can only delete quotes you added yourself — quote #{number} was added by someone else.")
            return
        delete_quote(ctx.guild.id, number)
        await ctx.send(f"Deleted quote #{number}.")
        return

    if not target:
        row, error = resolve_quote(ctx.guild.id)
        if row is None:
            await ctx.send(error)
            return
        await ctx.send(embed=build_quote_embed(row))
        return

    cleaned = target.lstrip("#")
    if cleaned.isdigit():
        row, error = resolve_quote(ctx.guild.id, number=int(cleaned))
        if row is None:
            await ctx.send(error)
            return
        await ctx.send(embed=build_quote_embed(row))
        return

    try:
        member = await commands.MemberConverter().convert(ctx, target)
    except commands.BadArgument:
        await ctx.send("Use `.q`, `.q <number>`, `.q <username>`, `.q list`, or `.q s <keyword>`.")
        return

    rows = get_quotes(ctx.guild.id, member.id)
    if not rows:
        await ctx.send(f"No quotes saved for {member.display_name} yet.")
        return
    view = QuoteListView(rows, ctx.author.id, f"Quotes from {member.display_name}")
    view.message = await ctx.send(embed=view.build_embed(), view=view)


@bot.tree.command(name="quote", description="Show a saved quote — by number, at random, or at random from one user.")
@app_commands.describe(number="Quote number, e.g. 12", user="Pick a random quote from this user instead")
async def quote_slash(interaction: discord.Interaction, number: int = None, user: discord.Member = None):
    row, error = resolve_quote(interaction.guild_id, number, user.id if user else None)
    if row is None:
        await interaction.response.send_message(error, ephemeral=True)
        return
    await interaction.response.send_message(embed=build_quote_embed(row))


@bot.tree.command(name="quotes", description="Browse every quote saved in this server.")
@app_commands.describe(user="Only show quotes from this user")
async def quotes_list(interaction: discord.Interaction, user: discord.Member = None):
    rows = get_quotes(interaction.guild_id, user.id if user else None)
    if not rows:
        await interaction.response.send_message(
            f"No quotes saved for {user.display_name} yet." if user
            else "No quotes saved yet — react to a message with 💬 to save one.",
            ephemeral=True,
        )
        return
    title = f"Quotes from {user.display_name}" if user else f"Quotes in {interaction.guild.name}"
    view = QuoteListView(rows, interaction.user.id, title)
    await interaction.response.send_message(embed=view.build_embed(), view=view)
    view.message = await interaction.original_response()


@bot.tree.command(name="delquote", description="Delete a saved quote by its number.")
@app_commands.describe(number="The quote number to delete")
@app_commands.checks.has_permissions(manage_messages=True)
async def delquote(interaction: discord.Interaction, number: int):
    if delete_quote(interaction.guild_id, number):
        await interaction.response.send_message(f"Deleted quote #{number}.", ephemeral=True)
    else:
        await interaction.response.send_message(f"There's no quote #{number} in this server.", ephemeral=True)


class ConfirmDefragQuotesView(discord.ui.View):
    def __init__(self, guild_id: int, requester_id: int):
        super().__init__(timeout=30)
        self.guild_id = guild_id
        self.requester_id = requester_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Only the person who ran the command can confirm this.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Yes, renumber", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        changed = renumber_quotes(self.guild_id)
        for item in self.children:
            item.disabled = True
        await interaction.edit_original_response(
            content=f"Done — quotes are now numbered 1..N with no gaps ({changed} quote(s) got a new number).",
            view=self,
        )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Cancelled — numbering left as-is.", view=self)
        self.stop()


@bot.tree.command(name="defragquotes", description="Renumber quotes to close gaps (e.g. after deletions), so numbers run 1..N with no gaps.")
@app_commands.checks.has_permissions(manage_messages=True)
async def defragquotes(interaction: discord.Interaction):
    rows = get_quotes(interaction.guild_id)
    if not rows:
        await interaction.response.send_message("No quotes saved yet — nothing to renumber.", ephemeral=True)
        return
    await interaction.response.send_message(
        f"This will renumber all {len(rows)} quote(s) in this server to run 1..N with no gaps, preserving their "
        f"current order. Existing `.q <number>` references will point to different quotes afterward. Continue?",
        view=ConfirmDefragQuotesView(interaction.guild_id, interaction.user.id),
        ephemeral=True,
    )


async def _send_klipy_gif(interaction: discord.Interaction, query: str) -> None:
    if not KLIPY_API_KEY:
        await interaction.response.send_message(
            "This command needs a Klipy API key — set the KLIPY_API_KEY environment variable and restart the bot.",
            ephemeral=True,
        )
        return

    await interaction.response.defer()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://api.klipy.com/api/v1/{KLIPY_API_KEY}/gifs/search",
                params={
                    "q": query,
                    "customer_id": str(interaction.user.id),
                    "per_page": 50,
                    "content_filter": "high",
                    "format_filter": "gif",
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    await interaction.followup.send("Couldn't reach Klipy right now — try again in a bit.")
                    return
                data = await resp.json()
    except (aiohttp.ClientError, TimeoutError):
        await interaction.followup.send("Couldn't reach Klipy right now — try again in a bit.")
        return

    if not data.get("result"):
        await interaction.followup.send("Couldn't reach Klipy right now — try again in a bit.")
        return

    results = (data.get("data") or {}).get("data") or []
    if not results:
        await interaction.followup.send(f"Couldn't find a {query} gif — try again in a bit.")
        return

    files = random.choice(results).get("file") or {}
    gif_url = None
    for size in ("md", "hd", "sm", "xs"):
        variant = files.get(size, {}).get("gif")
        if variant:
            gif_url = variant["url"]
            break

    if not gif_url:
        await interaction.followup.send(f"Couldn't find a {query} gif — try again in a bit.")
        return

    await interaction.followup.send(gif_url)


@bot.tree.command(name="capybara", description="Post a random capybara gif.")
async def capybara(interaction: discord.Interaction):
    await _send_klipy_gif(interaction, "capybara")


@bot.tree.command(name="foxxo", description="Post a random fox gif.")
async def foxxo(interaction: discord.Interaction):
    await _send_klipy_gif(interaction, "fox")


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    # "." is a command prefix, so plenty of ordinary messages ("...", ".hmm")
    # look like commands. Stay quiet on those instead of logging noise.
    if isinstance(error, (commands.CommandNotFound, commands.CheckFailure)):
        return
    if isinstance(error, commands.BadArgument):
        await ctx.send("I didn't understand that — try `.q <number>`.")
        return
    raise error


async def _permission_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You don't have permission to do that.", ephemeral=True)
    else:
        raise error


purgereports.error(_permission_error)
purgechannel.error(_permission_error)
clearreports.error(_permission_error)
setreportchannel.error(_permission_error)
setoncallrole.error(_permission_error)
setnoquoterole.error(_permission_error)
setpinrequestchannel.error(_permission_error)
setdmchannel.error(_permission_error)
setsimonsaysrole.error(_permission_error)
setsimonsayslogchannel.error(_permission_error)
reports_lookup.error(_permission_error)
dm_user.error(_permission_error)
dm_reply.error(_permission_error)
delquote.error(_permission_error)
defragquotes.error(_permission_error)


# ---------- reaction triggers: 📌 pin request, 💬 save quote ----------

PIN_EMOJI = "📌"
QUOTE_EMOJIS = ("💬", "🗨️", "🗯️")


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.guild_id is None:  # ignore DMs
        return
    if payload.member is not None and payload.member.bot:
        return

    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return

    emoji = str(payload.emoji)
    if emoji == PIN_EMOJI:
        await handle_pin_request(payload, guild)
    elif emoji in QUOTE_EMOJIS:
        await handle_quote_save(payload, guild)


async def handle_quote_save(payload: discord.RawReactionActionEvent, guild: discord.Guild):
    try:
        channel = guild.get_channel(payload.channel_id) or await guild.fetch_channel(payload.channel_id)
        message = await channel.fetch_message(payload.message_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return

    no_quote_role_id = no_quote_role_id_for(payload.guild_id)
    if no_quote_role_id is not None:
        author_member = message.author if isinstance(message.author, discord.Member) else guild.get_member(message.author.id)
        if author_member is not None and any(role.id == no_quote_role_id for role in author_member.roles):
            return  # this author is exempt from being quoted

    image_url = None
    for attachment in message.attachments:
        if attachment.content_type and attachment.content_type.startswith("image/"):
            image_url = attachment.url
            break

    content = message.content or ""
    if message.attachments and not content:
        content = f"*[{len(message.attachments)} attachment(s)]*"

    quote_number, created = create_quote(
        guild_id=payload.guild_id,
        message_id=payload.message_id,
        channel_id=payload.channel_id,
        author_id=message.author.id,
        author_name=message.author.display_name,
        content=content,
        image_url=image_url,
        jump_url=message.jump_url,
        saved_by_id=payload.user_id,
        message_created_at=message.created_at.isoformat(),
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    if quote_number is None:
        return  # couldn't allocate a number; nothing useful to say

    saver = payload.member or guild.get_member(payload.user_id)
    saver_name = saver.display_name if saver else "someone"
    note = (
        f"New quote added by {saver_name} as #{quote_number} {message.jump_url}"
        if created
        else f"💬 That's already **quote #{quote_number}** — recall it with `.q {quote_number}`\n{message.jump_url}"
    )
    try:
        await channel.send(note, reference=message, mention_author=False)
    except (discord.Forbidden, discord.HTTPException):
        pass  # the quote is saved either way; the confirmation is a nicety


async def handle_pin_request(payload: discord.RawReactionActionEvent, guild: discord.Guild):
    report_channel = pin_request_channel_for(payload.guild_id)
    if report_channel is None:
        return  # no report/pin-request channel configured for this server yet

    # only ever allow one pin request per message, regardless of how the first one was resolved
    if find_pin_request_for_message(payload.guild_id, payload.message_id) is not None:
        return

    try:
        channel = guild.get_channel(payload.channel_id) or await guild.fetch_channel(payload.channel_id)
        message = await channel.fetch_message(payload.message_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return

    requestor = payload.member or guild.get_member(payload.user_id)
    requestor_mention = requestor.mention if requestor else f"<@{payload.user_id}>"

    embed = discord.Embed(
        title="📌 Pin Request",
        color=discord.Color.gold(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(
        name="Message",
        value=message.content[:1024] if message.content else "*[no text content]*",
        inline=False,
    )
    embed.add_field(name="Author", value=message.author.mention, inline=True)
    embed.add_field(name="Channel", value=channel.mention, inline=True)
    embed.add_field(name="Requested by", value=requestor_mention, inline=True)
    embed.add_field(name="Jump to message", value=f"[Click here]({message.jump_url})", inline=False)
    embed.add_field(name="Status", value="🟡 Pending", inline=False)

    request_msg = await report_channel.send(embed=embed, view=PinRequestView())

    request_id = create_pin_request(
        guild_id=payload.guild_id,
        message_id=payload.message_id,
        channel_id=payload.channel_id,
        requestor_id=payload.user_id,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    update_pin_request(request_id, request_msg_id=request_msg.id)


# ---------- DM reply relay ----------

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if message.guild is not None:  # everything past this point only handles DMs
        await maybe_reply_good_bot(message)
        await bot.process_commands(message)
        return

    threads = get_dm_threads_for_user(message.author.id)
    if not threads:
        await bot.process_commands(message)
        return

    content = message.content or "*[no text content]*"
    if message.attachments:
        content += f" ({len(message.attachments)} attachment(s): " + ", ".join(a.url for a in message.attachments) + ")"

    for row in threads:
        guild = bot.get_guild(row["guild_id"])
        if guild is None:
            continue
        thread = guild.get_thread(row["thread_id"])
        if thread is None:
            try:
                thread = await guild.fetch_channel(row["thread_id"])
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                continue
        try:
            await thread.send(f"↩️ **{message.author}** replied: {content}")
        except (discord.Forbidden, discord.HTTPException):
            continue

    await bot.process_commands(message)


ACTIVITY_TYPE_MAP = {
    "playing": discord.ActivityType.playing,
    "watching": discord.ActivityType.watching,
    "listening": discord.ActivityType.listening,
    "competing": discord.ActivityType.competing,
}
DEFAULT_STATUS_TYPE = "watching"
DEFAULT_STATUS_TEXT = "for reports"


async def apply_saved_status():
    activity_type_key = get_setting("status_activity_type", DEFAULT_STATUS_TYPE)
    text = get_setting("status_text", DEFAULT_STATUS_TEXT)
    activity_type = ACTIVITY_TYPE_MAP.get(activity_type_key, discord.ActivityType.watching)
    await bot.change_presence(activity=discord.Activity(type=activity_type, name=text))


# ---------- lifecycle ----------

@bot.event
async def on_ready():
    bot.add_view(ReportActionView())  # re-register persistent buttons after a restart
    bot.add_view(PinRequestView())
    await apply_saved_status()

    test_guild_id = os.environ.get("TEST_GUILD_ID")
    if test_guild_id:
        guild = discord.Object(id=int(test_guild_id))
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        print(f"Commands synced instantly to guild {test_guild_id}.")
    else:
        await bot.tree.sync()
        print("Commands synced globally (may take up to ~1hr to appear).")

    print(f"Logged in as {bot.user} (id: {bot.user.id})")


def main():
    init_db()
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("Set the DISCORD_BOT_TOKEN environment variable before running this bot.")
    bot.run(token)


if __name__ == "__main__":
    main()
