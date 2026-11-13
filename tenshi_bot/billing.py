"""Per-server subscriptions and the paywall gate.

A server's subscription lives in its store under ``subscription`` (see
``guild_store`` in core.py). Two ways to activate a server:

* **Stripe mode** — set ``STRIPE_SECRET_KEY`` / ``STRIPE_PRICE_ID`` /
  ``STRIPE_WEBHOOK_SECRET`` and the Subscribe button on the website takes real
  monthly payments; the webhook (in web.py) calls :func:`grant` / :func:`expire`.
* **Manual mode** — the bot operator runs ``/subscription grant`` after an
  off-platform payment. No Stripe needed.

When ``BILLING_ENABLED`` is off (the default) every server counts as active, so
existing installs are never locked out. Flip it on to require a subscription.
"""

import datetime

from .core import *  # noqa: F401,F403

__all__ = [
    "PLANS",
    "DEFAULT_PLAN",
    "get_subscription",
    "subscription_state",
    "is_active",
    "grant",
    "start_trial",
    "expire",
    "is_owner",
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
GATE_EXEMPT_PREFIXES = ("subscription", "settings", "gang_help")


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
    """Resolved, read-friendly view of a server's subscription.

    Lazily flips an elapsed subscription to ``expired`` and persists that.
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


def is_active(guild_id: int) -> bool:
    """True if this server may use paid features (always True when billing off)."""
    if not BILLING_ENABLED:
        return True
    return subscription_state(guild_id)["active"]


def grant(
    guild_id: int,
    *,
    days: int | None = None,
    plan: str = DEFAULT_PLAN,
    status: str = "active",
    customer_id: str | None = None,
    sub_id: str | None = None,
    lifetime: bool = False,
) -> dict:
    """Activate or extend a server's subscription.

    Extends from the current expiry if it's still in the future, otherwise from
    now — so renewing early never loses the days already paid for.
    """
    store = guild_store(DATA, guild_id)
    sub = get_subscription(store)
    now = _now()
    if lifetime:
        sub["expires_at"] = None
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
    sub["updated_at"] = now.isoformat()
    save_data(DATA)
    log.info("subscription grant guild=%s plan=%s lifetime=%s days=%s", guild_id, plan, lifetime, days)
    return subscription_state(guild_id)


def start_trial(guild_id: int, days: int | None = None) -> dict | None:
    """Start the one-time free trial. Returns None if already used or disabled."""
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
    """Mark a server's subscription expired (payment failed / cancelled)."""
    store = guild_store(DATA, guild_id)
    sub = get_subscription(store)
    sub["status"] = "expired"
    sub["updated_at"] = _now().isoformat()
    save_data(DATA)
    log.info("subscription expired guild=%s", guild_id)
    return subscription_state(guild_id)


def is_owner(user_id) -> bool:
    try:
        return int(user_id) in BOT_OWNER_IDS
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------------------- #
# The gate — blocks paid commands in an unsubscribed server
# --------------------------------------------------------------------------- #

def _web_base_url() -> str:
    """Public URL of the site, for 'renew here' links (best-effort)."""
    base = os.getenv("PUBLIC_URL") or os.getenv("WEB_URL") or ""
    return base.rstrip("/")


def _renew_hint(guild_id: int) -> str:
    base = _web_base_url()
    if base:
        return f"\n\nManage your subscription: {base}/pricing"
    return "\n\nAsk the bot owner to renew, or open the Gang Manager website."


async def subscription_gate(interaction: "discord.Interaction") -> bool:
    """CommandTree check: allow the command only if the server is subscribed.

    Exempt: DMs, the owner, and setup/subscription/help commands — so an expired
    server can still check status and resubscribe. No-op when billing is off.
    """
    if not BILLING_ENABLED:
        return True
    guild = interaction.guild
    if guild is None:  # DM commands
        return True
    if is_owner(interaction.user.id):
        return True
    cmd = interaction.command
    qname = getattr(cmd, "qualified_name", "") if cmd is not None else ""
    if any(qname == p or qname.startswith(p + " ") for p in GATE_EXEMPT_PREFIXES):
        return True
    if is_active(guild.id):
        return True

    state = subscription_state(guild.id)
    word = "expired" if state["status"] == "expired" else "isn't set up"
    msg = (
        f"🔒 **Gang Manager isn't active in this server.**\n"
        f"This server's subscription {word}. Staff can start it with "
        f"`/subscription status`." + _renew_hint(guild.id)
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
    status = state["status"]
    if not state["billing_enabled"]:
        head = "✅ Active (billing disabled — free mode)"
        color = 0x22C55E
    elif state["active"] and state["lifetime"]:
        head = "✅ Active — lifetime"
        color = 0x22C55E
    elif state["active"] and state["trial"]:
        head = f"🧪 Free trial — {state['days_left']} day(s) left"
        color = 0x3B82F6
    elif state["active"]:
        head = f"✅ Active — {state['days_left']} day(s) left"
        color = 0x22C55E
    elif status == "expired":
        head = "🔒 Expired"
        color = 0xEF4444
    else:
        head = "🔒 Not subscribed"
        color = 0x6B7280

    embed = discord.Embed(title="💳  Subscription", description=head, color=color)
    plan = state.get("plan")
    if plan and plan in PLANS:
        embed.add_field(name="Plan", value=f"{PLANS[plan]['label']} — {PLANS[plan]['price']}", inline=True)
    elif plan:
        embed.add_field(name="Plan", value=str(plan).title(), inline=True)
    exp = _parse_dt(state.get("expires_at"))
    if exp is not None:
        embed.add_field(name="Renews / ends", value=f"<t:{int(exp.timestamp())}:D>", inline=True)
    elif state["active"]:
        embed.add_field(name="Renews / ends", value="Never (lifetime)", inline=True)
    base = _web_base_url()
    if base and not (state["active"] and state["lifetime"]):
        embed.add_field(name="Manage / subscribe", value=f"[Open the website]({base}/pricing)", inline=False)
    return _brand(embed, guild)


@subscription_group.command(name="status", description="Show this server's subscription status")
async def subscription_status(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message("Run this in a server.", ephemeral=True)
        return
    state = subscription_state(interaction.guild.id)
    await interaction.response.send_message(embed=_fmt_state(interaction.guild, state), ephemeral=True)


@subscription_group.command(name="trial", description="Start this server's one-time free trial")
async def subscription_trial(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("Run this in a server.", ephemeral=True)
        return
    if not (is_leadership(interaction.user) or is_owner(interaction.user.id)):
        await interaction.response.send_message("Only leadership can start the trial.", ephemeral=True)
        return
    state = start_trial(guild.id)
    if state is None:
        await interaction.response.send_message(
            "This server has already used its free trial. Use `/subscription status` to subscribe.",
            ephemeral=True,
        )
        return
    store = guild_store(DATA, guild.id)
    await send_log(guild, store, f"🧪 Free trial started ({state['days_left']} days).",
                   category="web", actor=interaction.user)
    await interaction.response.send_message(embed=_fmt_state(guild, state), ephemeral=True)


@subscription_group.command(name="grant", description="[Owner] Activate/extend a server's subscription")
@app_commands.describe(
    days="Days to add (use 0 with lifetime:true for a permanent grant)",
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
            "This command is for the bot owner only. Set `BOT_OWNER_IDS` to your Discord user ID.",
            ephemeral=True,
        )
        return
    if server_id:
        try:
            gid = int(server_id)
        except ValueError:
            await interaction.response.send_message("`server_id` must be a number.", ephemeral=True)
            return
    elif interaction.guild is not None:
        gid = interaction.guild.id
    else:
        await interaction.response.send_message("Pass a `server_id` or run this in the server.", ephemeral=True)
        return

    state = grant(gid, days=(None if lifetime else max(0, days)), plan=plan, status="active", lifetime=lifetime)
    target = bot.get_guild(gid)
    if target is not None:
        store = guild_store(DATA, gid)
        try:
            await send_log(target, store, f"💳 Subscription granted by owner ({plan}).", category="web")
        except discord.HTTPException:
            pass
    name = target.name if target is not None else f"`{gid}`"
    embed = _fmt_state(target, state) if target is not None else None
    txt = f"✅ Granted **{plan}** to {name}."
    if embed is not None:
        await interaction.response.send_message(txt, embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(txt + " (Bot isn't in that server yet — it'll apply when it joins.)",
                                                ephemeral=True)


@subscription_group.command(name="cancel", description="[Owner] Expire a server's subscription now")
@app_commands.describe(server_id="Target server ID (defaults to this server)")
async def subscription_cancel(interaction: discord.Interaction, server_id: str | None = None) -> None:
    if not is_owner(interaction.user.id):
        await interaction.response.send_message("This command is for the bot owner only.", ephemeral=True)
        return
    if server_id:
        try:
            gid = int(server_id)
        except ValueError:
            await interaction.response.send_message("`server_id` must be a number.", ephemeral=True)
            return
    elif interaction.guild is not None:
        gid = interaction.guild.id
    else:
        await interaction.response.send_message("Pass a `server_id` or run this in the server.", ephemeral=True)
        return
    state = expire(gid)
    target = bot.get_guild(gid)
    embed = _fmt_state(target, state) if target is not None else None
    if embed is not None:
        await interaction.response.send_message("🔒 Subscription expired.", embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message("🔒 Subscription expired.", ephemeral=True)
