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
  admins or a role you configure with `/setsimonsaysrole`.
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
| `/simonsays` | Admin or configured role | Make the bot say something |
| `/setstatus` | Bot owner | Change the bot's Discord status |

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
     Manage Messages, Moderate Members, Manage Threads, Create Public
     Threads**.

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
it needs on startup.

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
