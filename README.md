# Quest Bot

A Telegram quest bot built with aiogram 3. The interface is available in Uzbek, Russian, and English. There is no web admin panel or advertising.

## Features

- Only admins and superadmins can create quests.
- Configure quest title and description, public/private visibility, optional cover photo, start time, stages, and overall duration.
- Set a question, attempt limit, time limit, and automatic or admin review for each stage.
- Choose immediate progression after a correct answer or scheduled stage releases.
- Browse public quests and view all quests a user has joined, including private quests. Joining always requires a separate confirmation after a preview of the description, stage count, start time, cover photo (when present), and participant count. New cover photos appear inside the Rich Message preview with its inline buttons.
- View aggregate public-quest leaderboards for the current Tashkent calendar week, month, and year. Correctly solved stages earn one point; completed public quests break ties.
- Edit question text/media and automatic answers before release. In **Edit questions**, admins can also add a fully configured stage or remove an unreleased stage; deletions require confirmation, preserve the final stage, compact numbering, and keep the stage count synchronized. Active quests allow appending only while the final stage is unreleased and removing only an unreleased stage plus its unreleased suffix. The 30-stage limit still applies.
- Archive text, photo, and video questions plus optional quest cover photos in a private Telegram channel, then deliver them with Telegram message copies; no media files are downloaded or stored in the database.
- Invite users to private quests through a bot deep link.
- Publish questions to a Telegram group or channel; participants always submit answers privately to the bot.
- Pause and resume scheduled or active quests. While paused, the scheduler does not advance that quest; quest schedules, overall deadlines, and open stage timers all shift by the pause duration.
- Block a participant from a particular quest, optionally with a reason and a notification.
- Superadmin panel for admin management, all quests, archiving, user totals/new and active counts, language distribution, quest participation/completion metrics, audit logs, chat settings, pagination, and ZIP export.
- Support replies identify whether they came from a specific quest administrator or a superadmin; sent acknowledgements do not show an unnecessary reply button, while admin notifications retain their reply action.
- Private support conversations with superadmins or the admins of quests a user joined.

See [`plan.md`](plan.md) for the complete requirements, behavior, and Telegram API limitations.

## Production deployment: PostgreSQL and systemd

`deploy.sh` installs the Python dependencies, validates and initializes PostgreSQL, creates or updates a systemd unit, and enables the bot to run continuously and restart after a failure or server reboot. It is intended for a Linux server running systemd.

### 1. Install server prerequisites

The deployment script requires Linux with systemd, `sudo`, Python 3.11 or newer, Python virtual-environment and pip support, and PostgreSQL client utilities including `pg_dump`. Install those prerequisites with your distribution's package manager. Package names and commands vary by distribution, so confirm the Linux distribution and version before following distribution-specific installation steps.

If PostgreSQL runs on this server, install its server package too. Use a PostgreSQL client version that is the same as or newer than the server version. The deployment account needs sudo access for systemd; do **not** run `deploy.sh` with `sudo`.

### 2. Create a PostgreSQL database and role

For a PostgreSQL server on the same machine, create a dedicated role and database (choose a strong password):

```bash
sudo -u postgres createuser --pwprompt quest_bot
sudo -u postgres createdb --owner=quest_bot quest_bot
```

For managed or remote PostgreSQL, create the database and role with your provider. The role must be allowed to connect and create tables, indexes, and sequences in the target schema. Make sure the server accepts connections from the bot host.

### 3. Configure `.env`

From the repository directory, copy the example and edit it:

```bash
cp .env.example .env
nano .env
```

Set your BotFather token, at least one numeric superadmin Telegram ID, the private question archive channel ID, and the PostgreSQL URL:

```dotenv
BOT_TOKEN=123456:replace-with-real-token
SUPERADMIN_IDS=123456789
QUESTION_ARCHIVE_CHANNEL_ID=-1001234567890
DATABASE_URL=postgresql://quest_bot:your-password@127.0.0.1:5432/quest_bot
DATABASE_POOL_MIN_SIZE=1
DATABASE_POOL_MAX_SIZE=10
SCHEDULER_INTERVAL_SECONDS=10
BACKUP_UPLOAD_TIMEOUT_SECONDS=900
SERVICE_STOP_TIMEOUT_SECONDS=1800
```

URL-encode reserved characters in the database username or password. For example, encode `@` as `%40`. For a hosted PostgreSQL provider, include its required SSL query parameters (commonly `?sslmode=require`). `DATABASE_URL` is required by `deploy.sh`; the application also supports a local SQLite fallback for development and tests by leaving `DATABASE_URL` empty and setting `DATABASE_PATH=data/quest_bot.sqlite3`.

Create a dedicated **private Telegram channel** for question archives, add the bot as an administrator with permission to post messages, and set its numeric ID in `QUESTION_ARCHIVE_CHANNEL_ID`. The bot checks this configuration at startup. Questions and optional quest cover photos are copied into the channel when created. The database stores text, answer metadata, archive chat/message references, and reusable Telegram file IDs for new cover photos; it does not download or store media files. New cover photos are embedded in the quest Rich Message preview, with inline buttons attached to that same message. Existing covers without a stored file ID continue to use an archive-message copy until an admin replaces the cover; when a user opens quest details, that separate image is sent before a fresh Rich Message preview.

`BACKUP_UPLOAD_TIMEOUT_SECONDS` controls how long the bot waits while sending a backup; `SERVICE_STOP_TIMEOUT_SECONDS` controls how long systemd allows graceful shutdown and backup delivery. The bot keeps aiogram's default `https://api.telegram.org` base URL. Backup files are streamed from disk rather than loaded into memory.

### 4. Install and start the service

Run this as the normal Linux account that owns the checkout and has sudo privileges:

```bash
./deploy.sh
```

The script creates `.venv`, installs `requirements.txt`, sets `.env` permissions to owner-only, checks PostgreSQL and `pg_dump`, creates the tables and configured superadmin records, then installs and starts `quest-bot.service`. The unit's working directory is the repository, so the application loads `.env` at startup; secrets are not copied into the systemd unit. The service runs as the deployment account, starts automatically on reboot, and restarts if the process exits unexpectedly. It notifies configured superadmins after startup. On a graceful stop or restart, it notifies them, creates a full PostgreSQL custom-format dump, and sends the backup file to each superadmin.

Backup sending uses the default Bot API endpoint and a streamed file upload. Each superadmin must have opened the bot with `/start` to receive notices and backup files. A forced kill, power loss, or `SIGKILL` cannot run shutdown backup hooks.

Useful service commands:

```bash
sudo systemctl status quest-bot
sudo journalctl -u quest-bot -f
sudo systemctl restart quest-bot
./stop.sh
```

`./stop.sh` stops the service immediately but leaves it enabled for the next reboot. To disable automatic startup too, run `sudo systemctl disable --now quest-bot`. To use a different unit name, run `SERVICE_NAME=my-quest-bot ./deploy.sh` and later `SERVICE_NAME=my-quest-bot ./stop.sh` (or pass the name as an argument: `./stop.sh my-quest-bot`). After updating the checkout or changing dependencies, run `./deploy.sh` again to reinstall and restart the service.

The application writes runtime and incoming-update records to `/var/log/quest-bot/log` and errors with tracebacks to `/var/log/quest-bot/log_err`. Both files rotate at 5 MB with five backups and are readable only by the service account. Logs are also available in `journalctl`. Incoming-update logs contain identifiers and update types, not message contents. Unhandled update, scheduler, and process errors are sent to configured superadmins; identical error alerts are throttled to one per minute while every occurrence remains in `log_err`. The affected user receives a localized generic error notice in private chat. Expected outcomes such as a wrong quest answer are not reported as software errors. Superadmins must have opened the bot with `/start` to receive alerts.

### 5. Database operations

Back up PostgreSQL regularly. For a local database, a basic backup command is:

```bash
sudo -u postgres pg_dump quest_bot > quest_bot-$(date +%F).sql
```

Keep manual backups outside the repository and test restoring them. The shutdown attachment uses PostgreSQL's custom format; restore it into an existing empty database with:

```bash
pg_restore --no-owner --no-acl --dbname=quest_bot quest-bot-database-backup-<timestamp>.dump
```

The bot initializes missing tables on startup; it does not automatically import data from a previous SQLite database.

## Connect a group or channel

1. Add the bot to the Telegram group or channel and make it an administrator.
2. For a channel, grant permission to post (`can_post_messages`). For cleanup, grant permission to restrict members (`can_restrict_members`). For invite links, grant permission to invite users (`can_invite_users`).
3. Send `/chatid` in the group or channel to get its chat ID.
4. As a superadmin, open **Admin panel → Settings → Groups/channels → Add** in the bot and register that chat ID.
5. When creating a quest, select the registered chat or choose **Bot only**.
6. If members should be removed when a quest starts, enable cleanup in the superadmin chat settings and add exempt user IDs to the whitelist.

Questions are published to the group/channel, but answers are sent privately to the bot. In scheduled mode, each stage is published at its configured time. In immediate mode, only stage one is published to the group; subsequent stages are sent privately to each participant so their answers do not reveal future questions.

> **Member-removal limitation:** Telegram bots cannot retrieve a complete list of existing chat members. The bot can check only members it has tracked from `chat_member` updates since the chat was registered. The bot must be an administrator and receive these updates. If you need to manage existing members, provide their user IDs separately.

## Admin guide

- Superadmins and admins can open `/admin` or use the **Admin panel** menu in the bot's private chat. The command menu shows `/admin` only to registered bot admins in their private chats; the handler also checks the user's role.
- To add an admin, a superadmin selects **Manage admins → Add** and sends the person's numeric Telegram ID. The command menu is updated immediately. The new admin must open the bot with `/start` before creating quests or receiving bot messages.
- Create a quest by following the **Create quest** wizard.
- An admin can manage leaderboards, participants, and pending manual reviews for their own quests. Superadmins can manage every quest.
- Open **Edit questions** from a scheduled or active quest to update an unreleased stage's text/media and (for automatic-answer stages) its correct answer. Use **Add stage** to run the full setup (question/media, answer mode, correct answer for automatic checking, attempts, and time limit). Scheduled quests also ask for a delivery time later than the prior stage and no later than the quest deadline. The question is copied into the archive only after the setup is saved.
- Use **Remove stage** to start a confirmation flow. The last remaining stage cannot be removed; later stage numbers and the total count are updated automatically. In an active quest, a stage can be removed only if it and every later stage are still unreleased. Stages that have been delivered to a participant or announced in a quest chat remain locked. An active quest can accept a new stage only while its current final stage is unreleased. The existing limit of 30 stages applies.
- The public **Leaderboards** menu provides current-week, current-month, and current-year aggregate rankings across public quests, alongside the existing individual-quest ratings. Period boundaries use `Asia/Tashkent` (Monday week start, local month/year start).
- Only superadmins can archive quests.
- Before a user joins any quest, the bot shows a confirmation screen with the title, description, start time, stage count, participant count, and optional cover photo. After creating a private quest, the bot provides its join deep link. To retrieve it again, use **Get quest invite link** in the quest management view.
- Users can open **My joined quests** (or `/myquests`) to revisit quests they participate in, including private quests.
- Pause/resume is available in a quest's management view. Resuming shifts scheduled starts, the overall deadline, and active participant stage timers by the time spent paused. Admin subflows provide a direct return to the admin panel.
- A participant in a quest attached to a chat receives an individual one-person invite link: scheduled-quest participants receive it after start-time cleanup, while participants joining an active quest receive it immediately. The **One-person chat invite** button can retrieve it again. The link is created with `member_limit=1` and no expiration. One stored link is reused for each participant.

## Time and answer rules

- Quest times are entered in the `Asia/Tashkent` timezone and stored in UTC.
- Date format: `YYYY-MM-DD HH:MM`, for example `2026-10-03 18:30`.
- Overall and stage time limits are entered in minutes; `0` means no limit.
- Automatic answer checking requires an exact match after Unicode NFC normalization and trimming leading/trailing whitespace. Letter case, punctuation, and internal whitespace matter.
- If a participant exhausts their attempts or a stage time limit expires, they are marked `failed` for that quest.
- If a participant has open questions in multiple quests, the bot asks them to select the quest before submitting an answer.

## Commands

| Command | Purpose | Shown in command menu to |
|---|---|---|
| `/start` | Open the bot or join through a quest invitation link | Everyone |
| `/menu` | Open the main menu | Everyone |
| `/quests` | Browse public quests | Everyone |
| `/myquests` | Show quests you have joined | Everyone |
| `/admin` | Open the admin panel | Registered bot admins in private chats |
| `/support` | Contact a superadmin or quest admin | Everyone |
| `/language` | Change the interface language | Everyone |
| `/help` | Show usage help | Everyone |
| `/cancel` | Cancel the current wizard or action | Everyone |
| `/chatid` | Show the current group or channel ID | Telegram group admins and registered bot admins in private chats |

Command-menu visibility is a Telegram UI convenience, not an authorization boundary. Admin actions still enforce bot roles in their handlers.

## Tests

Run the test suite after installing the requirements:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

The tests cover SQLite quest flows, PostgreSQL SQL/schema compatibility, private-quest consent and viewing, archived cover references, pause/resume clock shifting, ratings, statistics, support attribution, answer attempts, manual review, role settings, and related rules. PostgreSQL integration should also be verified against the server configured in `.env` before launch.

## Security checklist

- Keep `.env` out of Git. `deploy.sh` sets its mode to `600`; never log the bot token or database password.
- Use a dedicated PostgreSQL role with access only to the quest-bot database.
- Admins should open a private chat with the bot and send `/start`; otherwise, the bot cannot deliver questions, review notices, or support messages to them.
- Use the bot's private chat for quest creation, admin actions, support, and answer submissions.
- Private-quest deep links can be shared. One-person group/channel invite links can also be forwarded; the first person to use a link may consume its one-person limit.
