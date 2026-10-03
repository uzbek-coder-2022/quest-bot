# Quest Bot — Project Plan

## 1. Goal and scope

Build a Telegram bot with aiogram 3 for creating and running quests and managing participants. The user interface supports Uzbek, Russian, and English. There will be no web admin panel and no advertising.

The first release runs entirely inside Telegram. Production deployments use PostgreSQL through `asyncpg`; SQLite remains available for local development and unit tests. Quest titles, descriptions, and questions are displayed in the language entered by the quest author; changing the interface language does not automatically translate quest content.

## 2. Functional components

### A. Participant experience
- `/start`, the main menu, `/help`, and language selection.
- Browse public quests by status: scheduled, active, completed, or archived.
- Pagination size configurable by a superadmin: 5, 10, 20, or 50 records.
- Join public quests in the bot; join private quests through a bot deep link.
- Submit answers in a private chat with the bot.
- Quest leaderboard ordered by correctly solved stages, completion time, and then join time.

### B. Quest creation and participation
- Quest fields: title, description, public/private visibility, start time, overall duration, number of stages, and the chat in which questions are published.
- Each stage has a question, automatic or manual answer checking, an attempt limit, and an optional time limit.
- Stage progression modes:
  - **Immediate:** after a correct answer, the next question is sent privately to that participant.
  - **Scheduled:** each stage is announced at its configured time and delivered to participants.
- Automatic checking uses exact matching after Unicode NFC normalization and trimming leading/trailing whitespace. Letter case, punctuation, and internal whitespace remain significant.
- Answers requiring manual review are sent with review controls to the quest owner and superadmins.
- When a participant exhausts the attempt limit or a stage time limit, that participant is marked `failed` for the quest.
- A quest ends when its overall duration expires, an admin finishes it, or all existing participants reach a terminal status. A quest with no participants remains open until its overall duration expires or an admin finishes it.

### C. Admins and superadmins
- Superadmins are configured by Telegram user ID in `.env`; each superadmin must open the bot with `/start`.
- A superadmin can add or remove admins by numeric Telegram user ID.
- An admin can manage only quests they created; superadmins can view and manage every quest.
- Quest management includes leaderboards, participants, manually reviewed answers, and finishing a quest.
- An admin can block a participant from a specific quest, optionally provide a reason, and notify the participant. The block can also be reversed.
- Only superadmins can archive and unarchive quests.
- The superadmin panel includes admin management, all quests, statistics, audit logs, pagination settings, managed group/channels, and ZIP data export.

### D. Group and channel integration
- Superadmins register managed chats. A quest can publish to one registered group/channel or run in the bot only.
- In scheduled mode, each stage question is announced in the selected chat once. Participants submit their answers privately to the bot.
- In immediate mode, only the first stage is published in the chat. Later stages are sent privately to each participant so one participant's progress does not reveal a later question to everyone.
- Superadmins can enable chat cleanup per managed chat. At quest start, the bot removes tracked members who are not on that chat's whitelist.
- `chat_member` updates track membership changes received after a chat is registered.

### E. Invite links
- A private quest gets a bot deep-link token that allows users to join that private quest.
- Each participant who joins a quest with an attached Telegram chat receives an individual invite link. Participants who joined before the quest starts receive it after start-time cleanup; participants who join an already-active quest receive it immediately. The link can be retrieved from the bot again later. The bot creates invite links with `member_limit=1` and no `expire_date`; the link's one-person limit is consumed by the first successful join.
- The invite link created for a participant is stored once and the same link is returned when requested again.

### F. Support tickets and audit
- A user can open a support conversation with a superadmin or an admin of a quest they joined.
- Messages in a ticket are relayed in both directions through the bot.
- Important admin actions are written to the audit log. Superadmins can view recent entries and export data.

## 3. Roles and permissions

| Action | Participant | Admin | Superadmin |
|---|---:|---:|---:|
| Browse and join public quests | Yes | Yes | Yes |
| Create a quest | No | Yes | Yes |
| Manage a quest | No | Own quests only | All quests |
| Review manual answers | No | Own quests only | All quests |
| Block a participant from a quest | No | Own quests only | All quests |
| Add or remove admins | No | No | Yes |
| Archive quests, change global settings, and export data | No | No | Yes |

## 4. Status and time rules

- Quest lifecycle: `scheduled` → `active` → `completed`; a superadmin can move a quest to `archived` or unarchive it.
- User-entered times use `Asia/Tashkent`; the database stores UTC ISO 8601 timestamps.
- Overall and stage time limits are entered in minutes; `0` means no limit.
- In scheduled mode, stage start times must be strictly increasing and, when an overall duration is set, must not extend past the quest end time.
- When the next scheduled stage begins, any still-open previous stage is closed for that participant.

## 5. Technical structure

- `main.py` — bot startup, routers, and scheduler.
- `quest_bot/handlers/` — common menu, quest creation, participant and leaderboard flows, admin tools, and support tickets.
- `quest_bot/database.py` — PostgreSQL/SQLite persistence and transactions.
- `quest_bot/postgres.py` — PostgreSQL driver compatibility for shared repository queries and schema.
- `quest_bot/backups.py` — temporary PostgreSQL `pg_dump` or SQLite backups for delivery.
- `quest_bot/lifecycle.py` — localized superadmin startup/shutdown notifications and backup delivery.
- `quest_bot/scheduler.py` — quest starts, scheduled stages, and timeout handling.
- `deploy.sh` — PostgreSQL validation and systemd service installation/startup.
- `stop.sh` — safely stop the systemd service during troubleshooting.
- `quest_bot/services.py` — question delivery, group announcements, and chat-member removal.
- `quest_bot/localization.py` — Uzbek, Russian, and English translations.
- `tests/` — database and core-rule tests.

## 6. Telegram limitations and security notes

- The Telegram Bot API does not let a bot enumerate all existing chat members. The bot can act only on user IDs it learned from `chat_member` updates after the chat was registered; it cannot automatically remove every pre-existing member. To include earlier members, their user IDs must be supplied separately or managed through an explicit list.
- To remove members, the bot must be an admin with `can_restrict_members`. Publishing to a channel requires `can_post_messages`; creating invite links requires `can_invite_users`.
- A recipient can forward a one-person Telegram invite link to someone else. `member_limit=1` limits the first successful join, not who uses the link, so send it privately to the intended participant.
- A private-quest bot deep link is a shareable quest invitation and is separate from a group/channel invite link.
- Production configuration uses `DATABASE_URL` and PostgreSQL. Back up the PostgreSQL database regularly. Exports omit private quest tokens and secret chat invite URLs.
- `deploy.sh` validates the PostgreSQL connection and `pg_dump` client, initializes missing tables, seeds configured superadmins, and installs/enables a systemd service with automatic restart. Run it as the deployment user with sudo privileges, not as root.
- The bot sends localized startup and graceful-shutdown notices to configured superadmins. During graceful shutdown it creates a full PostgreSQL custom-format dump and sends it as a streamed document; upload and systemd stop timeouts are configurable in `.env`.
- Existing SQLite databases are not automatically migrated to PostgreSQL; any required data transfer must be planned separately.
- A hard kill, power loss, or `SIGKILL` does not run shutdown backup hooks; schedule external/periodic PostgreSQL backups for disaster recovery.

## 7. Out of scope for the first release

- Web admin panel, advertising, and accepting answers publicly in a group.
- Automatically enumerating all channel/group members, which the Telegram API does not support.
- Automatically translating quest content into each participant's selected language.
- A global superadmin ban interface. The implemented participant block applies to an individual quest.
