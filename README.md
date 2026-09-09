# Discord Report Bot

A moderation bot for Discord that gives your community a proper reporting
pipeline: users flag messages or situations, mods get a tracked case with
one-click actions, and a handful of related moderation tools (pin requests,
mod-to-user DMs, bulk cleanup) live alongside it.

Built with `discord.py`. Data is stored locally in SQLite — no external
database or paid services required.

To add poohbot to your server: [Click this link](https://discord.com/oauth2/authorize?client_id=1503890279089045646&permissions=8&integration_type=0&scope=bot)

## Features

**Reporting**
- Right-click any message → **Apps → Report Message** to report it, with an
  optional reason typed into a popup.
- `/report` for situations that don't have one specific message (optionally
  tag a user).
- Every report gets a **case number**, a dedicated **discussion thread**, and
  a set of mod-only action buttons: **Resolve**, **Dismiss**, **Escalate**,
  **Delete Message**, **Timeout**, **Warn**. Resolving, dismissing, or
  deleting the message closes out the thread automatically.
- Re-reporting the same message bumps a "reported by N users" counter
  instead of spamming a duplicate case.
- Built-in guardrails: rate limiting (3 reports / 5 min per user by default),
  blocks on reporting bots or yourself, and a minimum account-age requirement.
- `/reports @user` — pull up someone's moderation history (times reported +
  warnings) at a glance.

**Pin requests**
- Anyone can react 📌 on a message to request it be pinned. The bot posts the
  request to a configurable channel with **Approve** (actually pins the
  message) and **Deny** buttons — open to anyone who can see that channel.
  Each message can only ever generate one pin request.

**Quote board**
- React 💬 (also 🗨️ or 🗯️) on any message to save it as a numbered quote —
  no setup required, no approval needed, works instantly in any channel the
  bot can read.
- Recall one with `.q <number>` or `/quote number:`. `.q` with nothing after
  it grabs a random quote; `.q @user` / `.q <username>` lists everything from
  one person; `.q me` lists your own; `.q list` (or `/quotes`) browses
  everything, paginated; `.q s <keyword>` (or `/quotes`) searches quote text.
- `.q delete <number>` lets you remove a quote you added yourself (matched
  against who triggered the 💬 save, not who was quoted); anyone with Manage
  Messages can delete any quote this way too.
- `.q help` lists everything in this section that's open to anyone, including
  the 📌 pin-request reaction — it leaves out mod-only stuff like `/delquote`
  and `/defragquotes`. Limited to once per 5 minutes per user.
- Quotes render in the classic UB3R-B0T style: `#<number>`, the quote text, a
  bullet with the author mention and a jump link, and a plain footer with the
  original message's timestamp.
- `/delquote number:` removes one (Manage Messages). `/defragquotes`
  renumbers everything to close gaps left by deletions, behind a
  confirmation prompt.
- `/setnoquoterole` exempts a role's members from ever being quoted — 💬
  reactions on their messages are silently ignored.
- `import_quotes.py` (run separately, not a bot command) bulk-imports a
  `quotes.json` export from another quote bot into `reportbot.db`.

**Fun extras**
- Say "good bot" (any casing/punctuation) in a channel the bot can see and
  it replies "no u" — once per channel per 5 minutes.

**Moderator DMs**
- `/dm` sends a message to a user on the moderation team's behalf, creating a
  dedicated conversation thread per user. The user's DM replies are relayed
  automatically into that thread.
- `/dmreply`, used from inside the thread, replies back without needing to
  re-specify the user.

**Cleanup & admin tools**
- `/purgereports` — deletes old resolved/dismissed cases (open cases are
  never touched).
- `/clearreports` — wipes every report and warning for the server, including
  open cases, behind a confirmation prompt. Use with care.
- `/purgechannel` — bulk-deletes recent messages in a channel (Admin only),
  also behind a confirmation prompt.
- `/simonsays` — makes the bot say something in the channel; usable by
  admins or a role you configure with `/setsimonsaysrole`. Optionally log
  every use (who, what, where) to a channel with `/setsimonsayslogchannel`.
- `/setstatus` — sets the bot's Discord activity status (bot owner only).

## Commands

| Command | Who can use it | What it does |
|---|---|---|
| **Report Message** (right-click) | Anyone | Report a specific message |
| `/report` | Anyone | Report a user/situation with no specific message |
| `/reports @user` | Manage Messages | View a user's report/warning history |
| `/dm` | Manage Messages | Message a user via the bot, opens a thread |
| `/dmreply` | Manage Messages | Reply from inside a DM thread |
| `/purgereports` | Manage Messages | Delete old closed cases |
| `/purgechannel` | Administrator | Bulk-delete recent messages |
| `/clearreports` | Manage Server | Wipe all reports/warnings (confirms first) |
| `/setreportchannel` | Manage Server | Set where reports are posted |
| `/setpinrequestchannel` | Manage Server | Set a separate channel for pin requests |
| `/setdmchannel` | Manage Server | Set a separate channel for DM threads |
| `/setoncallrole` | Manage Server | Set the role pinged on Escalate |
| `/setsimonsaysrole` | Manage Server | Grant a role access to `/simonsays` |
| `/setsimonsayslogchannel` | Manage Server | Set a channel to log `/simonsays` usage |
| `/simonsays` | Admin or configured role | Make the bot say something |
| `/setstatus` | Bot owner | Change the bot's Discord status |
| `.q` / `.q <number>` / `.q @user` / `.q me` | Anyone | Recall a quote (random / by number / by author / your own) |
| `.q list` / `.q s <keyword>` | Anyone | Browse or search saved quotes |
| `.q delete <number>` | Quote's adder, or Manage Messages | Delete a quote by number |
| `.q help` | Anyone | List the quote commands everyone can use (5m per-user cooldown) |
| `/quote`, `/quotes` | Anyone | Slash equivalents of `.q` and `.q list`/`.q s` |
| `/delquote` | Manage Messages | Delete a quote by number |
| `/defragquotes` | Manage Messages | Renumber quotes to close gaps (confirms first) |
| `/setnoquoterole` | Manage Server | Exempt a role's members from being quoted |

Channel settings (`setpinrequestchannel`, `setdmchannel`) fall back to the
main report channel if left unset.

## Setup

1. **Install dependencies** (Python 3.10+; `sqlite3` is in the standard
   library, nothing else to install for storage):
   ```bash
   pip install -U discord.py
   ```

2. **Create a bot application** at the
   [Discord Developer Portal](https://discord.com/developers/applications).
   - Invite it with the `bot` and `applications.commands` scopes.
   - Grant these permissions: **View Channels, Send Messages, Embed Links,
     Read Message History, Manage Messages, Moderate Members, Manage
     Threads, Create Public Threads**.
   - On the Bot page, turn on **Message Content Intent** — required for the
     `.q` and "good bot" text commands to see message content. Everything
     else (slash commands, reactions) works without it.

3. **Set environment variables:**
   ```bash
   export DISCORD_BOT_TOKEN="your-token-here"
   # optional, for instant command sync to one server while testing:
   export TEST_GUILD_ID="your-server-id"
   ```

4. **Run it:**
   ```bash
   python report_bot.py
   ```

5. **Configure it in Discord** (requires Manage Server):
   ```
   /setreportchannel #mod-reports
   /setoncallrole @Mods
   ```
   Everything else works with sensible defaults from there.

For always-on hosting, run it under a process manager like `systemd` so it
restarts automatically and survives reboots.

## Data

Everything is stored in a single SQLite file, `reportbot.db`, created
automatically on first run in the working directory. No manual migration
steps are needed when pulling updates — the bot adds any new columns/tables
it needs on startup. This includes the `quotes` table used by the quote
board.

Migrating from another quote bot? `python3 import_quotes.py quotes.json`
bulk-loads a JSON export into `reportbot.db`, preserving original quote
numbers and skipping anything already imported — see the script's docstring
for options.

## Notes

- Commands gated behind a specific Discord permission (Manage Messages,
  Manage Server, Administrator) are hidden from the Discord UI entirely for
  users without it — that's Discord's own behavior, not a bug.
- `/simonsays` is the exception: since it can be granted via a custom role
  rather than a built-in permission, it's visible to everyone but replies
  with an error if the user isn't eligible.
- New slash commands can take up to ~1 hour to appear after a restart
  (global sync). Set `TEST_GUILD_ID` for instant sync to one server while
  developing.
- Text commands (`.q`, and the `!` alt prefix) are case-insensitive —
  `.Q`, `.QUOTE`, etc. all work the same as `.q`.
