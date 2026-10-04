# Quest Bot — Project Plan

## 1. Goal and scope

Build a Telegram bot with aiogram 3 for creating and running quests and managing participants. The user interface supports Uzbek, «Yangi o‘zbek» (the same Uzbek interface with the letters ö, ğ, ş, and ç; format placeholders are never rewritten), Karakalpak, Russian, and English. There will be no web admin panel and no advertising.

The first release runs entirely inside Telegram. Production deployments use PostgreSQL through `asyncpg`; SQLite remains available for local development and unit tests. Quest titles, descriptions, and questions are displayed in the language entered by the quest author; changing the interface language does not automatically translate quest content.

## 2. Functional components

### A. Participant experience
- `/start`, the main menu, `/help`, and language selection.
- Browse public quests by status: scheduled, active, completed, or archived; revisit joined quests, including private quests, from a personal quest list.
- Every button list is paginated: quests, participants, joined quests, administrators, support quests and tickets, managed chats, and whitelists show **10 records per page by default**. A superadmin changes the page size (5, 10, 20, or 50) in **Settings**, and the new size applies to all of these lists.
- Join public quests in the bot and private quests through a bot deep link. Joining always requires explicit confirmation after a preview showing title, description, stage count, start time, optional cover photo, and participant count. The preview also shows the overall time with the quest's end time (when one is set) and, while the quest is still joinable, a notice that participation is open.
- Submit answers in a private chat with the bot. A started quest does not push its first question: it sends a start notice, and the question arrives after the participant presses **Start quest** in the quest card, below the rating button. Later stages use the same **Continue** button, including in stage-ready notices.
- When several unfinished quests have delivered a question, the most recently delivered question is used as the answer target instead of asking which quest the message belongs to. If the text could also be a support message, the bot asks whether it is an answer or a support message.
- Joining stays open for every quest that has not finished yet: a public quest that is scheduled or running accepts new participants at any time, and a late joiner starts from the question that is currently due. Only a finished (completed) or archived quest rejects new participants.
- The join confirmation states that the participant will be notified when the quest starts and that questions arrive on demand, never automatically.
- Preserve each quest's leaderboard, ordered by correctly solved stages, completion time, and then join time; show the completion time for each participant who successfully completed the quest. Ranks 1–5 are prefixed with 🥇🥈🥉🏅🎖.
- Aggregate rankings across public quests for the current calendar week, month, and year in `Asia/Tashkent`. Each correctly solved stage earns one point; break ties by the number of public quests completed in that period. The week begins Monday; months and years begin on their first calendar day. Aggregate rows use the same 🥇🥈🥉🏅🎖 medals.
- A leaderboard opened from a quest card returns to that card with **Back**, and a leaderboard opened from the ratings list returns to that list; the user-facing quest rating therefore needs no extra **Open quest** row, and **Back** performs that jump itself. Participation notices that end a playthrough (exhausted attempts, stage timeout, quest ended, quest completed) carry an **Open quest** button.
- Every **Back** button returns to the screen the user actually came from and never skips a section. Lists remember their filter and page, so Back from a quest card returns to that exact browse page, Back from the admin quest card returns to the admin list (all quests, an administrator's quest list or the deleted list) it was opened from, and Back from an administrator's detail page or a chat page returns to the administrators/chats page it was opened from. Sub-screens of the quest-management card (participants, question editing, details, cover) walk up one step to that card and only then to the original list. The tickets list returns to the support screen it was opened from, a ticket history offers a Back to the tickets list page, and the language screen offers a Back to the main menu. Breadcrumbs live in memory and fall back to the previous default when they are missing.
- User-facing screens never show the Administrator panel button: the public ratings section, the aggregate leaderboards, and every quest rating opened by a user end with the **Main menu** button, and managed-quest ratings stay inside the admin panel.
- The guide/instructions screen (the `/help` command and the **Guide** button) ends with an explicit **Main menu** row below its usual buttons, so a user always has a way back to the main menu.

### B. Quest creation and participation
- Quest fields: title, description, public/private visibility, optional cover photo, start time, overall duration, number of stages, and the chat in which questions are published. Copy cover photos into the configured private archive channel and retain archive chat/message references plus a reusable Telegram file ID for Rich Message previews; do not download media files. Embed new covers in the quest Rich Message with inline controls attached to the same message. Existing covers without a stored file ID retain the archive-message copy fallback until replaced; in user-side quest details, send this image before a fresh Rich Message preview.
- Creation asks whether one attempt limit applies to every stage (entered once) or every stage has its own limit (asked per stage). Each stage has a text, photo, or video question, automatic or manual answer checking, an attempt limit, and an optional time limit. Text/media questions are copied into a dedicated private Telegram archive channel; the database stores the question text/caption, answer metadata, Telegram archive chat/message references, and the media type plus reusable Telegram file ID for new or re-saved questions, never media files. New/re-saved questions are delivered as one Rich Message containing the quest title, stage and attempt/time details, question content, and localized answer-submission instruction. Legacy stages with unknown media retain the archive-copy fallback until re-saved.
- A new quest's start time must be at least 10 minutes after the moment of creation; earlier times are rejected with a localized warning.
- A scheduled quest's start time can be changed from **Edit quest details**; every scheduled stage time shifts by the same difference. The start time of a quest that already started cannot be moved.
- Questions and their media can be edited before the quest starts. Once active, only stages not yet delivered to participants or announced in a quest chat can be edited. Delivered or announced stages remain unchanged. Automatic-answer stages also allow the correct answer to be corrected while the stage remains unreleased.
- From the question-editing flow, admins can append a fully configured stage (question/media, answer mode, automatic correct answer when applicable, attempts, time limit, and scheduled delivery time when applicable) or remove a stage after explicit confirmation. New questions are copied to the archive only when the stage is saved. The 30-stage limit applies. The final remaining stage cannot be removed; deleting a stage compacts later stage numbers and keeps the count synchronized. In an active quest, append only while its existing final stage is unreleased, and remove only when the selected stage and its entire later suffix are unreleased. Scheduled delivery must be strictly after the prior stage and within the quest's overall deadline. Paused schedules continue to shift with the quest when it resumes.
- Stage progression modes:
  - **Immediate:** after a correct answer, the next question is sent privately to that participant.
  - **Scheduled:** each stage is announced at its configured time and delivered to participants.
- Automatic checking uses exact matching after Unicode NFC normalization and trimming leading/trailing whitespace. Letter case, punctuation, and internal whitespace remain significant.
- Answers requiring manual review are sent with review controls to the quest owner and superadmins.
- When a participant exhausts the attempt limit or a stage time limit, that participant is marked `failed` for the quest.
- A quest ends when its overall time runs out or an admin finishes it. Participants reaching a terminal status never close the quest on their own: a quest with no overall limit stays open until an admin finishes it, and a running quest keeps accepting new participants even after everyone who joined earlier has finished or failed. The scheduler only auto-completes a quest whose `duration_seconds` is greater than zero and whose start time plus that duration is already in the past.
- An authorized quest admin can pause or resume a scheduled or active quest. While paused, the scheduler must not start quests, release stages, expire participants, or complete the quest. On resume, shift the quest start/deadline, all stage schedules, and open participant-stage timers by the paused duration. Joining, answering, and manual review are held while paused.

### C. Admins and superadmins
- Superadmins are configured by Telegram user ID in `.env`; each superadmin must open the bot with `/start`.
- `QUESTION_ARCHIVE_CHANNEL_ID` is configured in `.env`. The bot must be an administrator with posting permission in that private channel.
- A superadmin can add or remove admins by numeric Telegram user ID.
- An admin can manage only quests they created; superadmins can view and manage every quest.
- Quest management includes leaderboards, database-paginated participant lists (20 per page), manually reviewed answers, pausing/resuming, question editing, stage addition/removal, and finishing a quest. Admin subflows provide navigation back to the admin panel.
- An admin can block a participant from a specific quest, optionally provide a reason, and notify the participant. The block can also be reversed.
- An admin can send a message to a participant of a quest they manage. In the participant list the first button identifies the participant and the second sends that message.
- Quests are deleted softly: the owning admin or a superadmin can mark a quest deleted, which only sets the delete flag and hides the quest from listings while every row (stages, questions, participations, and answers) stays in the database. The dialog stays in plain language for the admin: the confirmation asks whether to delete the quest, and the success notice only says that it was deleted and that a superadmin can restore it — internal details such as the `delete = true` flag and the database are never shown. A deleted record is visible to superadmins only: other admins cannot see it in any list and cannot open its card.
- Superadmins list deleted quests, restore them, or erase one for good with **Delete permanently** after a confirmation. Permanent deletion removes the quest row and everything attached to it (stages, questions, participations, answers, chat invites, announcement and notice markers) and best-effort deletes its archived Telegram copies; the action is written to the audit log.
- Only superadmins can archive and unarchive quests.
- The superadmin panel includes admin management, all quests, audit logs, pagination settings, managed group/channels, and ZIP data export. Statistics include total users, new users in the last 30 days, 7-/30-day active users, language distribution, quest counts, participation counts, and completion metrics.
- Admin management gives every administrator a detail page with a **Message** button and a quest list showing all of their quests, including deleted ones with a 🗑 marker. Quests opened from that list are fully manageable, so a superadmin can restore or permanently delete a deleted quest without leaving the section.
- The audit log is rendered as a terminal-style monospaced block with aligned timestamp, action, entity, and actor columns plus a **Refresh** button.

### D. Group and channel integration
- Superadmins register managed chats. A quest can publish to one registered group/channel or run in the bot only.
- In scheduled mode, each stage question is copied from the private archive and announced in the selected chat once. Participants submit their answers privately to the bot.
- In immediate mode, only the first stage is published in the chat. Later stages are copied from the archive and sent privately to each participant so one participant's progress does not reveal a later question to everyone.
- The published question offers **Answer privately** and, directly below it, a **Main menu** button that opens the bot.
- Superadmins can enable chat cleanup per managed chat. At quest start, the bot removes tracked members who are not on that chat's whitelist.
- `chat_member` updates track membership changes received after a chat is registered.

### E. Invite links
- A private quest gets a bot deep-link token that allows users to join that private quest.
- Each participant who joins a quest with an attached Telegram chat receives an individual invite link. Participants who joined before the quest starts receive it after start-time cleanup; participants who join an already-active quest receive it immediately. The link can be retrieved from the bot again later. The bot creates invite links with `member_limit=1` and no `expire_date`; the link's one-person limit is consumed by the first successful join.
- The invite link created for a participant is stored once and the same link is returned when requested again.

### F. Support tickets, audit, and runtime logs
- A user can open a support conversation with a superadmin or an admin of a quest they joined.
- Messages in a ticket are relayed in both directions through the bot. Replies identify the specific quest administrator or superadmin and ticket; user-facing sent acknowledgements omit reply buttons, while admin notifications keep an inline reply action.
- Important admin actions are written to the audit log. Superadmins can view recent entries and export data.
- Runtime activity is written to a rotating `log` file and errors with tracebacks to `log_err`; systemd also captures console logs.
- Unhandled Telegram update errors are reported to configured superadmins with safe update identifiers and a redacted traceback. The affected user receives a localized generic notice in a private chat. Expected validation failures are not treated as software errors.

## 3. Roles and permissions

| Action | Participant | Admin | Superadmin |
|---|---:|---:|---:|
| Browse and join public quests with explicit confirmation | Yes | Yes | Yes |
| View private quests already joined | Yes | Yes | Yes |
| Create a quest | No | Yes | Yes |
| Manage a quest | No | Own quests only | All quests |
| Review manual answers | No | Own quests only | All quests |
| Block a participant from a quest | No | Own quests only | All quests |
| Message a quest participant | No | Own quests only | All quests |
| Delete a quest (soft delete) | No | Own quests only | All quests |
| List, restore, or permanently delete deleted quests | No | No | Yes |
| Add or remove admins | No | No | Yes |
| Archive quests, change the page size/global settings, and export data | No | No | Yes |

## 4. Status and time rules

- Quest lifecycle: `scheduled` → `active` → `completed`; a superadmin can move a quest to `archived` or unarchive it. Pause is stored separately from lifecycle status, preserving whether the quest was scheduled or active.
- User-entered times use `Asia/Tashkent`; the database stores UTC ISO 8601 timestamps.
- Overall time is entered either as a duration in minutes (`0` means no limit) or as an explicit end time in `Asia/Tashkent` (`YYYY-MM-DD HH:MM`); the end time must be later than the quest start and is converted into the stored duration. The same two forms are accepted while creating a quest and from **Edit quest details → Change duration** for a scheduled or running quest; a finished quest can no longer be re-timed.
- In scheduled mode, stage start times must be strictly increasing and, when an overall duration is set, must not extend past the quest end time.
- When the next scheduled stage begins, any still-open previous stage is closed for that participant.
- A soft-deleted quest keeps all of its rows; only the delete flag changes. Deleted quests are excluded from browsing, joining, answering, scheduling, ratings, and statistics until a superadmin restores them, and only superadmins can see that the record still exists. Permanent deletion is available to superadmins alone and cannot be undone.

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
- `quest_bot/localization.py` — Uzbek, «Yangi o‘zbek», Karakalpak, Russian, and English translations.
- `quest_bot/localization_kaa.py` — the full Karakalpak interface dictionary merged into `localization.py`.
- `tests/` — database, core-rule, language, soft-delete, purge, leaderboard, and start-button tests.

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
