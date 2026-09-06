# Privacy Policy

**Last updated: 6 September 2026**

This Privacy Policy explains what data **PoohBot** (the "Bot") collects,
how it is used, and how it is stored. The Bot is operated by **Poohstrnak** ("we," "us," or "our"). By adding the Bot to a Discord
server or interacting with it, you acknowledge the practices described
here.

## 1. Data We Collect

The Bot stores the following data locally in a SQLite database on the
server(s) that run it:

| Data | Collected when | Includes |
|---|---|---|
| Report cases | A message/user is reported | Reported message content, reporter's user ID, reported user's ID, channel/message IDs, reason text, timestamps, moderator who resolved it |
| Warnings | A moderator issues a warning | Warned user's ID, moderator's ID, reason text, timestamp |
| Pin requests | A user reacts 📌 to a message | Requesting user's ID, message and channel IDs, approval/denial status |
| Moderator DMs | A moderator uses the DM feature | Message content sent to and received from the user, associated Discord IDs |
| Server configuration | An admin runs a setup command | Configured channel IDs, role IDs, and bot status preferences |

We do **not** collect data outside of these features — for example, the Bot
does not log ordinary messages that are never reported, reacted to with a
pin request, or otherwise passed through one of the features above.

## 2. How We Use This Data

Collected data is used solely to operate the Bot's features: displaying
report cases to moderators, tracking a user's report/warning history,
relaying messages between moderators and users, and remembering
per-server configuration. We do not use this data for advertising,
profiling, or any purpose unrelated to the Bot's moderation functions.

## 3. Data Sharing

We do not sell or share collected data with third parties. Data is only
visible to:

- Moderators and administrators within the same Discord server, through
  the Bot's own commands and report channels.
- Us, as the Bot's operator, to the extent necessary to maintain, debug,
  or secure the Bot.

The Bot operates on top of Discord, and your use of Discord itself is
separately governed by [Discord's Privacy Policy](https://discord.com/privacy).

## 4. Data Storage and Security

Data is stored in a local database file on the infrastructure running the
Bot. We take reasonable measures to protect this data (e.g., restricting
file access to the hosting environment) but cannot guarantee absolute
security, as no method of storage is completely immune to compromise.

## 5. Data Retention

Data persists until:

- A server administrator deletes it using the Bot's built-in cleanup
  commands (e.g., commands that purge old or all report/warning records), or
- The Bot is removed from a server and its underlying data is separately
  deleted by us upon request.

We do not automatically delete data on a fixed schedule unless a server
administrator configures the Bot to do so.

## 6. Your Rights

Depending on your jurisdiction, you may have rights to access, correct, or
request deletion of personal data associated with you. Because report and
warning data is managed by each individual Discord server's moderators,
requests to access or delete your data should generally start with that
server's administrators, who can use the Bot's commands directly. For
data-deletion requests we would need to handle directly, contact **poohbot@poohstrnak.com**.

## 7. Children's Privacy

The Bot is not directed at children under Discord's minimum age requirement
(currently 13, or higher where required by local law), consistent with
Discord's own Terms of Service. We do not knowingly collect data from users
who do not meet this requirement.

## 8. Changes to This Policy

We may update this Privacy Policy from time to time. Material changes will
be reflected in the "Last updated" date above. Continued use of the Bot
after changes take effect constitutes acceptance of the revised policy.

## 9. Contact

Questions about this Privacy Policy, or requests regarding your data, can
be directed to **poohbot@poohstrnak.com**.
