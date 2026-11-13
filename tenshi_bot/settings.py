"""/settings — channel & role configuration."""

from .core import *  # noqa: F401,F403
from .roster import *  # noqa: F401,F403
from .kos import *  # noqa: F401,F403


# ---- Settings panel (embed + dropdowns) ------------------------------------- #

# setting value -> (label, store key, kind)
CONFIG_SETTINGS = {
    "leadership_role": ("Leadership role", "leadership_role_id", "role"),
    "log_channel": ("General log channel", "log_channel_id", "channel"),
    "roster_log_channel": ("Roster log channel", "roster_log_channel_id", "channel"),
    "kos_log_channel": ("KOS log channel", "kos_log_channel_id", "channel"),
    "radio_log_channel": ("Radio log channel", "radio_log_channel_id", "channel"),
    "eoi_log_channel": ("Recruitment log channel", "eoi_log_channel_id", "channel"),
    "member_log_channel": ("Role log channel", "member_log_channel_id", "channel"),
    "join_log_channel": ("Join/leave log channel", "join_log_channel_id", "channel"),
    "ticket_log_channel": ("Ticket log channel", "ticket_log_channel_id", "channel"),
    "order_log_channel": ("Order log channel", "order_log_channel_id", "channel"),
    "web_log_channel": ("Website log channel", "web_log_channel_id", "channel"),
    "welcome_channel": ("Welcome channel", "welcome_channel_id", "channel"),
    "staff_channel": ("Staff channel", "staff_channel_id", "channel"),
    "eoi_channel": ("EOI channel", "eoi_channel_id", "channel"),
    "transcript_channel": ("Transcript channel", "transcript_channel_id", "channel"),
    "roster_role": ("Roster default role", "roster_role_id", "role"),
    "kos_role": ("KOS role", "kos_role_id", "role"),
    "radio_ping_role": ("Radio ping role", "radio_ping_role_id", "role"),
    "eoi_ping_role": ("EOI ping role", "eoi_ping_role_id", "role"),
}


_SETTING_EMOJI = {
    "leadership_role": "👑", "roster_role": "🎯", "kos_role": "💀",
    "radio_ping_role": "📻", "eoi_ping_role": "🔔",
    "welcome_channel": "👋", "staff_channel": "🛡️", "eoi_channel": "📥",
    "transcript_channel": "🧾",
    "log_channel": "📝", "roster_log_channel": "📋", "kos_log_channel": "💀",
    "radio_log_channel": "📻", "eoi_log_channel": "📨", "member_log_channel": "🎭",
    "join_log_channel": "🚪", "ticket_log_channel": "🎫", "order_log_channel": "💰",
    "web_log_channel": "🌐",
}
_LOG_KEYS = [k for k in CONFIG_SETTINGS if k.endswith("log_channel")]
_CHANNEL_KEYS = [k for k, v in CONFIG_SETTINGS.items() if v[2] == "channel"]
_ROLE_KEYS = [k for k, v in CONFIG_SETTINGS.items() if v[2] == "role"]
_MAIN_CHANNEL_KEYS = [k for k in _CHANNEL_KEYS if k not in _LOG_KEYS]


def _settings_health(guild: discord.Guild, store: dict[str, Any]) -> list[str]:
    """Things that will actually break features, in plain words."""
    issues: list[str] = []
    me = guild.me
    for key in _CHANNEL_KEYS:
        label, skey, _ = CONFIG_SETTINGS[key]
        cid = store.get(skey)
        if not cid:
            continue
        ch = guild.get_channel(cid)
        if ch is None:
            issues.append(f"{_SETTING_EMOJI[key]} **{label}** points at a deleted channel")
            continue
        if me is not None:
            perms = ch.permissions_for(me)
            if not (perms.view_channel and perms.send_messages and perms.embed_links):
                issues.append(f"{_SETTING_EMOJI[key]} I can't post in {ch.mention} (**{label}**)")
    for key in ("roster_role", "kos_role"):
        label, skey, _ = CONFIG_SETTINGS[key]
        role = guild.get_role(store.get(skey) or 0)
        if role is not None and me is not None and me.top_role <= role:
            issues.append(f"{_SETTING_EMOJI[key]} Drag **Tenshi** above {role.mention} so I can hand it out")
    for key in ("eoi_channel", "staff_channel", "transcript_channel"):
        if not store.get(CONFIG_SETTINGS[key][1]):
            issues.append(f"{_SETTING_EMOJI[key]} **{CONFIG_SETTINGS[key][0]}** isn't set yet")
    return issues


def _settings_cards(guild: discord.Guild, store: dict[str, Any], avatar: str | None) -> list[discord.Embed]:
    def line(key: str) -> str:
        label, skey, kind = CONFIG_SETTINGS[key]
        val = store.get(skey)
        obj = (guild.get_channel(val) if kind == "channel" else guild.get_role(val)) if val else None
        mark = "✅" if obj else "⬜"
        return f"{mark} {_SETTING_EMOJI[key]} {label} — {obj.mention if obj else '*not set*'}"

    done = sum(
        1 for k, (_, skey, kind) in CONFIG_SETTINGS.items()
        if store.get(skey) and (guild.get_channel(store[skey]) if kind == "channel" else guild.get_role(store[skey]))
    )
    total = len(CONFIG_SETTINGS)
    head = discord.Embed(
        title="⚙️  Tenshi — Setup",
        description=f"**Setup progress**\n`{_bar(done, total, 18)}` **{done}/{total}** configured",
        color=EMBED_COLOR,
    )
    if avatar:
        head.set_thumbnail(url=avatar)
    roles = discord.Embed(title="👑  Roles", description="\n".join(line(k) for k in _ROLE_KEYS), color=PURPLE)
    chans = discord.Embed(title="📁  Channels", description="\n".join(line(k) for k in _MAIN_CHANNEL_KEYS), color=SKY)
    logs = discord.Embed(title="📝  Logs", description="\n".join(line(k) for k in _LOG_KEYS), color=MUTED)
    issues = _settings_health(guild, store)
    health = discord.Embed(
        title="🩺  Health check",
        description="\n".join(f"⚠️ {i}" for i in issues[:12]) if issues else "✅ Everything looks good.",
        color=WARN if issues else OK,
    )
    _brand(
        health, guild,
        "/settings channel · /settings role · /settings logs",
    )
    return [head, roles, chans, logs, health]


settings_group = app_commands.Group(
    name="settings", description="Set up the bot — channels, roles & logs (leadership)"
)


async def _settings_guard(interaction: discord.Interaction) -> bool:
    if is_leadership(interaction.user):
        return True
    await interaction.response.send_message("Only leadership can change settings.", ephemeral=True)
    return False


async def _apply_setting(
    interaction: discord.Interaction, key: str, obj: "discord.abc.Snowflake | None"
) -> None:
    label, skey, kind = CONFIG_SETTINGS[key]
    store = guild_store(DATA, interaction.guild_id)
    store[skey] = obj.id if obj is not None else None
    save_data(DATA)
    shown = obj.mention if obj is not None else "*cleared*"
    done = sum(1 for _, s, _ in CONFIG_SETTINGS.values() if store.get(s))
    e = discord.Embed(
        title=f"✅  {_SETTING_EMOJI[key]} {label}",
        description=(
            f"Now set to {shown}\n\n`{_bar(done, len(CONFIG_SETTINGS), 18)}` "
            f"**{done}/{len(CONFIG_SETTINGS)}** configured"
        ),
        color=OK if obj is not None else MUTED,
    )
    _brand(e, interaction.guild, "See everything with /settings view")
    await _send_card(interaction, [e], ephemeral=True)
    await send_log(
        interaction.guild, store, f"⚙️ Setting changed: **{label}** → {shown}",
        category="config", actor=interaction.user,
    )


@settings_group.command(name="view", description="Setup progress, every setting, and a health check")
async def settings_view(interaction: discord.Interaction) -> None:
    if not await _settings_guard(interaction):
        return
    store = guild_store(DATA, interaction.guild_id)
    avatar = interaction.client.user.display_avatar.url if interaction.client.user else None
    await _send_card(interaction, _settings_cards(interaction.guild, store, avatar), ephemeral=True)


@settings_group.command(name="channel", description="Set (or clear) a channel setting")
@app_commands.describe(setting="Which channel setting", channel="The channel — leave empty to clear it")
@app_commands.choices(
    setting=[
        app_commands.Choice(name=f"{_SETTING_EMOJI[k]} {CONFIG_SETTINGS[k][0]}", value=k)
        for k in _CHANNEL_KEYS
    ]
)
async def settings_channel(
    interaction: discord.Interaction,
    setting: app_commands.Choice[str],
    channel: discord.TextChannel | None = None,
) -> None:
    if not await _settings_guard(interaction):
        return
    await _apply_setting(interaction, setting.value, channel)


@settings_group.command(name="role", description="Set (or clear) a role setting")
@app_commands.describe(setting="Which role setting", role="The role — leave empty to clear it")
@app_commands.choices(
    setting=[
        app_commands.Choice(name=f"{_SETTING_EMOJI[k]} {CONFIG_SETTINGS[k][0]}", value=k)
        for k in _ROLE_KEYS
    ]
)
async def settings_role(
    interaction: discord.Interaction,
    setting: app_commands.Choice[str],
    role: discord.Role | None = None,
) -> None:
    if not await _settings_guard(interaction):
        return
    if setting.value == "leadership_role" and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "Only a server admin can set the leadership role.", ephemeral=True
        )
        return
    await _apply_setting(interaction, setting.value, role)


@settings_group.command(name="logs", description="Send every log type to one channel in one go")
@app_commands.describe(channel="Where all logs should go")
async def settings_logs(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    if not await _settings_guard(interaction):
        return
    store = guild_store(DATA, interaction.guild_id)
    for k in _LOG_KEYS:
        store[CONFIG_SETTINGS[k][1]] = channel.id
    save_data(DATA)
    e = discord.Embed(
        title="✅  📝 All logs → one channel",
        description=f"All **{len(_LOG_KEYS)}** log types now go to {channel.mention}.\n"
        "-# Split any back out with `/settings channel`.",
        color=OK,
    )
    _brand(e, interaction.guild)
    await _send_card(interaction, [e], ephemeral=True)
    await send_log(
        interaction.guild, store, f"⚙️ All log types routed to {channel.mention}.",
        category="config", actor=interaction.user,
    )


def _log_channel_name(setting_key: str) -> str:
    """Friendly channel name for a *_log_channel setting key."""
    if setting_key == "log_channel":
        return "general-log"
    return setting_key[: -len("_channel")].replace("_", "-")  # web_log_channel -> web-log


async def ensure_log_channels(guild: discord.Guild, store: dict[str, Any]):
    """Create a '📋 Gang Manager Logs' category + a channel for every log type
    that isn't already set, wiring up the settings. Returns (created, kept) where
    created is a list of channel mentions and kept is a list of setting keys left
    alone. The caller saves DATA and logs. Shared by the command and the website."""
    me = guild.me
    cat = discord.utils.get(guild.categories, name="📋 Gang Manager Logs")
    if cat is None:
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        lead = _leadership_role(guild, store)
        if lead is not None:
            overwrites[lead] = discord.PermissionOverwrite(view_channel=True)
        try:
            cat = await guild.create_category(
                "📋 Gang Manager Logs", overwrites=overwrites, reason="Log channels setup"
            )
        except discord.HTTPException:
            cat = None
    created: list[str] = []
    kept: list[str] = []
    for k in _LOG_KEYS:
        skey = CONFIG_SETTINGS[k][1]
        cur = store.get(skey)
        if cur and guild.get_channel(cur) is not None:
            kept.append(k)
            continue
        try:
            ch = await guild.create_text_channel(
                _log_channel_name(k), category=cat, reason="Log channel setup"
            )
            store[skey] = ch.id
            created.append(ch.mention)
        except discord.HTTPException:
            pass
    return created, kept


@settings_group.command(
    name="logs_setup",
    description="Auto-create a Logs category with a channel for every log type (leadership)",
)
async def settings_logs_setup(interaction: discord.Interaction) -> None:
    if not await _settings_guard(interaction):
        return
    guild = interaction.guild
    me = guild.me
    if me is None or not me.guild_permissions.manage_channels:
        await interaction.response.send_message(
            "I need the **Manage Channels** permission to create log channels.",
            ephemeral=True,
        )
        return
    await interaction.response.defer(ephemeral=True)
    store = guild_store(DATA, interaction.guild_id)

    created, kept = await ensure_log_channels(guild, store)
    save_data(DATA)

    desc = f"Created **{len(created)}** log channel(s) under **📋 Gang Manager Logs**:"
    desc += "\n" + ("\n".join(created) if created else "—")
    if kept:
        desc += f"\n\nLeft **{len(kept)}** already-set channel(s) alone. Run this again any time to re-create missing ones."
    e = discord.Embed(title="✅  📋 Log channels ready", description=desc, color=OK)
    _brand(e, guild)
    await _send_card(interaction, [e], ephemeral=True)
    await send_log(
        guild, store, f"⚙️ Log channels auto-created via `/settings logs_setup` ({len(created)} new).",
        category="config", actor=interaction.user,
    )


# export everything (incl. _helpers) to the modules that build on this one
__all__ = [n for n in list(globals()) if not n.startswith("__")]
