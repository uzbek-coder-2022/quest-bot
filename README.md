# Quest Bot

A Telegram quest bot built with aiogram 3. The interface is available in Uzbek, Russian, and English. There is no web admin panel or advertising.

## Features

- Only admins and superadmins can create quests.
- Configure quest title and description, public/private visibility, start time, stages, and overall duration.
- Set a question, attempt limit, time limit, and automatic or admin review for each stage.
- Choose immediate progression after a correct answer or scheduled stage releases.
- Browse and join public quests, filter by status, and view leaderboards.
- Invite users to private quests through a bot deep link.
- Publish questions to a Telegram group or channel; participants always submit answers privately to the bot.
- Block a participant from a particular quest, optionally with a reason and a notification.
- Superadmin panel for admin management, all quests, archiving, statistics, audit logs, chat settings, pagination, and ZIP export.
- Private support conversations with superadmins or the admins of quests a user joined.

See [`plan.md`](plan.md) for the complete requirements, behavior, and Telegram API limitations.

## Requirements

- Python 3.11 or newer
- A Telegram bot token from BotFather
- The numeric Telegram user ID of at least one superadmin

## Install and run

```bash
python -m venv .venv
source .venv/bin/activate      # Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env
```

Set the following values in `.env`:

```dotenv
BOT_TOKEN=123456:replace-with-real-token
SUPERADMIN_IDS=123456789
DATABASE_PATH=data/quest_bot.sqlite3
SCHEDULER_INTERVAL_SECONDS=10
```

For multiple superadmins, separate IDs with commas:

```dotenv
SUPERADMIN_IDS=123456789,987654321
```

Start the bot:

```bash
python main.py
```

Each superadmin must open the bot and send `/start` at least once. The SQLite database is stored at `DATABASE_PATH`; in production, this path must be on persistent storage.

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

- Superadmins and admins can open `/admin` or use the **Admin panel** menu in the bot's private chat.
- To add an admin, a superadmin selects **Manage admins → Add** and sends the person's numeric Telegram ID. The new admin must open the bot with `/start` before creating quests or receiving bot messages.
- Create a quest by following the **Create quest** wizard.
- An admin can manage leaderboards, participants, and pending manual reviews for their own quests. Superadmins can manage every quest.
- Only superadmins can archive quests.
- After creating a private quest, the bot provides its join deep link. To retrieve it again, use **Get quest invite link** in the quest management view.
- A participant in a quest attached to a chat receives an individual one-person invite link: scheduled-quest participants receive it after start-time cleanup, while participants joining an active quest receive it immediately. The **One-person chat invite** button can retrieve it again. The link is created with `member_limit=1` and no expiration. One stored link is reused for each participant.

## Time and answer rules

- Quest times are entered in the `Asia/Tashkent` timezone and stored in UTC.
- Date format: `YYYY-MM-DD HH:MM`, for example `2026-10-03 18:30`.
- Overall and stage time limits are entered in minutes; `0` means no limit.
- Automatic answer checking requires an exact match after Unicode NFC normalization and trimming leading/trailing whitespace. Letter case, punctuation, and internal whitespace matter.
- If a participant exhausts their attempts or a stage time limit expires, they are marked `failed` for that quest.
- If a participant has open questions in multiple quests, the bot asks them to select the quest before submitting an answer.

## Commands

| Command | Purpose |
|---|---|
| `/start` | Open the bot or join through a quest invitation link |
| `/menu` | Open the main menu |
| `/quests` | Browse public quests |
| `/admin` | Open the admin panel |
| `/support` | Contact a superadmin or quest admin |
| `/language` | Change the interface language |
| `/help` | Show usage help |
| `/cancel` | Cancel the current wizard or action |
| `/chatid` | Show the current group or channel ID |

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

The tests cover SQLite quest flows, private-quest tokens, answer attempts, manual review, role settings, and related rules.

## Security checklist before launch

- Keep `.env` out of Git and never log the bot token.
- Back up `data/quest_bot.sqlite3` regularly.
- Admins should open a private chat with the bot and send `/start`; otherwise, the bot cannot deliver questions, review notices, or support messages to them.
- Use the bot's private chat for quest creation, admin actions, support, and answer submissions.
- Private-quest deep links can be shared. One-person group/channel invite links can also be forwarded; the first person to use a link may consume its one-person limit.
