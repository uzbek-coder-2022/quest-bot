# Quest Bot

A Telegram quest bot built with aiogram 3. The interface is available in Uzbek, «Yangi o‘zbek» (the same Uzbek text written with ö, ğ, ş, and ç), Karakalpak, Russian, and English. There is no web admin panel or advertising.

## Features

- Only admins and superadmins can create quests.
- Configure quest title and description, public/private visibility, optional cover photo, start time, stages, and the overall time. A new quest must start at least 10 minutes after it is created. The overall time is entered either as minutes (0 = no limit) or as an explicit end time such as `2026-10-05 20:00`, and an admin can change it later from **Edit quest details → Change duration** while the quest is scheduled or running.
- Every button list is paginated (quests, participants, joined quests, administrators, support quests and tickets, chats, whitelists) and shows 10 records per page by default; a superadmin changes the page size in **Settings**.
- Choose during creation whether every stage shares a single attempt limit (asked once) or each stage has its own limit (asked per stage).
- Set a question, attempt limit, time limit, and automatic or admin review for each stage.
- A started quest sends a start notice instead of pushing the first question: the participant presses **Start quest** in the quest card, below the rating button, and later stages arrive through the same **Continue** button.
- Change a scheduled quest's start time from **Edit quest details**; the scheduled stage times shift with it. A quest that has already started keeps its start time.
- Delete a quest softly: only the delete flag is set and nothing is removed from the database. The admin sees a short confirmation and a short success notice (the quest is hidden and a superadmin can restore it) without technical details about the flag or the database. The quest's own admin or a superadmin can do it, only superadmins can see that the record still exists, and they can restore it or erase it permanently after a confirmation.
- Send a message to a participant from the participant list, where one button identifies the participant and the next one composes the message.
- Choose immediate progression after a correct answer or scheduled stage releases.
- Browse public quests and view all quests a user has joined, including private quests. Joining always requires a separate confirmation after a preview of the description, stage count, start time, cover photo (when present), and participant count. New cover photos appear inside the Rich Message preview with its inline buttons.
- Join any quest that has not finished yet: a scheduled or running quest still accepts participants even after the participants who joined earlier have finished or failed, because a playthrough no longer closes the quest, and a late joiner starts from the question that is currently due. The quest card shows the end time and repeats that the quest is still open. The confirmation says the participant will be notified when the quest starts and that questions arrive only after pressing **Start**.
- View aggregate public-quest leaderboards for the current Tashkent calendar week, month, and year. Correctly solved stages earn one point; completed public quests break ties. Ranks 1–5 carry 🥇🥈🥉🏅🎖 medals in both quest and aggregate ratings. User-facing rating screens end with a **Main menu** button; the Administrator panel button appears only on ratings opened from the admin panel.
- A leaderboard remembers where it was opened from: **Back** returns to the quest card or to the ratings list, and the quest rating a user opens needs no separate **Open quest** button because **Back** already performs that jump there. Notices that end a playthrough (exhausted attempts, stage timeout, quest ended or completed) include the **Open quest** button.
- Every **Back** button returns to the exact previous screen instead of skipping a section: a quest card remembers the browse list with its filter and page, the quest-management card remembers the admin list it was opened from, administrators/chats detail pages remember their list page, sub-screens walk up one step at a time, ticket history goes back to the tickets list, and the language screen goes back to the main menu.
- Edit question text/media and automatic answers before release. In **Edit questions**, admins can also add a fully configured stage or remove an unreleased stage; deletions require confirmation, preserve the final stage, compact numbering, and keep the stage count synchronized. Active quests allow appending only while the final stage is unreleased and removing only an unreleased stage plus its unreleased suffix. The 30-stage limit still applies.
- Archive text, photo, and video questions plus optional quest cover photos in a private Telegram channel. New or re-saved question media stores its type and reusable Telegram file ID so the title, stage/attempt details, question, and answer instruction fit into one Rich Message; no media files are downloaded. Existing stages keep the archive-message fallback until their questions are re-saved.
- Invite users to private quests through a bot deep link.
- Publish questions to a Telegram group or channel; participants always submit answers privately to the bot. The published question carries an **Answer privately** button and a **Main menu** button directly below it.
- Pause and resume scheduled or active quests. While paused, the scheduler does not advance that quest; quest schedules, overall deadlines, and open stage timers all shift by the pause duration.
- Block a participant from a particular quest, optionally with a reason and a notification.
- Superadmin panel for admin management, all quests, archiving, user totals/new and active counts, language distribution, quest participation/completion metrics, audit logs, chat settings, pagination, and ZIP export.
- Support replies identify whether they came from a specific quest administrator or a superadmin; sent acknowledgements do not show an unnecessary reply button, while admin notifications retain their reply action.
- Every message an admin sends opens a numbered conversation: participant messages, admin messages, and support requests are tagged with a searchable hashtag such as `#T12`, and each one has a **Close** button. Closing ends the conversation for both sides, stops further replies, and is written to the audit log. Answering a message removes that message's Reply button so it cannot be answered twice.
- Private support conversations with superadmins or the admins of quests a user joined.
- Read the guide from `/help` or the **Guide** button; the guide screen ends with a **Main menu** button.

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

Create a dedicated **private Telegram channel** for question archives, add the bot as an administrator with permission to post messages, and set its numeric ID in `QUESTION_ARCHIVE_CHANNEL_ID`. The bot checks this configuration at startup. Questions and optional quest cover photos are copied into the channel when created or replaced. The database stores question text/captions, answer metadata, archive chat/message references, and the type plus reusable Telegram file ID for new or re-saved question media; it does not download media files. New and re-saved text/photo/video questions are delivered in one Rich Message containing stage details and the localized answer instruction. Legacy stages whose media type is unknown retain the archive-message copy flow until an admin re-saves the question. New cover photos are embedded in the quest Rich Message preview, with inline buttons attached to that same message. Existing covers without a stored file ID continue to use an archive-message copy until an admin replaces the cover; when a user opens quest details, that separate image is sent before a fresh Rich Message preview.

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

Questions are published to the group/channel, but answers are sent privately to the bot. In scheduled mode, each stage is published at its configured time. In immediate mode, only stage one is published to the group; subsequent stages are sent privately to each participant so their answers do not reveal future questions. Participant deliveries combine the quest/stage title, attempts/time limit, question and localized answer instruction in one Rich Message for new or re-saved questions; legacy media remains copied from the archive.

> **Member-removal limitation:** Telegram bots cannot retrieve a complete list of existing chat members. The bot can check only members it has tracked from `chat_member` updates since the chat was registered. The bot must be an administrator and receive these updates. If you need to manage existing members, provide their user IDs separately.

## Admin guide

- Superadmins manage administrators from **Manage admins**: every row opens a detail page with the admin's quest count, a **Message** button that sends them a direct message through the bot, and a quest list that includes deleted quests (marked 🗑) with full management access, so a deleted quest can be restored right there.
- Superadmins and admins can open `/admin` or use the **Admin panel** menu in the bot's private chat. The command menu shows `/admin` only to registered bot admins in their private chats; the handler also checks the user's role.
- To add an admin, a superadmin selects **Manage admins → Add** and sends the person's numeric Telegram ID. The command menu is updated immediately. The new admin must open the bot with `/start` before creating quests or receiving bot messages.
- Create a quest by following the **Create quest** wizard.
- An admin can manage leaderboards, participants, and pending manual reviews for their own quests. Superadmins can manage every quest. Participant lists are paginated in database-backed pages of 20, and individual quest leaderboards show each successful finisher's completion time.
- Open **Edit questions** from a scheduled or active quest to update an unreleased stage's text/media and (for automatic-answer stages) its correct answer. Use **Add stage** to run the full setup (question/media, answer mode, correct answer for automatic checking, attempts, and time limit). Scheduled quests also ask for a delivery time later than the prior stage and no later than the quest deadline. The question is copied into the archive only after the setup is saved.
- From **Edit quest details** a scheduled quest's start time can be moved; the scheduled stage times shift by the same difference. New quests must start at least 10 minutes after creation.
- The quest management view offers **Delete quest** with a confirmation step. Deletion only sets the delete flag: stages, questions, participations, and answers stay in the database. Only superadmins can see such a record: they find deleted quests in **Admin panel → Quests I manage → Deleted**, may open one, restore it with one button, or use **Delete permanently** to erase the quest and all of its data after a confirmation (archived Telegram copies are removed too). The owning admin can delete a quest but never sees it again.
- Participant lists show one button per participant and a second button that messages that participant; quest administrators can use it for their own quests, superadmins for any quest.
- Use **Remove stage** to start a confirmation flow. The last remaining stage cannot be removed; later stage numbers and the total count are updated automatically. In an active quest, a stage can be removed only if it and every later stage are still unreleased. Stages that have been delivered to a participant or announced in a quest chat remain locked. An active quest can accept a new stage only while its current final stage is unreleased. The existing limit of 30 stages applies.
- The public **Leaderboards** menu provides current-week, current-month, and current-year aggregate rankings across public quests, alongside the existing individual-quest ratings. Period boundaries use `Asia/Tashkent` (Monday week start, local month/year start). Ranks 1–5 show their medals, and every leaderboard offers **Open quest** next to **Back**.
- The audit log in the superadmin panel opens as a terminal-style monospaced block (timestamp, action, entity, actor) with a **Refresh** button.
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
- If a participant has several unfinished quests, the answer is applied to the question that was delivered most recently, so a plain text message is never sent back to the chat unanswered. When the text could equally be a support message, the bot asks whether it is the quest answer or a support message (the quest's own admin can receive participant messages from the participant list).

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

The tests cover SQLite quest flows, PostgreSQL SQL/schema compatibility, private-quest consent and viewing, archived cover/question media references, single-Rich-Message stage delivery with legacy fallbacks, database-backed pagination for participants, administrators, support quests and tickets, chats, and whitelists (10 per page by default, configurable), the answer-button main-menu row, user-facing rating buttons, the guide main-menu row, numbered conversations with searchable `#T` tags and Close buttons for participant/admins messages and support tickets (including refusals on closed threads), back targets for every section (browse filters and pages, admin quest lists, administrators/chats pages, tickets, the language screen), quest duration/end-time entry and re-timing (minutes or an explicit end time), completion only after the overall time runs out (late joining into a running quest), quest-level rating Back targets, leaderboard completion times, pause/resume clock shifting, ratings, statistics, support attribution, answer attempts, manual review, role settings, the five interface languages (including the four-letter «Yangi o‘zbek» substitution, which keeps format placeholders such as `{chat_id}` intact, and the full Karakalpak dictionary), soft deletion with restore and permanent deletion, start-time shifting, question delivery gating, late joining until a quest finishes, the shared/per-stage attempt policy, leaderboard medals and back targets, the terminal audit log, admin overview screens, the 10-minute creation lead time, and the start/continue button, plus related rules. PostgreSQL integration should also be verified against the server configured in `.env` before launch.

## Security checklist

- Keep `.env` out of Git. `deploy.sh` sets its mode to `600`; never log the bot token or database password.
- Use a dedicated PostgreSQL role with access only to the quest-bot database.
- Admins should open a private chat with the bot and send `/start`; otherwise, the bot cannot deliver questions, review notices, or support messages to them.
- Use the bot's private chat for quest creation, admin actions, support, and answer submissions.
- Private-quest deep links can be shared. One-person group/channel invite links can also be forwarded; the first person to use a link may consume its one-person limit.
