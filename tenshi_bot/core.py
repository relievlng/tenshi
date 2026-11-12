"""Core: config, data store, the shared bot object, logging, branding and
Components V2 helpers, EOI + role-menu views, and the command groups.
Every other module builds on this via `from .core import *`."""

import os
import io
import re
import json
import random
import asyncio
import logging
import datetime
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

TOKEN = os.getenv("DISCORD_TOKEN")

# Name of the role that is allowed to run leadership-only actions
# (reset roster/KOS, set the EOI channel, and vote on applications).
LEADERSHIP_ROLE_NAME = os.getenv("LEADERSHIP_ROLE", "Leadership")

# Where persistent data lives. On Railway, point this at a mounted
# volume path if you want it to survive redeploys (e.g. /data/gang_data.json).
DATA_FILE = os.getenv("DATA_FILE", "gang_data.json")

# Lock the bot to specific servers. Comma-separated guild IDs in ALLOWED_GUILD_IDS;
# the bot leaves any server not on the list. Empty = no restriction.
_allowed_raw = os.getenv("ALLOWED_GUILD_IDS", "").replace(" ", "")
ALLOWED_GUILD_IDS = {int(x) for x in _allowed_raw.split(",") if x.isdigit()}

EMBED_COLOR = 0xE11D48  # deep red, fits the "gang" vibe

# --------------------------------------------------------------------------- #
# Palette — one source of truth for every embed colour.
#
# Status colours carry meaning across the whole bot, so an accept always looks
# like an accept and a loss always looks like a loss. Accent colours give each
# feature its own identity (and double as the log-category hues).
# --------------------------------------------------------------------------- #
OK = 0x22C55E       # success · accepted · win · paid · active
BAD = 0xDC2626      # danger · loss · strike · over the limit
WARN = 0xF59E0B     # pending · priority · needs attention
MUTED = 0x6B7280    # closed · denied · inactive · cancelled

BLUE = 0x3B82F6     # roster
PURPLE = 0xA855F7   # radio · roles
SKY = 0x0EA5E9      # tickets · channels
PINK = 0xEC4899     # members
TEAL = 0x14B8A6     # joins & leaves

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
log = logging.getLogger("gang-bot")


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #

def _default_data() -> dict[str, Any]:
    return {
        # per-guild data keyed by guild id (as string)
        "guilds": {}
    }


def load_data() -> dict[str, Any]:
    if not os.path.exists(DATA_FILE):
        return _default_data()
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        log.error("Could not read %s (%s). Starting fresh.", DATA_FILE, exc)
        return _default_data()


def save_data(data: dict[str, Any]) -> None:
    tmp = DATA_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, DATA_FILE)  # atomic write
    except OSError as exc:
        log.error("Failed to save data: %s", exc)


def guild_store(data: dict[str, Any], guild_id: int) -> dict[str, Any]:
    """Return (creating if needed) the data bucket for one guild."""
    g = data["guilds"].setdefault(
        str(guild_id),
        {
            "roster": [],
            "kos": [],
            "eoi_channel_id": None,
            "roster_role_id": None,
            "kos_role_id": None,
            "self_roles": [],
            "radio_freq": None,
            "radio_ping_role_id": None,
            "ranks": [],
            "log_channel_id": None,
            "ticket_log_channel_id": None,
            "order_log_channel_id": None,
            "join_log_channel_id": None,
            "welcome_channel_id": None,
            "staff_channel_id": None,
            "roster_log_channel_id": None,
            "kos_log_channel_id": None,
            "radio_log_channel_id": None,
            "eoi_log_channel_id": None,
            "member_log_channel_id": None,
            "web_log_channel_id": None,
            "orders": [],
            "next_order_id": 1,
            "tickets_open": True,
            "ticket_category_id": None,
            "ticket_types": [],
            "leadership_role_id": None,
            "transcript_channel_id": None,
        },
    )
    # make sure older save files get any new keys
    g.setdefault("leadership_role_id", None)
    g.setdefault("transcript_channel_id", None)
    g.setdefault("ticket_types", [])
    g.setdefault("roster", [])
    g.setdefault("kos", [])
    g.setdefault("eoi_channel_id", None)
    g.setdefault("eoi_ping_role_id", None)
    g.setdefault("roster_role_id", None)
    g.setdefault("kos_role_id", None)
    g.setdefault("self_roles", [])
    g.setdefault("radio_freq", None)
    g.setdefault("radio_ping_role_id", None)
    g.setdefault("ranks", [])
    g.setdefault("log_channel_id", None)
    g.setdefault("ticket_log_channel_id", None)
    g.setdefault("order_log_channel_id", None)
    g.setdefault("roster_log_channel_id", None)
    g.setdefault("kos_log_channel_id", None)
    g.setdefault("radio_log_channel_id", None)
    g.setdefault("eoi_log_channel_id", None)
    g.setdefault("member_log_channel_id", None)
    g.setdefault("web_log_channel_id", None)
    g.setdefault("join_log_channel_id", None)
    g.setdefault("welcome_channel_id", None)
    g.setdefault("staff_channel_id", None)
    g.setdefault("kos_musters", {})
    g.setdefault("kos_draws", {})
    g.setdefault("kos_proposals", {})
    g.setdefault("orders", [])
    g.setdefault("next_order_id", 1)
    g.setdefault("tickets_open", True)
    g.setdefault("ticket_category_id", None)
    g.setdefault("ticket_autoclose_hours", 0)
    g.setdefault("strikes", [])
    g.setdefault("next_strike_id", 1)
    g.setdefault("strike_limit", 3)
    g.setdefault("bank", {"balance": 0, "tx": []})
    g.setdefault("wars", [])
    return g


# --------------------------------------------------------------------------- #
# Bot setup
# --------------------------------------------------------------------------- #

intents = discord.Intents.default()
# Join/leave events + welcome messages need the members intent, which is
# privileged. Turning it on here ALSO requires enabling "Server Members Intent"
# in the Developer Portal — so it is opt-in via an env var to avoid crashing
# the whole bot when the portal toggle isn't set yet.
# To enable: flip Server Members Intent in the portal, then set MEMBERS_INTENT=true.
MEMBERS_INTENT = os.getenv("MEMBERS_INTENT", "").lower() in ("1", "true", "yes", "on")
intents.members = MEMBERS_INTENT
bot = commands.Bot(command_prefix="!", intents=intents)
DATA = load_data()


def is_leadership(member: discord.Member) -> bool:
    """True if the member is a server admin or has the configured leadership role.

    The leadership role can be set in-server with /set_leadership (stored by id);
    otherwise it falls back to a role named LEADERSHIP_ROLE_NAME.
    """
    if member.guild_permissions.administrator:
        return True
    store = guild_store(DATA, member.guild.id)
    rid = store.get("leadership_role_id")
    if rid and any(r.id == rid for r in member.roles):
        return True
    return any(r.name == LEADERSHIP_ROLE_NAME for r in member.roles)


async def _deny(
    interaction: discord.Interaction, what: str = "do that", who: str = "leadership"
) -> bool:
    """The one permission gate for every command. If the user isn't leadership,
    send a uniform ephemeral "Only {who} can {what}." and return True (so the
    caller returns). Otherwise return False. `who` only changes the wording —
    there's no separate staff role, so staff actions still require leadership."""
    if is_leadership(interaction.user):
        return False
    await interaction.response.send_message(
        f"Only {who} can {what}.", ephemeral=True
    )
    return True


def _leadership_role(
    guild: discord.Guild, store: dict[str, Any]
) -> discord.Role | None:
    """The leadership role: the one set in the settings panel, else one named
    LEADERSHIP_ROLE_NAME. Used to grant staff access to ticket channels."""
    rid = store.get("leadership_role_id")
    if rid:
        role = guild.get_role(rid)
        if role is not None:
            return role
    return discord.utils.get(guild.roles, name=LEADERSHIP_ROLE_NAME)


def _brand(
    embed: discord.Embed, guild: discord.Guild | None, footer: str | None = None
) -> discord.Embed:
    """Consistent look: footer carries the server name + icon. Keeps any
    existing footer text (prefixed to the server name)."""
    if guild is None:
        return embed
    existing = embed.footer.text if embed.footer and embed.footer.text else None
    text = footer or existing
    text = f"{text} · {guild.name}" if text else guild.name
    text = f"{text} · made by pmxl"
    if guild.icon:
        embed.set_footer(text=text[:2048], icon_url=guild.icon.url)
    else:
        embed.set_footer(text=text[:2048])
    return embed


def _embed_container(embed: discord.Embed) -> "discord.ui.Container":
    """Render a classic embed as a Components V2 container: big header (with the
    thumbnail beside it), body, inline fields packed into a grid-ish block,
    full-width fields as sub-headings, and the footer as small print."""
    container = discord.ui.Container(
        accent_colour=embed.colour or discord.Colour(EMBED_COLOR)
    )
    head_parts = []
    if embed.author and embed.author.name:
        head_parts.append(f"-# {embed.author.name}")
    if embed.title:
        head_parts.append(f"## {embed.title}")
    if embed.description:
        head_parts.append(embed.description)
    head_txt = "\n".join(head_parts) or "​"
    thumb = embed.thumbnail.url if embed.thumbnail and embed.thumbnail.url else None
    if thumb:
        container.add_item(
            discord.ui.Section(
                discord.ui.TextDisplay(head_txt[:3900]),
                accessory=discord.ui.Thumbnail(thumb),
            )
        )
    else:
        container.add_item(discord.ui.TextDisplay(head_txt[:3900]))

    if embed.fields:
        container.add_item(discord.ui.Separator())
        inline_buf: list[str] = []

        def flush() -> None:
            if inline_buf:
                container.add_item(discord.ui.TextDisplay("\n".join(inline_buf)[:3900]))
                inline_buf.clear()

        for f in embed.fields:
            if f.inline:
                inline_buf.append(f"**{f.name}** · {f.value}")
            else:
                flush()
                container.add_item(discord.ui.TextDisplay(f"### {f.name}\n{f.value}"[:3900]))
        flush()

    if embed.image and embed.image.url:
        container.add_item(
            discord.ui.MediaGallery(discord.MediaGalleryItem(embed.image.url))
        )
    if embed.footer and embed.footer.text:
        container.add_item(discord.ui.Separator(visible=False))
        container.add_item(discord.ui.TextDisplay(f"-# {embed.footer.text}"))
    return container


def _embed_to_v2(embed: discord.Embed) -> "discord.ui.LayoutView":
    layout = discord.ui.LayoutView()
    layout.add_item(_embed_container(embed))
    return layout


def _is_v2(message: "discord.Message | None") -> bool:
    try:
        return bool(message is not None and message.flags.components_v2)
    except Exception:  # noqa: BLE001
        return False


def _v2_layout(
    embeds: "list[discord.Embed]",
    view: "discord.ui.View | None" = None,
    header: str | None = None,
) -> "discord.ui.LayoutView":
    """Embeds → V2 containers, plus the classic view's buttons/menus moved into
    action rows (same items, same custom_ids, same callbacks)."""
    layout = discord.ui.LayoutView(timeout=None)
    if header:
        layout.add_item(discord.ui.TextDisplay(header))
    for e in embeds:
        layout.add_item(_embed_container(e))
    if view is not None:
        items = list(view.children)
        buf: list = []

        def flush() -> None:
            if buf:
                layout.add_item(discord.ui.ActionRow(*buf))
                buf.clear()

        last_row = object()
        for item in items:
            if getattr(item, "width", 1) >= 5:  # menus take a whole row
                flush()
                layout.add_item(discord.ui.ActionRow(item))
                last_row = object()
                continue
            if buf and (len(buf) == 5 or (item.row is not None and item.row != last_row)):
                flush()
            buf.append(item)
            last_row = item.row
        flush()
    return layout


async def _send_card(
    dest,
    embeds: "list[discord.Embed]",
    make_view=None,
    *,
    header: str | None = None,
    allowed_mentions: "discord.AllowedMentions | None" = None,
    ephemeral: bool = False,
):
    """Post in the V2 look (with buttons); on any V2 failure, post the classic
    embed + view instead. `dest` is a channel/user or an Interaction.
    `make_view` builds a FRESH view each attempt. Returns the Message (or None
    for interaction responses)."""
    am = allowed_mentions or discord.AllowedMentions.none()
    is_inter = isinstance(dest, discord.Interaction)
    try:
        layout = _v2_layout(embeds, make_view() if make_view else None, header)
        if is_inter:
            if dest.response.is_done():
                return await dest.followup.send(
                    view=layout, allowed_mentions=am, ephemeral=ephemeral, wait=True
                )
            await dest.response.send_message(view=layout, allowed_mentions=am, ephemeral=ephemeral)
            return None
        return await dest.send(view=layout, allowed_mentions=am)
    except discord.Forbidden:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("V2 send failed (%s) — falling back to classic", exc)
    kwargs = {"embeds": embeds, "allowed_mentions": am}
    if make_view:
        kwargs["view"] = make_view()
    if header:
        kwargs["content"] = header
    if is_inter:
        if dest.response.is_done():
            return await dest.followup.send(ephemeral=ephemeral, wait=True, **kwargs)
        await dest.response.send_message(ephemeral=ephemeral, **kwargs)
        return None
    return await dest.send(**kwargs)


async def _edit_card(
    message: "discord.Message",
    embeds: "list[discord.Embed]",
    make_view=None,
    *,
    interaction: "discord.Interaction | None" = None,
    header: str | None = None,
) -> None:
    """Edit a card in whichever format it was posted in (V2 or classic).
    Uses the interaction response when it's still open, else edits the message."""
    if _is_v2(message):
        kwargs = {"view": _v2_layout(embeds, make_view() if make_view else None, header)}
    else:
        kwargs = {"embeds": embeds, "view": make_view() if make_view else None}
    if interaction is not None and not interaction.response.is_done():
        await interaction.response.edit_message(**kwargs)
    else:
        await message.edit(**kwargs)


async def _send_v2(
    interaction: discord.Interaction, embed: discord.Embed, *, ephemeral: bool = False
) -> None:
    """Send an embed in the new V2 look; if anything about V2 fails, fall back
    to the classic embed so the command always answers."""
    try:
        layout = _embed_to_v2(embed)
        await interaction.response.send_message(view=layout, ephemeral=ephemeral)
        return
    except Exception as exc:  # noqa: BLE001
        log.warning("V2 render failed (%s) — falling back to embed", exc)
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=ephemeral)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)


_SPARK_CHARS = "▁▂▃▄▅▆▇█"


def _spark(values) -> str:
    """Unicode sparkline, e.g. ▁▂▄▇█▆▃ — reads like a mini line graph."""
    vals = [float(v) for v in values]
    if not vals:
        return "—"
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return _SPARK_CHARS[3] * len(vals)
    return "".join(_SPARK_CHARS[min(7, int((v - lo) / (hi - lo) * 7.999))] for v in vals)


def _bar(n: float, total: float, width: int = 12) -> str:
    """Filled progress bar, e.g. ███████░░░░░."""
    if total <= 0:
        return "░" * width
    k = max(0, min(width, round(width * n / total)))
    return "█" * k + "░" * (width - k)


def _daily_counts(timestamps, days: int = 14) -> list[int]:
    """How many timestamps fell on each of the last `days` UTC days (oldest first)."""
    today = int(datetime.datetime.now(datetime.timezone.utc).timestamp()) // 86400
    counts = [0] * days
    for ts in timestamps:
        d = today - int(ts) // 86400
        if 0 <= d < days:
            counts[days - 1 - d] += 1
    return counts


def now_stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


async def notify_strike_change(guild, store, user_id: int, *, kind: str, strike_id=None) -> None:
    """DM a member that a strike was removed (`kind='remove'`) or all of their
    strikes were cleared (`kind='clear'`). Best-effort — a closed DM or a missing
    member is swallowed so it never blocks the action. Works the same whether the
    change came from `/strike` in Discord or from the website."""
    try:
        member = guild.get_member(user_id)
        if member is None:
            member = await guild.fetch_member(user_id)
    except discord.HTTPException:
        return
    if member is None:
        return
    limit = int(store.get("strike_limit", 3) or 3)
    count = sum(1 for s in store.get("strikes", []) if s.get("user_id") == user_id)
    bar = "🟥" * min(count, limit) + "⬛" * max(0, limit - count)
    if kind == "clear":
        title = "🧹 Your strikes were cleared"
        desc = f"All of your strikes in **{guild.name}** have been cleared. You're back to **0/{limit}**. ✨"
        color = OK
    else:
        sid = f" `#{strike_id}`" if strike_id is not None else ""
        title = "🗑️ A strike was removed"
        desc = (
            f"A strike{sid} was removed in **{guild.name}**.\n"
            f"You're now on **{count}/{limit}**.\n{bar}"
        )
        color = OK if count < limit else WARN
    embed = discord.Embed(title=title, description=desc, color=color,
                          timestamp=datetime.datetime.now(datetime.timezone.utc))
    _brand(embed, guild)
    try:
        await member.send(embed=embed)
    except discord.HTTPException:
        pass


LOG_CATEGORIES = {
    # category -> (emoji, label, color)
    "roster": ("📋", "Roster", BLUE),
    "kos": ("💀", "KOS", BAD),
    "radio": ("📻", "Radio", PURPLE),
    "eoi": ("📨", "Recruitment", OK),
    "members": ("🎭", "Roles", PINK),
    "joins": ("🚪", "Joins & Leaves", TEAL),
    "tickets": ("🎫", "Tickets", SKY),
    "orders": ("💰", "Orders", WARN),
    "web": ("🌐", "Website", PURPLE),
    "config": ("⚙️", "Config", MUTED),
    "activity": ("•", "Activity", EMBED_COLOR),
}

# which stored channel each category logs to (falls back to the general log)
LOG_ROUTING = {
    "roster": "roster_log_channel_id",
    "kos": "kos_log_channel_id",
    "radio": "radio_log_channel_id",
    "eoi": "eoi_log_channel_id",
    "members": "member_log_channel_id",
    "joins": "join_log_channel_id",
    "tickets": "ticket_log_channel_id",
    "orders": "order_log_channel_id",
    "web": "web_log_channel_id",
}


async def send_log(
    guild: "discord.Guild",
    store: dict[str, Any],
    text: str,
    *,
    category: str = "activity",
    actor: "discord.abc.User | None" = None,
    fields: "list[tuple[str, str, bool]] | None" = None,
    channel_ctx: "discord.abc.GuildChannel | None" = None,
) -> None:
    """Post a categorized, detailed audit entry to the log channel, if one is set.

    `fields` is a list of (name, value, inline) tuples for extra detail.
    `channel_ctx` adds a 'Channel' field pointing at where the action happened.
    """
    # route to the category's own channel, else fall back to the general log
    key = LOG_ROUTING.get(category)
    cid = (store.get(key) if key else None) or store.get("log_channel_id")
    if not cid:
        return
    channel = guild.get_channel(cid)
    if channel is None:
        return
    emoji, label, color = LOG_CATEGORIES.get(category, LOG_CATEGORIES["activity"])
    embed = discord.Embed(
        description=text,
        color=color,
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )
    embed.set_author(name=f"{emoji} {label}")
    if actor is not None:
        embed.add_field(
            name="Member", value=f"{actor.mention}\n`{actor}` · `{actor.id}`", inline=True
        )
    if channel_ctx is not None:
        embed.add_field(name="Channel", value=channel_ctx.mention, inline=True)
    for name, value, inline in fields or []:
        embed.add_field(name=name, value=value or "—", inline=inline)
    if actor is not None:
        embed.set_footer(text=str(actor), icon_url=actor.display_avatar.url)
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        pass


# Matches the video id in the common YouTube URL shapes:
#   youtube.com/watch?v=ID, youtu.be/ID, youtube.com/shorts/ID, /embed/ID, /live/ID
_YT_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|embed/|live/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{11})"
)


def youtube_thumbnail(url: str | None) -> str | None:
    """Return the thumbnail image URL for a YouTube link, or None if it isn't one."""
    if not url:
        return None
    match = _YT_RE.search(url)
    if not match:
        return None
    return f"https://img.youtube.com/vi/{match.group(1)}/hqdefault.jpg"


async def youtube_preview(url: str) -> dict[str, Any] | None:
    """Preview info for a YouTube link: watch url, title, channel and the
    sharpest thumbnail that exists. Title/channel come from YouTube's public
    oEmbed endpoint (no API key); if YouTube can't be reached we still return
    the link + standard thumbnail so the EOI always gets a preview."""
    match = _YT_RE.search(url or "")
    if not match:
        return None
    vid = match.group(1)
    info: dict[str, Any] = {
        "url": f"https://youtu.be/{vid}",
        "title": None,
        "author": None,
        "image": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
    }
    try:
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=6)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                "https://www.youtube.com/oembed",
                params={"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    info["title"] = (data.get("title") or "").strip() or None
                    info["author"] = (data.get("author_name") or "").strip() or None
            # 1280x720 widescreen thumbnail when the video has one (no black bars)
            maxres = f"https://i.ytimg.com/vi/{vid}/maxresdefault.jpg"
            async with session.head(maxres) as resp:
                if resp.status == 200:
                    info["image"] = maxres
    except Exception as exc:  # preview is best-effort; never block an EOI on it
        log.info("YouTube preview lookup failed for %s: %s", vid, exc)
    return info


def _md_link_text(text: str, limit: int = 80) -> str:
    """Make a title safe to put inside a [masked](link)."""
    text = text.replace("[", "(").replace("]", ")").replace("*", "").replace("_", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --------------------------------------------------------------------------- #
# EOI voting view (persistent buttons)
# --------------------------------------------------------------------------- #

_EOI_TIMERS = [
    ("1 hour", 1),
    ("6 hours", 6),
    ("12 hours", 12),
    ("24 hours", 24),
    ("2 days", 48),
    ("3 days", 72),
    ("7 days", 168),
]
_EOI_TIMER_FIELD = "⏱️ Voting closes"
_EOI_VOUCH_FIELD = "🤝 Vouch"
_EOI_DEFAULT_HOURS = 24  # every EOI auto-closes this many hours after it's posted


def _set_embed_field(embed: discord.Embed, name: str, value: str, inline: bool = False) -> None:
    for i, f in enumerate(embed.fields):
        if f.name == name:
            embed.set_field_at(i, name=name, value=value, inline=inline)
            return
    embed.add_field(name=name, value=value, inline=inline)


class EOIView(discord.ui.View):
    """Yes / No voting buttons attached to an EOI application embed.

    Votes are stored on the embed itself (as footer counts) and tracked in
    memory per-message so one person can't vote twice. The view is persistent
    (timeout=None) with a fixed custom_id so buttons keep working after a
    restart.
    """

    def __init__(self) -> None:
        super().__init__(timeout=None)
        # message_id -> {user_id: "yes"|"no"}
        # kept on the view instance; re-registered on startup
        self.votes: dict[int, dict[int, str]] = {}

    def _tally(self, message_id: int) -> tuple[int, int]:
        record = self.votes.get(message_id, {})
        yes = sum(1 for v in record.values() if v == "yes")
        no = sum(1 for v in record.values() if v == "no")
        return yes, no

    async def _handle_vote(
        self, interaction: discord.Interaction, choice: str
    ) -> None:
        log.info(
            "Vote received: choice=%s user=%s msg=%s",
            choice,
            interaction.user,
            getattr(interaction.message, "id", None),
        )
        # Defer first (silent ack) so we can never miss Discord's 3s window,
        # then do the slower edit/followup afterwards.
        try:
            await interaction.response.defer()
        except discord.InteractionResponded:
            pass

        msg = interaction.message
        if msg is None:
            await interaction.followup.send(
                "Could not read this message.", ephemeral=True
            )
            return

        store = guild_store(DATA, interaction.guild_id)
        if store.get("eoi_timers", {}).get(str(msg.id), {}).get("done"):
            await interaction.followup.send("🔒 Voting on this EOI has closed.", ephemeral=True)
            return
        record = store.setdefault("eoi_votes", {}).setdefault(str(msg.id), {})
        previous = record.get(str(interaction.user.id))
        record[str(interaction.user.id)] = choice
        save_data(DATA)
        yes = sum(1 for v in record.values() if v == "yes")
        no = sum(1 for v in record.values() if v == "no")

        embed = msg.embeds[0] if msg.embeds else discord.Embed()
        embed.set_footer(text=f"✅ {yes}   •   ❌ {no}")
        await msg.edit(embed=embed, view=EOIView())

        if previous == choice:
            note = f"Your **{choice.upper()}** vote is already counted."
        elif previous:
            note = f"Changed your vote to **{choice.upper()}**."
        else:
            note = f"Counted your **{choice.upper()}** vote."
        await interaction.followup.send(note, ephemeral=True)

        if previous != choice:
            vote_emoji = "✅" if choice == "yes" else "❌"
            await send_log(
                interaction.guild,
                guild_store(DATA, interaction.guild_id),
                f"{vote_emoji} Application vote: **{choice.upper()}**",
                category="eoi",
                actor=interaction.user,
                fields=[("Tally", f"✅ {yes}  ·  ❌ {no}", True)],
            )

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item,
    ) -> None:
        log.exception(
            "EOIView error on %s: %s", getattr(item, "custom_id", "?"), error
        )
        text = (
            "⚠️ Couldn't record that vote — I may be missing permission to edit "
            "the message or post here."
        )
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except discord.HTTPException:
            pass

    @discord.ui.button(
        label="Yes",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id="eoi:yes",
    )
    async def yes_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._handle_vote(interaction, "yes")

    @discord.ui.button(
        label="No",
        style=discord.ButtonStyle.danger,
        emoji="❌",
        custom_id="eoi:no",
    )
    async def no_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._handle_vote(interaction, "no")

    @discord.ui.select(
        custom_id="eoi:timer",
        placeholder="⏱️  Leadership: change the 24h deadline…",
        row=1,
        options=[
            discord.SelectOption(label=f"Close voting in {label}", value=str(h), emoji="⏱️")
            for label, h in _EOI_TIMERS
        ],
    )
    async def set_timer(
        self, interaction: discord.Interaction, select: discord.ui.Select
    ) -> None:
        if await _deny(interaction, "set the EOI timer"):
            return
        await interaction.response.defer()
        msg = interaction.message
        store = guild_store(DATA, interaction.guild_id)
        rec = store.setdefault("eoi_timers", {}).setdefault(str(msg.id), {})
        if rec.get("done"):
            await interaction.followup.send("🔒 Voting on this EOI already closed.", ephemeral=True)
            return
        hours = int(select.values[0])
        ends = int(datetime.datetime.now(datetime.timezone.utc).timestamp()) + hours * 3600
        rec.update({"ends": ends, "channel_id": interaction.channel_id, "set_by": interaction.user.id})
        save_data(DATA)
        embed = msg.embeds[0] if msg.embeds else discord.Embed()
        _set_embed_field(embed, _EOI_TIMER_FIELD, f"<t:{ends}:R> · <t:{ends}:f>")
        await msg.edit(embed=embed, view=EOIView())
        await interaction.followup.send(f"⏱️ Voting closes <t:{ends}:R>.", ephemeral=True)
        await send_log(
            interaction.guild, store, f"⏱️ EOI voting timer set — closes <t:{ends}:R>.",
            category="eoi", actor=interaction.user,
        )


# single shared view instance so vote state is consistent
EOI_VIEW = EOIView()


# --------------------------------------------------------------------------- #
# EOI application modal (the form in your screenshot + a YouTube link)
# --------------------------------------------------------------------------- #

_EOI_ROLES = [
    ("Shooter", "🔫"),
    ("Crafter / Supplier", "🛠️"),
    ("Money / Business", "💰"),
    ("Whatever's needed", "🧩"),
]
_EOI_ACTIVITY = [
    ("Every day", "🔥"),
    ("Most days", "📅"),
    ("A few times a week", "🕒"),
    ("Weekends", "🎉"),
    ("Casual", "😴"),
]


async def _block_if_already_member(interaction: discord.Interaction) -> bool:
    """If the user already holds any gang role (the roster role OR any configured
    rank, e.g. Tenshi), tell them so and return True so the EOI form is never
    shown. Returns False if they can still apply (incl. when nothing is configured)."""
    store = guild_store(DATA, interaction.guild_id)
    gang_role_ids = set()
    if store.get("roster_role_id"):
        gang_role_ids.add(store["roster_role_id"])
    for r in store.get("ranks", []):
        if r.get("role_id"):
            gang_role_ids.add(r["role_id"])
    member_roles = getattr(interaction.user, "roles", None)
    if (
        gang_role_ids
        and member_roles
        and any(getattr(r, "id", None) in gang_role_ids for r in member_roles)
    ):
        await interaction.response.send_message(
            "You're already in the gang, silly 😄 — no need to apply.",
            ephemeral=True,
        )
        return True
    return False


class EOIModal(discord.ui.Modal, title="Join the Gang — Expression of Interest"):
    ign = discord.ui.Label(
        text="In-game name",
        description="Your FiveM character / RP name",
        component=discord.ui.TextInput(placeholder="e.g. Kenji Tanaka", max_length=100),
    )
    role = discord.ui.Label(
        text="What role do you want?",
        description="Pick what fits you best",
        component=discord.ui.Select(
            placeholder="Choose a role…",
            required=True,
            options=[discord.SelectOption(label=l, value=l, emoji=e) for l, e in _EOI_ROLES],
        ),
    )
    activity = discord.ui.Label(
        text="How active are you?",
        component=discord.ui.Select(
            placeholder="Choose how often you're on…",
            required=True,
            options=[discord.SelectOption(label=l, value=l, emoji=e) for l, e in _EOI_ACTIVITY],
        ),
    )
    montage = discord.ui.Label(
        text="Montage(s)",
        description="One YouTube link per line — optional",
        component=discord.ui.TextInput(
            style=discord.TextStyle.paragraph,
            placeholder="https://youtu.be/…",
            required=False,
            max_length=600,
        ),
    )
    voucher = discord.ui.Label(
        text="Vouch (optional)",
        description="Pick a current member to vouch for you — they get a DM to confirm",
        component=discord.ui.UserSelect(
            placeholder="Choose someone to vouch for you…",
            min_values=0,
            max_values=1,
            required=False,
        ),
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        store = guild_store(DATA, interaction.guild_id)
        channel_id = store.get("eoi_channel_id")

        if channel_id is None:
            await interaction.followup.send(
                "No EOI channel is set yet. Ask leadership to set one from "
                "`/settings channel` → 📥 EOI channel.",
                ephemeral=True,
            )
            return

        channel = interaction.guild.get_channel(channel_id)
        if channel is None:
            await interaction.followup.send(
                "The configured EOI channel no longer exists. "
                "Ask leadership to set it again.",
                ephemeral=True,
            )
            return

        user = interaction.user
        ign = self.ign.component.value.strip()
        role = (self.role.component.values or ["—"])[0]
        activity = (self.activity.component.values or ["—"])[0]
        role_emoji = dict(_EOI_ROLES).get(role, "🎯")
        act_emoji = dict(_EOI_ACTIVITY).get(activity, "🔥")

        embed = discord.Embed(
            title=f"📨  Expression of Interest — {ign}",
            description=f"{user.mention} wants in as {role_emoji} **{role}**",
            color=EMBED_COLOR,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="🎮 In-game", value=ign or "—", inline=True)
        embed.add_field(name="🎯 Role", value=f"{role_emoji} {role}", inline=True)
        embed.add_field(name="⏱️ Activity", value=f"{act_emoji} {activity}", inline=True)
        embed.add_field(
            name="👤 Applicant",
            value=f"`{user}` · `{user.id}`",
            inline=False,
        )
        embed.add_field(
            name="🗓️ Account made", value=f"<t:{int(user.created_at.timestamp())}:R>", inline=True
        )
        joined = getattr(user, "joined_at", None)
        embed.add_field(
            name="📥 Joined server",
            value=f"<t:{int(joined.timestamp())}:R>" if joined else "—",
            inline=True,
        )
        embed.add_field(name=_EOI_VOUCH_FIELD, value="—", inline=True)
        montage_raw = self.montage.component.value.strip()
        links = [ln.strip() for ln in montage_raw.splitlines() if ln.strip()][:5]
        previews = await asyncio.gather(*(youtube_preview(ln) for ln in links))
        lines = []
        for ln, pv in zip(links, previews):
            if pv is None:
                lines.append(f"🔗 {ln}")
                continue
            title = _md_link_text(pv["title"]) if pv["title"] else "Watch montage"
            line = f"▶️ **[{title}]({pv['url']})**"
            if pv["author"]:
                line += f"\n╰ by {discord.utils.escape_markdown(pv['author'])}"
            lines.append(line)
        embed.add_field(
            name=f"🎬 Montage{'s' if len(links) > 1 else ''}",
            value="\n".join(lines)[:1024] if lines else "—",
            inline=False,
        )
        # Big preview: the first YouTube montage's thumbnail, with a watch link
        # up top so it's the first thing voters see.
        first = next((pv for pv in previews if pv), None)
        if first:
            embed.set_image(url=first["image"])
            embed.description += f"\n\n🎬 **[▶  Watch montage]({first['url']})**"
        # Every EOI auto-closes after 24h; leadership can change it with the
        # dropdown below.
        default_ends = (
            int(datetime.datetime.now(datetime.timezone.utc).timestamp())
            + _EOI_DEFAULT_HOURS * 3600
        )
        embed.add_field(
            name=_EOI_TIMER_FIELD,
            value=f"<t:{default_ends}:R> · <t:{default_ends}:f>\n-# auto {_EOI_DEFAULT_HOURS}h — leadership can change below",
            inline=False,
        )
        embed.set_footer(text="✅ 0   •   ❌ 0")

        ping_id = store.get("eoi_ping_role_id")
        ping_role = interaction.guild.get_role(ping_id) if ping_id else None
        posted = await channel.send(
            content=f"{ping_role.mention} 📨 New EOI from {interaction.user.mention}"
            if ping_role
            else None,
            embed=embed,
            view=EOIView(),
            allowed_mentions=discord.AllowedMentions(roles=True, users=False),
        )
        store.setdefault("eoi_apps", {})[str(posted.id)] = {
            "user_id": user.id,
            "ign": ign,
            "role": role,
            "ts": int(datetime.datetime.now(datetime.timezone.utc).timestamp()),
        }
        # Arm the automatic 24h close. The dropdown overwrites this if leadership
        # picks a different length.
        store.setdefault("eoi_timers", {})[str(posted.id)] = {
            "ends": default_ends,
            "channel_id": channel.id,
            "set_by": None,
        }
        save_data(DATA)

        # Optional vouch picked right in the form.
        vouch_vals = self.voucher.component.values
        voucher = vouch_vals[0] if vouch_vals else None
        vouch_note = ""
        if voucher is not None:
            if voucher.id == user.id:
                vouch_note = "\n⚠️ You can't vouch for yourself — that was skipped."
            elif getattr(voucher, "bot", False):
                vouch_note = "\n⚠️ You picked a bot to vouch — that was skipped."
            elif await _request_vouch(interaction.guild, store, posted, user, voucher):
                vouch_note = (
                    f"\n🤝 Asked {voucher.mention} to vouch — they'll get a DM to confirm."
                )
            else:
                vouch_note = (
                    f"\n⚠️ Couldn't DM {voucher.mention} (their DMs are closed), "
                    "so no vouch was requested."
                )

        await interaction.followup.send(
            f"Your EOI has been sent to {channel.mention}. Good luck. 🫡{vouch_note}",
            ephemeral=True,
        )
        await send_log(
            interaction.guild,
            guild_store(DATA, interaction.guild_id),
            "📨 New application submitted.",
            category="eoi",
            actor=interaction.user,
            fields=[
                ("In-game", ign or "—", True),
                ("Role", role, True),
                ("Activity", activity, True),
            ],
        )


# ---- EOI voting timer ------------------------------------------------------- #

async def _close_eoi(
    guild: discord.Guild, store: dict[str, Any], mid: int, rec: dict, force: str | None = None
) -> None:
    """Tally votes, lock the EOI post, and — if it passed — welcome them into
    the gang in the EOI channel and give them the base Tenshi rank role (falling
    back to the roster role only if no Tenshi rank is configured). Denials are
    quiet: post is marked and the applicant gets a DM. `force` overrides the vote
    result: "accept" always passes, "deny" always fails."""
    ch = guild.get_channel(rec.get("channel_id") or 0)
    if ch is None:
        return
    votes = store.get("eoi_votes", {}).get(str(mid), {})
    yes = sum(1 for v in votes.values() if v == "yes")
    no = sum(1 for v in votes.values() if v == "no")
    passed = True if force == "accept" else False if force == "deny" else yes > no
    app = store.get("eoi_apps", {}).get(str(mid), {})
    uid = app.get("user_id")
    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())

    msg = None
    try:
        msg = await ch.fetch_message(mid)
    except discord.HTTPException:
        pass
    if msg is not None and msg.embeds:
        e = msg.embeds[0]
        e.colour = discord.Colour(OK if passed else MUTED)
        e.title = (("✅  ACCEPTED · " if passed else "❌  DENIED · ") + (e.title or "").replace("📨  ", ""))[:256]
        _set_embed_field(e, _EOI_TIMER_FIELD, f"🔒 Closed <t:{now}:R>")
        _set_embed_field(
            e, "🗳️ Result",
            f"{'✅ **Accepted**' if passed else '❌ **Denied**'} — ✅ {yes} · ❌ {no}",
        )
        e.set_footer(text=f"✅ {yes}   •   ❌ {no}   •   voting closed")
        try:
            await msg.edit(embed=e, view=None)
        except discord.HTTPException:
            pass

    member = guild.get_member(uid) if uid else None
    if member is None and uid:
        try:
            member = await guild.fetch_member(uid)
        except discord.HTTPException:
            member = None

    if passed:
        ann = discord.Embed(
            title="🎉  Welcome to the gang!",
            description=(
                f"<@{uid}> has been **made into the gang**." if uid else "An applicant made it in."
            ) + f"\n-# Final vote: ✅ {yes} · ❌ {no}",
            color=OK,
        )
        if member is not None:
            ann.set_thumbnail(url=member.display_avatar.url)
        _brand(ann, guild)
        try:
            await ch.send(
                content=f"<@{uid}>" if uid else None,
                embed=ann,
                reference=msg.to_reference(fail_if_not_exists=False) if msg else None,
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        except discord.HTTPException:
            pass
        # On accept, give the base "Tenshi" rank role (not the generic roster
        # role). Fall back to the roster role only if no Tenshi rank is set up.
        tenshi_rid = next(
            (r.get("role_id") for r in store.get("ranks", [])
             if r.get("role_id") and r.get("name", "").strip().lower() in ("tenshi", "tenchi")),
            None,
        )
        role_id = tenshi_rid or store.get("roster_role_id")
        role = guild.get_role(role_id) if role_id else None
        if member is not None and role is not None:
            try:
                await member.add_roles(role, reason="EOI accepted")
            except discord.HTTPException:
                pass

    if member is not None:
        try:
            await member.send(
                embed=discord.Embed(
                    title="🎉 You're in!" if passed else "Your application",
                    description=(
                        f"Your EOI for **{guild.name}** was **accepted** — welcome to the gang."
                        if passed
                        else f"Your EOI for **{guild.name}** wasn't accepted this time."
                    ),
                    color=OK if passed else MUTED,
                )
            )
        except discord.HTTPException:
            pass
    await send_log(
        guild, store,
        f"{'✅' if passed else '❌'} EOI {'accepted' if passed else 'denied'} — "
        f"{f'<@{uid}>' if uid else 'applicant'} (✅ {yes} · ❌ {no}).",
        category="eoi",
    )


# ---- EOI vouches ------------------------------------------------------------ #
#
# An applicant can pick one member to vouch for them. That member is DM'd an
# Accept / Decline button; on accept, their name is shown on the EOI post.
# The DM buttons are DynamicItems so they keep working across restarts — all the
# state they need (guild, EOI message, voucher) lives in the custom_id.


class VouchDecisionButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"vouch:(?P<action>yes|no):(?P<guild>\d+):(?P<msg>\d+):(?P<voucher>\d+)",
):
    def __init__(self, action: str, guild_id: int, msg_id: int, voucher_id: int):
        self.action = action
        self.guild_id = guild_id
        self.msg_id = msg_id
        self.voucher_id = voucher_id
        accept = action == "yes"
        super().__init__(
            discord.ui.Button(
                label="Accept vouch" if accept else "Decline",
                style=discord.ButtonStyle.success if accept else discord.ButtonStyle.secondary,
                emoji="🤝" if accept else "✖️",
                custom_id=f"vouch:{action}:{guild_id}:{msg_id}:{voucher_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(
            match["action"], int(match["guild"]), int(match["msg"]), int(match["voucher"])
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await _handle_vouch_decision(
            interaction, self.action == "yes", self.guild_id, self.msg_id, self.voucher_id
        )


def _vouch_dm_view(guild_id: int, msg_id: int, voucher_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(VouchDecisionButton("yes", guild_id, msg_id, voucher_id))
    view.add_item(VouchDecisionButton("no", guild_id, msg_id, voucher_id))
    return view


async def _handle_vouch_decision(
    interaction: discord.Interaction, accepted: bool, guild_id: int, msg_id: int, voucher_id: int
) -> None:
    if interaction.user.id != voucher_id:
        await interaction.response.send_message("This vouch request isn't for you.", ephemeral=True)
        return
    guild = bot.get_guild(guild_id)
    if guild is None:
        await interaction.response.edit_message(
            content="That server is no longer available.", embed=None, view=None
        )
        return
    store = guild_store(DATA, guild_id)
    rec = store.get("eoi_vouches", {}).get(str(msg_id))
    if not rec or rec.get("status") != "pending":
        await interaction.response.edit_message(
            content="This vouch request is no longer active.", embed=None, view=None
        )
        return
    rec["status"] = "accepted" if accepted else "declined"
    save_data(DATA)

    voucher_mention = f"<@{voucher_id}>"
    applicant_id = rec.get("applicant_id")

    # Update the EOI post so voters see the (declined) vouch.
    ch = guild.get_channel(rec.get("channel_id") or 0)
    if ch is not None:
        try:
            eoi_msg = await ch.fetch_message(msg_id)
            if eoi_msg.embeds:
                e = eoi_msg.embeds[0]
                _set_embed_field(
                    e,
                    _EOI_VOUCH_FIELD,
                    f"✅ Vouched by {voucher_mention}"
                    if accepted
                    else f"✖️ Declined by {voucher_mention}",
                    inline=True,
                )
                voting_done = store.get("eoi_timers", {}).get(str(msg_id), {}).get("done")
                await eoi_msg.edit(embed=e, view=None if voting_done else EOIView())
        except discord.HTTPException:
            pass

    # Close out the DM.
    result = discord.Embed(
        title="🤝 Vouch confirmed" if accepted else "Vouch declined",
        description=(
            f"You vouched for <@{applicant_id}> — your name now shows on their application. 🫡"
            if accepted
            else f"You declined to vouch for <@{applicant_id}>. No worries."
        ),
        color=OK if accepted else MUTED,
    )
    await interaction.response.edit_message(embed=result, view=None)

    # Let the applicant know when a vouch lands.
    if accepted and applicant_id:
        member = guild.get_member(applicant_id)
        if member is not None:
            try:
                await member.send(
                    embed=discord.Embed(
                        description=f"✅ {voucher_mention} accepted your vouch on your "
                        f"**{guild.name}** application.",
                        color=OK,
                    )
                )
            except discord.HTTPException:
                pass

    await send_log(
        guild,
        store,
        f"🤝 Vouch {'accepted' if accepted else 'declined'} — {voucher_mention} for <@{applicant_id}>.",
        category="eoi",
    )


async def _request_vouch(
    guild: discord.Guild,
    store: dict[str, Any],
    eoi_msg: discord.Message,
    applicant: discord.abc.User,
    voucher: discord.abc.User,
) -> bool:
    """DM `voucher` asking them to vouch for `applicant`'s EOI, and flag the EOI
    post as pending. Returns False (storing nothing) if they can't be DM'd."""
    dm_embed = discord.Embed(
        title="🤝  Vouch request",
        description=(
            f"{applicant.mention} put in an application to join **{guild.name}** "
            f"and picked **you** to vouch for them.\n\n"
            "Accept and your name is shown on their application for everyone voting to see."
        ),
        color=EMBED_COLOR,
    )
    dm_embed.set_thumbnail(url=applicant.display_avatar.url)
    _brand(dm_embed, guild)
    dm_view = _vouch_dm_view(guild.id, eoi_msg.id, voucher.id)
    try:
        await voucher.send(embed=dm_embed, view=dm_view)
    except discord.Forbidden:
        return False

    store.setdefault("eoi_vouches", {})[str(eoi_msg.id)] = {
        "voucher_id": voucher.id,
        "applicant_id": applicant.id,
        "channel_id": eoi_msg.channel.id,
        "status": "pending",
    }
    save_data(DATA)

    try:
        e = eoi_msg.embeds[0]
        _set_embed_field(e, _EOI_VOUCH_FIELD, f"⏳ Pending — {voucher.mention}", inline=True)
        await eoi_msg.edit(embed=e, view=EOIView())
    except (discord.HTTPException, IndexError):
        pass
    return True


@tasks.loop(minutes=1)
async def eoi_timer_loop() -> None:
    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    for guild in bot.guilds:
        store = guild_store(DATA, guild.id)
        for mid, rec in list(store.get("eoi_timers", {}).items()):
            if rec.get("done") or not rec.get("ends") or rec["ends"] > now:
                continue
            rec["done"] = True
            save_data(DATA)
            await _close_eoi(guild, store, int(mid), rec)


@eoi_timer_loop.before_loop
async def _before_eoi_loop() -> None:
    await bot.wait_until_ready()


@eoi_timer_loop.error
async def _eoi_loop_error(exc: BaseException) -> None:
    log.exception("EOI timer loop error: %s", exc)


# --------------------------------------------------------------------------- #
# Self-serve role menu (persistent dropdown)
# --------------------------------------------------------------------------- #

class SelfRoleSelect(discord.ui.Select):
    """Dropdown that gives/removes the menu's roles based on the viewer's picks.

    Whatever a member selects from this menu becomes their set of those roles:
    selected ones are added, menu roles they leave unselected are removed. The
    authoritative list of menu roles is read live from the guild's self_roles
    config, so the handler keeps working after a restart.
    """

    def __init__(self, options: list[discord.SelectOption] | None = None) -> None:
        opts = options or [discord.SelectOption(label="—", value="0")]
        super().__init__(
            custom_id="selfrole:menu",
            placeholder="Select your roles…",
            min_values=0,
            max_values=len(opts),
            options=opts,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        store = guild_store(DATA, interaction.guild_id)
        menu_ids = [r["id"] for r in store.get("self_roles", [])]
        if not menu_ids:
            await interaction.followup.send(
                "This role menu isn't set up anymore.", ephemeral=True
            )
            return

        selected = {int(v) for v in self.values if v.isdigit()}
        member = interaction.user
        added, removed, failed = [], [], []
        for rid in menu_ids:
            role = interaction.guild.get_role(rid)
            if role is None:
                continue
            has = role in member.roles
            want = rid in selected
            try:
                if want and not has:
                    await member.add_roles(role, reason="Self-role menu")
                    added.append(role.mention)
                elif not want and has:
                    await member.remove_roles(role, reason="Self-role menu")
                    removed.append(role.mention)
            except discord.HTTPException:
                failed.append(role.mention)

        parts = []
        if added:
            parts.append("➕ " + ", ".join(added))
        if removed:
            parts.append("➖ " + ", ".join(removed))
        if failed:
            parts.append(
                "⚠️ Couldn't change " + ", ".join(failed) + " (check my role position)"
            )
        await interaction.followup.send(
            "  •  ".join(parts) if parts else "No changes.", ephemeral=True
        )
        if added or removed:
            fields = []
            if added:
                fields.append(("Added", ", ".join(added), False))
            if removed:
                fields.append(("Removed", ", ".join(removed), False))
            await send_log(
                interaction.guild,
                store,
                "🎭 Self-roles updated.",
                category="members",
                actor=interaction.user,
                fields=fields,
            )


class SelfRoleView(discord.ui.View):
    def __init__(self, options: list[discord.SelectOption] | None = None) -> None:
        super().__init__(timeout=None)
        self.add_item(SelfRoleSelect(options))


# template instance registered on startup so the dropdown survives restarts
SELF_ROLE_VIEW = SelfRoleView()


# --------------------------------------------------------------------------- #
# Command groups
# --------------------------------------------------------------------------- #

roster_group = app_commands.Group(name="roster", description="Manage the gang roster")
kos_group = app_commands.Group(name="kos", description="Manage the Kill-On-Sight list")
role_menu_group = app_commands.Group(
    name="role_menu", description="Self-serve role selection menu"
)
radio_group = app_commands.Group(
    name="radio", description="Random gang radio frequency"
)


# export everything (incl. _helpers) to the modules that build on this one
__all__ = [n for n in list(globals()) if not n.startswith("__")]
