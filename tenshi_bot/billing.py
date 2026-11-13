"""Per-server subscriptions and the paywall gate.

A server's subscription lives in its store under ``subscription`` (see
``guild_store`` in core.py). Ways a server becomes active:

* **Stripe** — set ``STRIPE_SECRET_KEY`` / ``STRIPE_PRICE_ID`` /
  ``STRIPE_WEBHOOK_SECRET``; the Subscribe button takes real monthly payments and
  the webhook (web.py) keeps the paid-until date in sync with Stripe.
* **Manual** — the bot operator comps a server with ``/subscription grant`` or
  the panel's owner tools (e.g. ♾ Lifetime for your own gang's server).

The paywall is the same everywhere: without an active subscription, slash
commands (except /subscription, /settings, /help), buttons and menus,
welcome messages and the background jobs all pause in that server.

The bot operator is detected automatically from the Discord application (the
account/team that owns the bot in the Developer Portal), plus anyone listed in
``BOT_OWNER_IDS``. Operators can comp servers and open any server's web panel,
but get no special pass inside Discord — so testing shows exactly what a
customer sees. Subscriptions are required by default (``BILLING_ENABLED=false``
turns the paywall off).
"""

import datetime

from .core import *  # noqa: F401,F403

__all__ = [
    "PLANS",
    "DEFAULT_PLAN",
    "get_subscription",
    "subscription_state",
    "effective_state",
    "is_active",
    "peek_active",
    "grant",
    "start_trial",
    "expire",
    "move_subscription",
    "is_owner",
    "detect_app_owners",
    "guild_for_sub",
    "web_base_url",
    "note_base_url",
    "subscription_gate",
    "subscription_group",
    "GATE_EXEMPT_PREFIXES",
]

# Plans people can buy. ``days`` is how long one purchase/renewal lasts.
PLANS = {
    "monthly": {"label": "Monthly", "days": 30, "price": "$7.99 / month"},
}
DEFAULT_PLAN = "monthly"

# Command (qualified) names that stay usable even when a server is NOT subscribed,
# so staff can still check status, finish setup, and subscribe.
GATE_EXEMPT_PREFIXES = ("subscription", "settings", "help")

# Discord user ids that own the bot application (filled by detect_app_owners()).
_APP_OWNERS: set[int] = set()
# Last public site URL we saw a visitor use (fallback for links in Discord).
_SEEN_BASE = ""


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_dt(value):
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


# --------------------------------------------------------------------------- #
# Who runs the bot
# --------------------------------------------------------------------------- #

def is_owner(user_id) -> bool:
    """True for the bot operator: the application's owner (or team members) and
    anyone in BOT_OWNER_IDS."""
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return False
    return uid in BOT_OWNER_IDS or uid in _APP_OWNERS


async def detect_app_owners() -> set[int]:
    """Read the application's owner (or team) from Discord so the person who owns
    the bot is recognised automatically — no env var needed."""
    try:
        app = await bot.application_info()
    except Exception as exc:  # never block startup on this
        log.warning("Couldn't read the application owner: %s", exc)
        return set(_APP_OWNERS)
    ids: set[int] = set()
    team = getattr(app, "team", None)
    if team is not None:
        for m in getattr(team, "members", None) or []:
            try:
                ids.add(int(m.id))
            except (TypeError, ValueError, AttributeError):
                pass
    owner = getattr(app, "owner", None)
    if owner is not None and team is None:
        ids.add(int(owner.id))
    _APP_OWNERS.clear()
    _APP_OWNERS.update(ids)
    log.info("Bot operator(s): %s", ", ".join(str(i) for i in sorted(ids | BOT_OWNER_IDS)) or "none found")
    return set(ids)


# --------------------------------------------------------------------------- #
# Subscription state
# --------------------------------------------------------------------------- #

def get_subscription(store: dict) -> dict:
    """The raw subscription dict for a store, created with defaults if missing."""
    sub = store.get("subscription")
    if not isinstance(sub, dict):
        sub = {
            "status": "none",
            "plan": None,
            "expires_at": None,
            "trial_used": False,
            "customer_id": None,
            "sub_id": None,
            "updated_at": None,
        }
        store["subscription"] = sub
    return sub


def subscription_state(guild_id: int) -> dict:
    """The stored subscription, resolved (what was bought / granted).

    Lazily flips an elapsed subscription to ``expired`` and persists that. For
    "may this server use the bot right now", use :func:`effective_state`.
    """
    store = guild_store(DATA, guild_id)
    sub = get_subscription(store)
    status = sub.get("status", "none")
    exp = _parse_dt(sub.get("expires_at"))
    now = _now()
    lifetime = status in ("active", "trial") and sub.get("expires_at") is None

    if status in ("active", "trial"):
        if exp is None:  # lifetime grant
            active, days_left = True, None
        else:
            active = exp > now
            days_left = max(0, (exp - now).days + (1 if exp > now else 0))
            if not active:
                sub["status"] = "expired"
                sub["updated_at"] = now.isoformat()
                save_data(DATA)
                status = "expired"
    else:
        active, days_left = False, 0

    return {
        "active": bool(active),
        "status": status,
        "plan": sub.get("plan"),
        "expires_at": sub.get("expires_at"),
        "days_left": days_left,
        "trial": status == "trial",
        "trial_used": bool(sub.get("trial_used")),
        "lifetime": lifetime,
        "customer_id": sub.get("customer_id"),
        "sub_id": sub.get("sub_id"),
        "billing_enabled": BILLING_ENABLED,
    }


def effective_state(guild_id: int) -> dict:
    """subscription_state plus the real answer to "can this server use the bot":
    ``mode`` is one of free (paywall off) · lifetime · paid · none."""
    s = subscription_state(guild_id)
    if not BILLING_ENABLED:
        s["active"], s["mode"] = True, "free"
    elif s["active"]:
        s["mode"] = "lifetime" if s["lifetime"] else "paid"
    else:
        s["mode"] = "none"
    return s


def is_active(guild_id: int) -> bool:
    """True if this server may use paid features right now."""
    return effective_state(guild_id)["active"]


def peek_active(guild_id) -> bool:
    """Like is_active, but read-only: never creates a data entry. Used for
    servers the bot isn't in yet (the server picker, invites, new joins)."""
    if not BILLING_ENABLED:
        return True
    g = (DATA.get("guilds") or {}).get(str(guild_id))
    sub = (g or {}).get("subscription") or {}
    if sub.get("status") not in ("active", "trial"):
        return False
    exp = _parse_dt(sub.get("expires_at"))
    return exp is None or exp > _now()


def grant(
    guild_id: int,
    *,
    days: int | None = None,
    until: "datetime.datetime | None" = None,
    plan: str = DEFAULT_PLAN,
    status: str = "active",
    customer_id: str | None = None,
    sub_id: str | None = None,
    purchaser_id: str | int | None = None,
    lifetime: bool = False,
) -> dict:
    """Activate or extend a server's subscription.

    * ``days`` — add that many days (from the current end if still in the
      future, else from now). Used for manual grants.
    * ``until`` — make sure the server is paid up to at least this moment. It
      only ever moves the end date forward, so replaying the same Stripe event
      (or receiving two events for one payment) never double-counts.
    * ``lifetime`` — no end date.
    """
    store = guild_store(DATA, guild_id)
    sub = get_subscription(store)
    now = _now()
    currently_lifetime = sub.get("status") in ("active", "trial") and sub.get("expires_at") is None
    if lifetime:
        sub["expires_at"] = None
    elif until is not None:
        if not currently_lifetime:  # never downgrade a lifetime grant
            target = until if until.tzinfo else until.replace(tzinfo=datetime.timezone.utc)
            cur = _parse_dt(sub.get("expires_at"))
            if cur is not None and cur > target:
                target = cur
            sub["expires_at"] = target.isoformat()
    elif days:
        base = _parse_dt(sub.get("expires_at"))
        start = base if (base and base > now) else now
        sub["expires_at"] = (start + datetime.timedelta(days=days)).isoformat()
    sub["status"] = status
    sub["plan"] = plan
    if customer_id:
        sub["customer_id"] = customer_id
    if sub_id:
        sub["sub_id"] = sub_id
    if purchaser_id:
        sub["purchaser_id"] = str(purchaser_id)
    sub["updated_at"] = now.isoformat()
    save_data(DATA)
    log.info("subscription grant guild=%s plan=%s lifetime=%s days=%s until=%s",
             guild_id, plan, lifetime, days, until)
    return effective_state(guild_id)


def start_trial(guild_id: int, days: int | None = None) -> dict | None:
    """Optional one-time trial (off by default — TRIAL_DAYS=0). Returns None if
    already used or disabled."""
    store = guild_store(DATA, guild_id)
    sub = get_subscription(store)
    if sub.get("trial_used"):
        return None
    days = TRIAL_DAYS if days is None else days
    if days <= 0:
        return None
    sub["trial_used"] = True
    save_data(DATA)
    return grant(guild_id, days=days, plan="trial", status="trial")


def expire(guild_id: int) -> dict:
    """Mark a server's subscription expired (cancelled / ended)."""
    store = guild_store(DATA, guild_id)
    sub = get_subscription(store)
    sub["status"] = "expired"
    sub["updated_at"] = _now().isoformat()
    save_data(DATA)
    log.info("subscription expired guild=%s", guild_id)
    return effective_state(guild_id)


def move_subscription(from_guild_id, to_guild_id) -> "dict | None":
    """Move an active subscription from one server to another — no new charge.

    Copies the Stripe linkage (sub_id / customer_id), plan, purchaser and the
    paid-through date (or lifetime flag) onto ``to_guild_id``, then releases
    ``from_guild_id`` and unlinks its Stripe ids so a replayed renewal can never
    re-activate the old server. Returns the destination's new state, or ``None``
    if the source isn't actually active or the two ids are the same.

    The caller is responsible for repointing the Stripe subscription's
    ``metadata.guild_id`` at the destination (so future invoices follow it).
    """
    try:
        src_gid, dst_gid = int(from_guild_id), int(to_guild_id)
    except (TypeError, ValueError):
        return None
    if src_gid == dst_gid:
        return None
    if not subscription_state(src_gid)["active"]:
        return None

    src = get_subscription(guild_store(DATA, src_gid))
    lifetime = src.get("expires_at") is None
    until = _parse_dt(src.get("expires_at"))
    plan = src.get("plan") or DEFAULT_PLAN
    customer_id = src.get("customer_id")
    sub_id = src.get("sub_id")
    purchaser_id = src.get("purchaser_id")

    grant(
        dst_gid,
        until=None if lifetime else until,
        lifetime=lifetime,
        plan=plan,
        status="active",
        customer_id=customer_id,
        sub_id=sub_id,
        purchaser_id=purchaser_id,
    )
    expire(src_gid)
    # The old server no longer carries this Stripe subscription.
    src["sub_id"] = None
    src["customer_id"] = None
    src["updated_at"] = _now().isoformat()
    save_data(DATA)
    log.info("subscription moved guild=%s -> guild=%s (sub=%s)", src_gid, dst_gid, sub_id)
    return effective_state(dst_gid)


def guild_for_sub(sub_id, exclude_guild=None):
    """Return a guild id (other than exclude_guild) whose ACTIVE subscription is
    powered by this Stripe subscription id — used to enforce one server per sub.
    """
    if not sub_id:
        return None
    for gid_s, g in (DATA.get("guilds") or {}).items():
        sub = g.get("subscription") or {}
        if sub.get("sub_id") != sub_id:
            continue
        try:
            gid = int(gid_s)
        except (TypeError, ValueError):
            continue
        if exclude_guild is not None and gid == int(exclude_guild):
            continue
        if subscription_state(gid)["active"]:
            return gid
    return None


# --------------------------------------------------------------------------- #
# Public site URL (for links the bot posts in Discord)
# --------------------------------------------------------------------------- #

def note_base_url(url: str) -> None:
    """Remember the site URL visitors use (fallback when nothing is configured)."""
    global _SEEN_BASE
    if url:
        _SEEN_BASE = url.rstrip("/")


def web_base_url() -> str:
    """PUBLIC_URL / WEB_URL if set, else Railway's own public domain, else the
    last URL a visitor used. Empty if none known yet."""
    base = (os.getenv("PUBLIC_URL") or os.getenv("WEB_URL") or "").strip()
    if not base:
        dom = (os.getenv("RAILWAY_PUBLIC_DOMAIN") or "").strip()
        if dom:
            base = dom if dom.startswith("http") else f"https://{dom}"
    return (base or _SEEN_BASE).rstrip("/")


def _renew_hint(guild_id: int) -> str:
    base = web_base_url()
    if base:
        return f"\n\nSubscribe or manage it here: {base}/panel"
    return "\n\nThe server owner can subscribe from the Gang Manager website."


# --------------------------------------------------------------------------- #
# The gate — blocks paid commands in an unsubscribed server
# --------------------------------------------------------------------------- #

async def subscription_gate(interaction: "discord.Interaction") -> bool:
    """The paywall check for slash commands AND buttons/menus (GatedView).

    Exempt: DMs and the setup/subscription/help commands — so an unsubscribed
    server can still check status, set up and subscribe. No-op when billing is
    off. Nobody gets a personal pass, so what you test is what customers get.
    """
    if not BILLING_ENABLED:
        return True
    guild = interaction.guild
    if guild is None:  # DMs (e.g. rating a closed ticket)
        return True
    cmd = interaction.command
    qname = getattr(cmd, "qualified_name", "") if cmd is not None else ""
    if any(qname == p or qname.startswith(p + " ") for p in GATE_EXEMPT_PREFIXES):
        return True
    if is_active(guild.id):
        return True

    state = subscription_state(guild.id)
    word = "has expired" if state["status"] == "expired" else "isn't active yet"
    msg = (
        "🔒 **Gang Manager isn't active in this server.**\n"
        f"This server's subscription {word}." + _renew_hint(guild.id)
    )
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except discord.HTTPException:
        pass
    return False


# --------------------------------------------------------------------------- #
# /subscription commands
# --------------------------------------------------------------------------- #

subscription_group = app_commands.Group(
    name="subscription",
    description="View or manage this server's Gang Manager subscription",
)


def _fmt_state(guild: "discord.Guild", state: dict) -> discord.Embed:
    mode = state.get("mode")
    status = state["status"]
    if mode == "free":
        head, color = "✅ Active (billing is off — free mode)", 0x22C55E
    elif mode == "lifetime":
        head, color = "✅ Active — lifetime", 0x22C55E
    elif state["active"]:
        head, color = f"✅ Active — {state['days_left']} day(s) left", 0x22C55E
    elif status == "expired":
        head, color = "🔒 Expired", 0xEF4444
    else:
        head, color = "🔒 Not subscribed", 0x6B7280

    embed = discord.Embed(title="💳  Subscription", description=head, color=color)
    plan = state.get("plan")
    if mode != "free":
        if plan and plan in PLANS:
            embed.add_field(name="Plan", value=f"{PLANS[plan]['label']} — {PLANS[plan]['price']}", inline=True)
        elif plan:
            embed.add_field(name="Plan", value=str(plan).title(), inline=True)
        exp = _parse_dt(state.get("expires_at"))
        if exp is not None and state["active"]:
            embed.add_field(name="Paid until", value=f"<t:{int(exp.timestamp())}:D>", inline=True)
    base = web_base_url()
    if base and mode in ("none", "paid"):
        embed.add_field(name="Subscribe / manage", value=f"[Open the panel]({base}/panel)", inline=False)
    return _brand(embed, guild)


@subscription_group.command(name="status", description="Show this server's subscription status")
async def subscription_status(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message("Run this in a server.", ephemeral=True)
        return
    state = effective_state(interaction.guild.id)
    await interaction.response.send_message(embed=_fmt_state(interaction.guild, state), ephemeral=True)


def _target_gid(interaction: discord.Interaction, server_id: str | None):
    if server_id:
        try:
            return int(server_id)
        except ValueError:
            return None
    return interaction.guild.id if interaction.guild is not None else None


@subscription_group.command(name="grant", description="[Bot owner] Activate/extend a server's subscription")
@app_commands.describe(
    days="Days to add (ignored with lifetime:true)",
    plan="Plan label to record (default monthly)",
    lifetime="Grant forever instead of for a number of days",
    server_id="Target server ID (defaults to this server)",
)
async def subscription_grant(
    interaction: discord.Interaction,
    days: int = 30,
    plan: str = DEFAULT_PLAN,
    lifetime: bool = False,
    server_id: str | None = None,
) -> None:
    if not is_owner(interaction.user.id):
        await interaction.response.send_message(
            "Only the bot owner (the account that owns the bot application) can do this.", ephemeral=True
        )
        return
    gid = _target_gid(interaction, server_id)
    if gid is None:
        await interaction.response.send_message("Pass a valid `server_id` or run this in the server.", ephemeral=True)
        return
    state = grant(gid, days=(None if lifetime else max(0, days)), plan=plan, status="active", lifetime=lifetime)
    target = bot.get_guild(gid)
    if target is not None:
        try:
            await send_log(target, guild_store(DATA, gid), f"💳 Subscription granted by the bot owner ({plan}).",
                           category="web")
        except discord.HTTPException:
            pass
    name = target.name if target is not None else f"`{gid}`"
    txt = f"✅ Granted **{plan}** to {name}."
    if target is not None:
        await interaction.response.send_message(txt, embed=_fmt_state(target, state), ephemeral=True)
    else:
        await interaction.response.send_message(
            txt + " (The bot isn't in that server yet — it applies as soon as it joins.)", ephemeral=True
        )


@subscription_group.command(name="cancel", description="[Bot owner] Expire a server's subscription now")
@app_commands.describe(server_id="Target server ID (defaults to this server)")
async def subscription_cancel(interaction: discord.Interaction, server_id: str | None = None) -> None:
    if not is_owner(interaction.user.id):
        await interaction.response.send_message("Only the bot owner can do this.", ephemeral=True)
        return
    gid = _target_gid(interaction, server_id)
    if gid is None:
        await interaction.response.send_message("Pass a valid `server_id` or run this in the server.", ephemeral=True)
        return
    state = expire(gid)
    target = bot.get_guild(gid)
    if target is not None:
        await interaction.response.send_message("🔒 Subscription expired.", embed=_fmt_state(target, state),
                                                ephemeral=True)
    else:
        await interaction.response.send_message("🔒 Subscription expired.", ephemeral=True)


# --------------------------------------------------------------------------- #
# Install the paywall: buttons/menus (GatedView), background jobs and events
# in core.py consult these. Slash commands use subscription_gate directly via
# bot.tree.interaction_check (gang_bot.py).
# --------------------------------------------------------------------------- #
from . import core as _core  # noqa: E402

_core._PAYWALL = subscription_gate
_core._ACTIVE = is_active
