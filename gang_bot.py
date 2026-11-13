"""
Gang Manager — FiveM gang management bot (entrypoint).

Run with `python gang_bot.py`. Features live in the tenshi_bot package:
  tenshi_bot/core.py — Config, data store, the bot object, shared helpers (logging, branding, V2 cards), EOI + role-menu views and command groups.
  tenshi_bot/roster.py — /roster — live roster from rank roles, ranks, import_roles, add/remove/reset.
  tenshi_bot/kos.py — /kos — KOS list, sign-up → draw → staff approval → announce flow.
  tenshi_bot/settings.py — /settings — channel & role configuration.
  tenshi_bot/panels.py — /role_menu, /radio, /eoi, /eoi_post and /gang_help.
  tenshi_bot/tickets.py — Ticket / order system: panels, tickets, transcripts, priority, auto-close, stats.
  tenshi_bot/gang.py — /gang dashboard & profile, /strike, /war (live scoreboards).
"""

from tenshi_bot.core import *  # noqa: F401,F403
from tenshi_bot.roster import *  # noqa: F401,F403
from tenshi_bot.kos import *  # noqa: F401,F403
from tenshi_bot.settings import *  # noqa: F401,F403
from tenshi_bot.panels import *  # noqa: F401,F403
from tenshi_bot.tickets import *  # noqa: F401,F403
from tenshi_bot.gang import *  # noqa: F401,F403
from tenshi_bot.billing import *  # noqa: F401,F403
from tenshi_bot.web import *  # noqa: F401,F403


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #

bot.tree.add_command(roster_group)
bot.tree.add_command(kos_group)
bot.tree.add_command(role_menu_group)
bot.tree.add_command(radio_group)
bot.tree.add_command(ticket_group)
bot.tree.add_command(strike_group)
bot.tree.add_command(war_group)
bot.tree.add_command(gang_group)
bot.tree.add_command(settings_group)
bot.tree.add_command(subscription_group)

# Paywall: block paid commands in a server whose subscription has lapsed.
# No-op unless BILLING_ENABLED is set (see billing.py).
bot.tree.interaction_check = subscription_gate


# --------------------------------------------------------------------------- #
# Live status — rotates every 30s through real numbers
# --------------------------------------------------------------------------- #

def _status_activities() -> list[discord.BaseActivity]:
    """The presence rotation, built fresh each tick off live numbers.

    Clean custom lines (no verb) so they read as one voice — "managing 12,400
    members", "3 gang wars live". Counts only subscribed servers, so the figures
    match what members actually see. Set BOT_STATUS to pin a line of your own.
    """
    active = [g for g in bot.guilds if guild_active(g.id)]
    members = sum(g.member_count or 0 for g in active)
    tickets = 0
    wars = 0
    for g in active:
        store = guild_store(DATA, g.id)
        tickets += sum(1 for o in store.get("orders", []) if o.get("status", "open") == "open")
        wars += sum(1 for w in store.get("wars", []) if w.get("active", True))

    def line(text: str) -> discord.CustomActivity:
        return discord.CustomActivity(name=text[:128])

    acts: list[discord.BaseActivity] = []
    custom = (os.getenv("BOT_STATUS") or "").strip()
    if custom:
        acts.append(line(custom))
    if members:
        acts.append(line(f"👥 managing {members:,} member{'s' if members != 1 else ''}"))
    if active:
        acts.append(line(f"🏴 managing {len(active)} gang{'s' if len(active) != 1 else ''}"))
    if wars:
        acts.append(line(f"⚔️ {wars} gang war{'s' if wars != 1 else ''} live"))
    if tickets:
        acts.append(line(f"🎫 {tickets} open ticket{'s' if tickets != 1 else ''}"))
    acts.append(line("🥷 /gang_help"))
    return acts


_status_i = 0


@tasks.loop(seconds=30)
async def status_loop() -> None:
    global _status_i
    try:
        acts = _status_activities()
        if not acts:
            return
        act = acts[_status_i % len(acts)]
        _status_i += 1
        await bot.change_presence(status=discord.Status.online, activity=act)
    except Exception as exc:  # noqa: BLE001 — a status blip must never stop the loop
        log.debug("status update skipped: %s", exc)


@status_loop.before_loop
async def _before_status_loop() -> None:
    await bot.wait_until_ready()


@bot.listen("on_interaction")
async def _trace_interaction(interaction: discord.Interaction) -> None:
    # slim trace (debug level): which component was clicked, by whom
    if interaction.type == discord.InteractionType.component:
        data = interaction.data or {}
        log.debug(
            "component: custom_id=%s user=%s", data.get("custom_id"), interaction.user
        )


@bot.event
async def on_ready() -> None:
    # register persistent views so buttons/menus survive restarts
    bot.add_view(EOI_VIEW)
    bot.add_view(EOIPromptView())
    bot.add_view(SELF_ROLE_VIEW)
    bot.add_view(RADIO_PANEL_VIEW)
    bot.add_view(TICKET_PANEL_VIEW)
    bot.add_view(ORDER_TICKET_VIEW)
    bot.add_view(MUSTER_VIEW)
    bot.add_view(STAFF_DRAW_VIEW)
    bot.add_view(ROSTER_PROPOSAL_VIEW)
    bot.add_view(ROSTER_REFRESH_VIEW)
    bot.add_view(RatingView())
    bot.add_view(DashboardView())
    bot.add_view(WarPanelView())
    bot.add_view(HelpView())
    bot.add_dynamic_items(VouchDecisionButton)
    if not status_loop.is_running():
        status_loop.start()
    if not ticket_autoclose_loop.is_running():
        ticket_autoclose_loop.start()
    if not eoi_timer_loop.is_running():
        eoi_timer_loop.start()
    # Recognise whoever owns the bot application as the operator (no env needed).
    await detect_app_owners()
    await start_web_server()
    # Stripe is the source of truth for payments: re-apply live subscriptions in
    # the background, so a reset data file never locks out paying servers.
    async def _stripe_heal():
        try:
            await sync_subscriptions_from_stripe()
        except Exception as exc:
            log.error("Stripe sync failed: %s", exc)
    asyncio.create_task(_stripe_heal())
    try:
        synced = await bot.tree.sync()
        log.info("Synced %d slash commands.", len(synced))
    except discord.DiscordException as exc:
        log.error("Command sync failed: %s", exc)
    log.info("Logged in as %s (id: %s)", bot.user, bot.user.id)
    log.info(
        "discord.py %s · Components V2 available: %s",
        discord.__version__,
        hasattr(discord.ui, "LayoutView"),
    )
    log.info(
        "In %d guild(s): %s",
        len(bot.guilds),
        ", ".join(f"{g.name} ({g.id})" for g in bot.guilds) or "none",
    )
    # enforce the server allowlist on startup, if one is configured
    if ALLOWED_GUILD_IDS:
        for g in list(bot.guilds):
            if g.id not in ALLOWED_GUILD_IDS:
                log.warning("Leaving unauthorized guild %s (%s)", g.name, g.id)
                try:
                    await g.leave()
                except discord.HTTPException:
                    pass


@bot.event
async def on_guild_join(guild: discord.Guild) -> None:
    # if an allowlist is set, immediately leave any server not on it
    if ALLOWED_GUILD_IDS and guild.id not in ALLOWED_GUILD_IDS:
        log.warning("Leaving unauthorized guild %s (%s)", guild.name, guild.id)
        try:
            await guild.leave()
        except discord.HTTPException:
            pass
        return

    # A subscription is required BEFORE the bot can be added. The website only
    # offers "Add bot" for subscribed servers; this catches copied invite links.
    # Servers owned by the bot owner are allowed in so they can be comped.
    if BILLING_ENABLED and not peek_active(guild.id) and not is_owner(getattr(guild, "owner_id", None)):
        log.info("Leaving %s (%s): no active subscription", guild.name, guild.id)
        site = web_base_url()
        where = f" at {site}/panel" if site else " on the Gang Manager website"
        note = (
            "👋 Thanks for adding **Gang Manager**! This server doesn't have an active "
            f"subscription yet, so I've left. Subscribe for this server{where}, then add me "
            "again from there."
        )
        channel = guild.system_channel
        me = guild.me
        if channel is None or (me is not None and not channel.permissions_for(me).send_messages):
            channel = next(
                (c for c in guild.text_channels if me is not None and c.permissions_for(me).send_messages),
                None,
            )
        if channel is not None:
            try:
                await channel.send(note)
            except discord.HTTPException:
                pass
        try:
            await guild.leave()
        except discord.HTTPException:
            pass


@bot.event
async def on_member_join(member: discord.Member) -> None:
    guild = member.guild
    if not guild_active(guild.id):
        return  # no welcome/logs until the server is subscribed
    store = guild_store(DATA, guild.id)

    # public welcome message
    wid = store.get("welcome_channel_id")
    wchannel = guild.get_channel(wid) if wid else None
    if wchannel is not None:
        submit = discord.utils.get(guild.text_channels, name="submit-eois")
        where = submit.mention if submit is not None else "#submit-eois"
        embed = discord.Embed(
            title=f"👋  Welcome to {guild.name}!",
            description=(
                f"Hey {member.mention}, glad to have you.\n"
                f"Grab your roles, read the rules, and when you want to join up head to "
                f"{where} to submit your application."
            ),
            color=EMBED_COLOR,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Member #{guild.member_count}")
        try:
            await _send_card(
                wchannel,
                [embed],
                header=member.mention,
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        except discord.HTTPException:
            pass

    # join log
    created = int(member.created_at.timestamp())
    await send_log(
        guild,
        store,
        f"📥 {member.mention} **joined**.",
        category="joins",
        fields=[
            ("User", f"`{member}` · `{member.id}`", True),
            ("Account made", f"<t:{created}:R>", True),
            ("Member #", str(guild.member_count), True),
        ],
    )


@bot.event
async def on_member_remove(member: discord.Member) -> None:
    guild = member.guild
    if not guild_active(guild.id):
        return
    store = guild_store(DATA, guild.id)
    joined = member.joined_at
    roles = [r.mention for r in member.roles if r.name != "@everyone"]
    fields = [
        ("User", f"`{member}` · `{member.id}`", True),
        ("Member #", str(guild.member_count), True),
    ]
    if joined is not None:
        fields.append(("Joined", f"<t:{int(joined.timestamp())}:R>", True))
    if roles:
        fields.append(("Roles", " ".join(roles)[:1024], False))
    await send_log(
        guild,
        store,
        f"📤 **{member}** left.",
        category="joins",
        fields=fields,
    )


def main() -> None:
    if not TOKEN:
        raise SystemExit(
            "DISCORD_TOKEN is not set. Add it as an environment variable."
        )
    try:
        bot.run(TOKEN, log_handler=None)
    except discord.PrivilegedIntentsRequired:
        log.error(
            "Server Members Intent is NOT enabled. Turn it on in the Discord "
            "Developer Portal → your app → Bot → Privileged Gateway Intents → "
            "Server Members Intent, then redeploy."
        )
        raise


if __name__ == "__main__":
    main()
