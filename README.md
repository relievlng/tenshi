# FiveM Gang Management Bot

A Discord bot to manage a FiveM roleplay gang: roster, Kill-On-Sight list, and an
Expression-of-Interest (EOI) application system with Yes/No voting.

## Commands

All channel/role settings live in `/settings`. Staff commands need the leadership role or admin.

### Roster
| Command | What it does | Who |
|---|---|---|
| `/roster view` | Show the roster, built live from your rank roles (members grouped by rank) with a 🔄 Refresh button | Everyone |
| `/roster add @member [more] [rank]` | Add up to 30 members (pick one + paste the rest); a rank gives that rank's role | Everyone |
| `/roster remove @member` | Remove a member and take the role back | Everyone |
| `/roster reset` | Wipe the roster list (keeps everyone's role) | Leadership |
| `/roster rank_set <name> <role>` | Map a rank name to a server role | Leadership |
| `/roster rank_remove <name>` / `rank_list` | Remove / list ranks | Leadership / Everyone |

### KOS
| Command | What it does | Who |
|---|---|---|
| `/kos view` | Show the KOS list | Everyone |
| `/kos add @member [more] [reason]` | Add up to 30 to KOS (pick one + paste the rest) and give the KOS role | Everyone |
| `/kos remove @member` | Remove and take the role back | Everyone |
| `/kos reset` | Full KOS roster reset: clears the list, takes the KOS role off everyone, cancels open sign-ups/draws — keeps your saved KOS channels | Leadership |
| `/kos start <text> <size> [channel] [draw_channel] [roster_channel] [ping_role]` | Channels and ping role are remembered after the first run, so later you can just do `/kos start text size`. Posts the KOS sign-up in `channel` (your KOS announcements), pinging `ping_role`. A **🎲 Draw** control goes to `draw_channel` (else staff channel, else here). Draw randomly picks `size`, pings the role, and lets leadership Remove / Add / Reshuffle in that same chat, then **Approve** — which posts the final roster to KOS announcements **and** `roster_channel`, pinging and assigning the **KOS role** | Leadership |

### Tickets / Orders
| Command | What it does | Who |
|---|---|---|
| `/ticket type_add <name> [category] [emoji]` | Add a ticket type (Gang Orders, Vendors, Gun Crafting…) + its channel category | Leadership |
| `/ticket type_remove <name>` / `type_list` | Remove / list types | Leadership / Everyone |
| `/ticket panel [message]` | Post a dropdown menu of types; picking one opens a private ticket | Leadership |
| `/ticket toggle` | Open or close new ticket orders for everyone | Leadership |
| `/ticket board` | Active orders grouped by type, paid/unpaid | Staff |
| `/close [reason]` | Close the ticket you're in — the reason and how long it was open go in the transcript, the customer's DM and the log | Staff or ticket owner |
| `/ticket add @member` / `/ticket remove @member` | Let someone into the ticket you're in, or take them out | Staff |
| `/ticket stats` | Open/closed/paid counts, tickets-per-day graph, average time open, and a staff leaderboard | Staff |
| `/ticket autoclose <hours>` | Warn then auto-close tickets with no messages for that long (0 = off) | Leadership |
| (in ticket) 🚩 Priority | Cycle Low → Normal → High → Urgent; the board sorts urgent first | Staff |
| (after close) DM | Customer gets one short DM (ticket #, paid, time open, items, reason) with the chat log attached; staff get the full HTML + text transcript | Customer |
| (in ticket) 💰 Mark Paid · 🔒 Close | Mark paid, or close → transcript then delete | Staff |

### Recruitment · Radio · Roles
| Command | What it does | Who |
|---|---|---|
| (ticket channels) | Each ticket channel is named `❌-<type>-<customer>-<id>` and flips to `✅-…` when marked paid | — |
| `/eoi` | Application form: in-game name, role + activity dropdowns, montages (with YouTube preview), and an optional **vouch** — pick a current member and they get a DM to confirm; their name then shows on the application. Posts to the EOI channel where current members vote ✅/❌. Every EOI auto-closes after **24h** (leadership can change it with the ⏱️ dropdown) — when it ends the vote is tallied, the post locks, and accepted applicants are welcomed into the gang there and given the base **Tenshi** rank role (falls back to the roster role only if no Tenshi rank is set), plus a DM | Everyone |
| `/eoi_post [message]` | Post an "Apply" button | Leadership |
| `/radio new` / `view` / `panel` | Roll a new frequency / show it / post a reroll button | Leadership / Everyone |
| `/role_menu add` / `remove` / `list` / `post` | Manage & post a self-serve role dropdown | Leadership |

### Gang
| Command | What it does | Who |
|---|---|---|
| `/gang dashboard` | One-glance overview: ranked members, KOS, open EOIs, open/urgent tickets, strikes (30d), wars, radio | Everyone |
| `/gang profile [@member]` | Rank, KOS status, strikes bar, join date, tickets opened/handled | Everyone |
| `/strike add @member <reason>` | Give a strike (DMs them, warns at the limit) | Leadership |
| `/strike list [@member]` / `remove <id>` / `clear @member` / `limit <n>` | Strike board, per-member history, remove/clear, set the limit (default 3) | Leadership (list: everyone) |
| `/war start <enemy> [first_to]` | Posts a live scoreboard (big score, win-rate bar, 🟩🟥 rounds). Staff tap 🏆 **Round won** / 💀 **Round lost** / ↩️ Undo / 🏳️ End; with `first_to` it ends itself | Leadership |
| `/war view` / `history` / `panel <enemy>` / `end <enemy>` | Active wars with scoreboard links · past results · re-post a scoreboard · end a war | View/history: everyone |

### Settings
| Command | What it does | Who |
|---|---|---|
| `/settings view` | Setup progress bar, ✅/⬜ for every setting, and a 🩺 health check (can't post somewhere, role above Tenshi, key channel missing) | Leadership |
| `/settings channel` / `/settings role` | Set one channel or role setting from a short emoji list (leave empty to clear) | Leadership (leadership role: admin) |
| `/settings logs #channel` | Send every log type to one channel in one go | Leadership |
| `/settings logs_setup` | Auto-create a **📋 Gang Manager Logs** category with a channel for every log type (general, roster, KOS, recruitment, roles, joins, tickets, orders, website) and wire up all the settings. Re-run any time to re-create missing ones | Leadership |
| `/gang_help` | List all commands | Everyone |

## Code layout

`gang_bot.py` is the entrypoint (Railway runs `python gang_bot.py`); features live in `tenshi_bot/`:

| File | What's in it |
|---|---|
| `core.py` | Config, JSON data store, the bot object, logging, branding + Components V2 helpers, EOI & role-menu views, command groups |
| `roster.py` | `/roster` — live roster from rank roles |
| `kos.py` | `/kos` — KOS list and the sign-up → draw → approve flow |
| `settings.py` | `/settings` |
| `panels.py` | `/role_menu`, `/radio`, `/eoi`, `/eoi_post`, `/gang_help` |
| `tickets.py` | Ticket system: panel, tickets, transcripts, priority, auto-close, stats |
| `gang.py` | `/gang`, `/strike`, `/war` |
| `web.py` | Optional web roster manager (Discord-login gated) |

## Setup

1. Create a bot at <https://discord.com/developers/applications>, copy its **token**.
2. Under **Bot**, no privileged intents are required (everything uses slash commands).
3. Invite it with the `applications.commands` and `bot` scopes, and permission to
   send messages / embed links in the channels you care about.
4. For the roster role feature, give the bot **Manage Roles**, and drag the bot's
   own role **above** the roster role in Server Settings → Roles (Discord only lets
   a bot manage roles beneath its highest one). Then run `/roster set_role`.

## Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `DISCORD_TOKEN` | yes | — | Your bot token |
| `LEADERSHIP_ROLE` | no | `Leadership` | Name of the role allowed to run leadership actions |
| `DATA_FILE` | no | `gang_data.json` | Where data is saved |
| `MEMBERS_INTENT` | no | off | Set `true` (and enable Server Members Intent in the portal) for join/leave events |
| `PORT` | no | — | Set automatically by Railway for a web service; turns on the web roster manager |
| `DISCORD_CLIENT_SECRET` | no | — | OAuth2 secret (Developer Portal → OAuth2) — required for the web manager login |
| `WEB_SECRET` | no | derived | Optional extra secret used to sign web sessions |

## Web roster manager (optional)

A browser page, served by the bot itself, that mirrors a lot of the bot. It's
**off** unless you set it up, and the bot runs exactly the same without it.

- **Overview strip** — live counts (ranked, KOS, open EOIs, strikes 30d, wars, radio).
- **Roster tab** — everyone grouped by rank, each with their Discord **@handle**.
  Per member you can **remove an individual role** (the ✕ on each role chip),
  **add a rank** (the `+ role` dropdown), or **Remove all** (strip every gang
  role at once). Roles above the bot show a 🔒 and can't be touched.
- **KOS tab** — the KOS list with each entry's reason, who added it and when,
  and **Take off KOS** (removes the KOS role + list entry).
- **Strikes tab** — everyone who currently has strikes, worst first: a count
  against the limit, a bar, and every strike's reason, who issued it and when.
  Members with strikes also show a ⚠️ badge on the Roster tab. Read-only.
- **Stash tab** — a gang inventory: add/±/remove items, and a **screenshot
  scanner** — drop a stash screenshot and it OCRs the item text (names +
  quantities), crops the item icon with its background cut out, shows you the
  candidates to confirm/edit, then adds them.
- **Wars tab** — active/past wars with live scores, win-rate bar, a recent
  rounds strip (🟩🟥), when each war started, the first-to target and progress,
  and the last round logged. Read-only.
- **Orders tab** — orders **grouped by category** (ticket type), each group
  showing open/paid/total counts. Per order: customer (with their Discord
  **@handle**), items/guns + quantity, paid/unpaid, priority and age. **＋ New
  order** opens a real Discord ticket (type + optional customer, items,
  quantity, notes); **Mark paid / unpaid** toggles payment; **Force close**
  closes the ticket (transcript saved, channel deleted) even if the channel is
  gone; **🧹 Clear closed** removes old closed orders from the list. **⚙ Order
  types** creates and names the types people can open (e.g. "Tenshi Vendor",
  "P90s Order") — the posted ticket panel **auto-updates** when types change.

Every change applies to Discord live.

The screenshot scanner needs the **Tesseract** OCR engine; `nixpacks.toml`
installs it on Railway automatically. Without it, scanning just reports
"couldn't read any items" and manual entry still works.

Access is gated by a dedicated **Website access role** (separate from
leadership): people log in with Discord, and only those holding that role can
open it. Access is re-checked on every action and by a fast background poll
(fetched live from Discord, so it works even without the members intent) plus
whenever the tab regains focus — so if someone's website-access role is taken
away, they're logged out almost immediately. A bot cannot remove anyone's
*server tag* — that's controlled by each user — so this manages roster roles,
not tags.

**Setup**
1. In the Discord **Developer Portal → OAuth2**, copy the **Client Secret** and
   add a redirect: `https://<your-railway-domain>/callback`.
2. In Railway, add a **public domain** (Settings → Networking → Generate Domain)
   so the service gets a URL and a `PORT`.
3. Add service variables: `DISCORD_CLIENT_SECRET=<the secret>`.
4. In Discord, make a role (e.g. `Web Access`) and point the bot at it:
   `/settings role → 🌐 Website access role`. Give it to whoever should manage
   the roster.
5. Open `https://<your-railway-domain>/`, log in with Discord, and you're in.

## Run locally

```bash
pip install -r requirements.txt
export DISCORD_TOKEN="your-token-here"
python gang_bot.py
```

## Run on Railway

Same as your other bot: push this repo, add `DISCORD_TOKEN` (and optionally
`LEADERSHIP_ROLE`) as service variables, and set the start command to
`python gang_bot.py`.

> **Data persistence note:** Railway's filesystem resets on redeploy. To keep the
> roster/KOS between deploys, attach a Volume and point `DATA_FILE` at it, e.g.
> `DATA_FILE=/data/gang_data.json`.
