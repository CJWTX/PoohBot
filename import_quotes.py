"""
One-off importer: loads quotes from an old bot's quotes.json export into
report_bot.py's SQLite database (reportbot.db), preserving the original
quote numbers so existing ".q <n>" references keep working.

Usage:
    python3 import_quotes.py quotes.json [--db reportbot.db]

Safe to re-run: existing rows (matched by guild_id + message_id, or by
guild_id + quote_number) are left alone instead of duplicated/erroring.
"""

import argparse
import json
import sqlite3
from datetime import datetime, timezone

# Keep this in sync with init_db()'s "quotes" table in report_bot.py.
CREATE_QUOTES_TABLE = """
CREATE TABLE IF NOT EXISTS quotes (
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
)
"""
CREATE_INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_quotes_guild_number ON quotes (guild_id, quote_number)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_quotes_guild_message ON quotes (guild_id, message_id)",
]

INSERT_SQL = """
INSERT OR IGNORE INTO quotes
    (guild_id, quote_number, message_id, channel_id, author_id, author_name,
     content, image_url, jump_url, saved_by_id, message_created_at, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("json_file", help="Path to the exported quotes.json")
    parser.add_argument("--db", default="reportbot.db", help="Path to reportbot.db (default: reportbot.db)")
    args = parser.parse_args()

    with open(args.json_file, encoding="utf-8") as f:
        entries = json.load(f)

    conn = sqlite3.connect(args.db)
    conn.execute(CREATE_QUOTES_TABLE)
    for stmt in CREATE_INDEXES:
        conn.execute(stmt)

    imported = 0
    skipped = 0
    for entry in entries:
        guild_id = int(entry["server"])
        quote_number = int(entry["id"])
        message_id = int(entry["messageId"])
        channel_id = int(entry["channelId"])
        author_id = int(entry["userId"])
        jump_url = f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"
        when = entry.get("dateTime") or datetime.now(timezone.utc).isoformat()

        cur = conn.execute(
            INSERT_SQL,
            (
                guild_id,
                quote_number,
                message_id,
                channel_id,
                author_id,
                entry.get("nick"),
                entry.get("text"),
                None,  # image_url — not present in the old export
                jump_url,
                None,  # saved_by_id — unknown for imported quotes
                when,
                when,
            ),
        )
        if cur.rowcount:
            imported += 1
        else:
            skipped += 1

    conn.commit()
    conn.close()
    print(f"Imported {imported} quote(s), skipped {skipped} already-present quote(s).")


if __name__ == "__main__":
    main()
