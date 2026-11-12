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


@bot.listen("on_interaction")
async def _trace_interaction(interaction: discord.Interaction) -> None:
    # slim trace: confirm component clicks arrive and with what custom_id
    if interaction.type == discord.InteractionType.component:
        data = interaction.data or {}
        log.info(
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
    try:
        await bot.change_presence(activity=discord.CustomActivity(name="winks fat"))
    except discord.DiscordException:
        pass
    if not ticket_autoclose_loop.is_running():
        ticket_autoclose_loop.start()
    if not eoi_timer_loop.is_running():
        eoi_timer_loop.start()
    await start_web_server()
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


@bot.event
async def on_member_join(member: discord.Member) -> None:
    guild = member.guild
    store = guild_store(DATA, guild.id)

    # public welcome message
    wid = store.get("welcome_channel_id")
    wchannel = guild.get_channel(wid) if wid else None
    if wchannel is not None:
        embed = discord.Embed(
            title=f"👋  Welcome to {guild.name}!",
            description=(
                f"Hey {member.mention}, glad to have you.\n"
                "Grab your roles, read the rules, and if you want to join up use "
                "`/eoi` to apply."
            ),
            color=EMBED_COLOR,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Member #{guild.member_count}")
        try:
            await wchannel.send(
                content=member.mention,
                embed=embed,
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
