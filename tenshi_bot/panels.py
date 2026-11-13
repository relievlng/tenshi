"""/role_menu, /radio, /eoi, /eoi_post and /gang_help."""

from .core import *  # noqa: F401,F403
from .roster import *  # noqa: F401,F403
from .kos import *  # noqa: F401,F403
from .settings import *  # noqa: F401,F403


# ---- /role_menu ------------------------------------------------------------- #

@role_menu_group.command(
    name="add", description="Add a role to the self-serve menu (leadership)"
)
@app_commands.describe(
    role="A role members can give themselves",
    description="Optional short note shown next to the role in the dropdown",
)
async def role_menu_add(
    interaction: discord.Interaction,
    role: discord.Role,
    description: str | None = None,
) -> None:
    if await _deny(interaction, "edit the role menu"):
        return
    store = guild_store(DATA, interaction.guild_id)
    roles = store["self_roles"]
    if any(r["id"] == role.id for r in roles):
        await interaction.response.send_message(
            f"{role.mention} is already in the menu.", ephemeral=True
        )
        return
    if len(roles) >= 25:
        await interaction.response.send_message(
            "A dropdown can hold at most 25 roles.", ephemeral=True
        )
        return
    roles.append(
        {
            "id": role.id,
            "label": role.name,
            "description": (description.strip()[:100] if description else None),
        }
    )
    save_data(DATA)
    await interaction.response.send_message(
        f"Added {role.mention} to the role menu. Run `/role_menu post` to "
        "(re)post it so the change shows. ✅",
        ephemeral=True,
    )


@role_menu_group.command(
    name="remove", description="Remove a role from the self-serve menu (leadership)"
)
@app_commands.describe(role="The role to take out of the menu")
async def role_menu_remove(
    interaction: discord.Interaction, role: discord.Role
) -> None:
    if await _deny(interaction, "edit the role menu"):
        return
    store = guild_store(DATA, interaction.guild_id)
    before = len(store["self_roles"])
    store["self_roles"] = [r for r in store["self_roles"] if r["id"] != role.id]
    if len(store["self_roles"]) == before:
        await interaction.response.send_message(
            f"{role.mention} isn't in the menu.", ephemeral=True
        )
        return
    save_data(DATA)
    await interaction.response.send_message(
        f"Removed {role.mention}. Run `/role_menu post` to repost the menu. 🗑️",
        ephemeral=True,
    )


@role_menu_group.command(name="list", description="Show the roles in the self-serve menu")
async def role_menu_list(interaction: discord.Interaction) -> None:
    store = guild_store(DATA, interaction.guild_id)
    roles = store["self_roles"]
    embed = discord.Embed(title="🎭 Role Menu", color=EMBED_COLOR)
    if not roles:
        embed.description = "No roles yet. Add some with `/role_menu add`."
    else:
        embed.description = "\n".join(
            f"- <@&{r['id']}>" + (f" — {r['description']}" if r.get("description") else "")
            for r in roles
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@role_menu_group.command(
    name="post",
    description="Post the self-serve role menu in this channel (leadership)",
)
@app_commands.describe(
    title="Optional heading for the menu",
    description="Optional text shown above the dropdown",
)
async def role_menu_post(
    interaction: discord.Interaction,
    title: str | None = None,
    description: str | None = None,
) -> None:
    if await _deny(interaction, "post the role menu"):
        return
    store = guild_store(DATA, interaction.guild_id)
    options: list[discord.SelectOption] = []
    for r in store["self_roles"]:
        role = interaction.guild.get_role(r["id"])
        if role is None:
            continue
        options.append(
            discord.SelectOption(
                label=role.name,
                value=str(role.id),
                description=r.get("description") or None,
            )
        )
    if not options:
        await interaction.response.send_message(
            "No usable roles in the menu yet — add some with `/role_menu add` first.",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title=title or "🎭 Select Your Roles",
        description=description
        or "Pick the roles you want from the menu below. Anything you leave "
        "unselected will be removed.",
        color=EMBED_COLOR,
    )
    _hero(embed)
    _brand(embed, interaction.guild)
    await _send_card(interaction.channel, [embed], lambda: SelfRoleView(options))
    await interaction.response.send_message("Role menu posted. ✅", ephemeral=True)


# ---- /radio ----------------------------------------------------------------- #

def _roll_frequency() -> str:
    """A random radio frequency in 00.00 format, e.g. '47.23'."""
    return f"{random.randint(0, 99):02d}.{random.randint(0, 99):02d}"


def _radio_embed(freq: str, rolled_by: str | None = None) -> discord.Embed:
    embed = discord.Embed(
        title="📻  Gang Radio",
        description=f"**Frequency**\n# `{freq}`",
        color=EMBED_COLOR,
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )
    if rolled_by:
        embed.set_footer(text=f"Rolled by {rolled_by}")
    return embed


async def _announce_radio(
    guild: discord.Guild,
    store: dict[str, Any],
    channel: "discord.abc.Messageable",
    content: str | None = None,
    embed: discord.Embed | None = None,
) -> None:
    """Post a new-frequency announcement and delete the previous one, so the
    channel only ever shows the current radio."""
    last = store.get("radio_last_msg") or {}
    if last.get("channel_id") and last.get("message_id"):
        old_ch = guild.get_channel(last["channel_id"])
        if old_ch is not None:
            try:
                await old_ch.get_partial_message(last["message_id"]).delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
    msg = await channel.send(
        content=content,
        embed=embed,
        allowed_mentions=discord.AllowedMentions(roles=True),
    )
    store["radio_last_msg"] = {"channel_id": msg.channel.id, "message_id": msg.id}
    save_data(DATA)


def _radio_panel_embed(freq: str | None, rolled_by: str | None = None) -> discord.Embed:
    embed = discord.Embed(title="📻  Gang Radio", color=EMBED_COLOR)
    if freq:
        embed.description = (
            f"**Current frequency**\n# `{freq}`\n\n"
            "Press **📻 New Radio** to roll a fresh one."
        )
    else:
        embed.description = (
            "No frequency set yet.\nPress **📻 New Radio** below to roll one."
        )
    if rolled_by:
        embed.set_footer(text=f"Last rolled by {rolled_by}")
    return embed


class RadioPanelView(GatedView):
    """Persistent panel: a button that rolls a new frequency and pings a role."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="New Radio",
        style=discord.ButtonStyle.primary,
        emoji="📻",
        custom_id="radio:new",
    )
    async def new_radio(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if await _deny(interaction, "roll a new radio"):
            return
        await interaction.response.defer()

        store = guild_store(DATA, interaction.guild_id)
        freq = _roll_frequency()
        store["radio_freq"] = freq
        save_data(DATA)

        # refresh the panel to show the new frequency
        try:
            await _edit_card(
                interaction.message,
                [_brand(_radio_panel_embed(freq, str(interaction.user)), interaction.guild)],
                RadioPanelView,
            )
        except discord.HTTPException:
            pass

        # ping the configured role with the new frequency
        rid = store.get("radio_ping_role_id")
        role = interaction.guild.get_role(rid) if rid else None
        if role is not None:
            try:
                await _announce_radio(
                    interaction.guild, store, interaction.channel,
                    content=f"{role.mention} 📻 New radio frequency: `{freq}`",
                )
            except discord.HTTPException:
                pass

        await interaction.followup.send(f"Rolled `{freq}`. 📻", ephemeral=True)
        await send_log(
            interaction.guild,
            store,
            "📻 New radio frequency rolled.",
            category="radio",
            actor=interaction.user,
            fields=[("Frequency", f"`{freq}`", True)],
        )


RADIO_PANEL_VIEW = RadioPanelView()


@radio_group.command(
    name="new",
    description="Roll a new random radio frequency and post it here (leadership)",
)
async def radio_new(interaction: discord.Interaction) -> None:
    if await _deny(interaction, "roll a new radio"):
        return
    store = guild_store(DATA, interaction.guild_id)
    freq = _roll_frequency()
    store["radio_freq"] = freq
    save_data(DATA)
    await interaction.response.send_message(
        f"New radio rolled: `{freq}` 📻", ephemeral=True
    )
    rid = store.get("radio_ping_role_id")
    role = interaction.guild.get_role(rid) if rid else None
    await _announce_radio(
        interaction.guild, store, interaction.channel,
        content=role.mention if role else None,
        embed=_brand(_radio_embed(freq, rolled_by=str(interaction.user)), interaction.guild),
    )
    await send_log(
        interaction.guild,
        store,
        "📻 New radio frequency rolled.",
        category="radio",
        actor=interaction.user,
        fields=[("Frequency", f"`{freq}`", True)],
    )


@radio_group.command(name="view", description="Show the current radio frequency")
async def radio_view(interaction: discord.Interaction) -> None:
    store = guild_store(DATA, interaction.guild_id)
    freq = store.get("radio_freq")
    if not freq:
        await interaction.response.send_message(
            "No radio set yet — leadership can roll one with `/radio new`.",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(embed=_radio_embed(freq))


@radio_group.command(
    name="panel",
    description="Post a radio panel with a New Radio button (leadership)",
)
async def radio_panel(interaction: discord.Interaction) -> None:
    if await _deny(interaction, "post the radio panel"):
        return
    store = guild_store(DATA, interaction.guild_id)
    await _send_card(
        interaction.channel,
        [_brand(_radio_panel_embed(store.get("radio_freq")), interaction.guild)],
        RadioPanelView,
    )
    await interaction.response.send_message("Radio panel posted. 📻", ephemeral=True)


# ---- /eoi (open the form) --------------------------------------------------- #

@bot.tree.command(name="eoi", description="Apply to the gang — fill out an EOI form")
async def eoi(interaction: discord.Interaction) -> None:
    if await _block_if_already_member(interaction):
        return
    await interaction.response.send_modal(EOIModal())


# ---- /eoi_post (leadership force-posts a blank EOI prompt) ------------------- #

@bot.tree.command(
    name="eoi_post",
    description="Leadership: post an EOI prompt button for people to apply",
)
@app_commands.describe(
    message="Optional custom text shown above the Apply button",
)
async def eoi_post(
    interaction: discord.Interaction, message: str | None = None
) -> None:
    if await _deny(interaction, "post the EOI prompt"):
        return

    store = guild_store(DATA, interaction.guild_id)
    if store.get("eoi_channel_id") is None:
        await interaction.response.send_message(
            "Set an EOI channel first: `/settings channel` → 📥 EOI channel.", ephemeral=True
        )
        return

    embed = discord.Embed(
        title="📝  Recruitment is Open",
        description=message
        or "Want to roll with us? Tap **📨 Apply** below and fill out the form — "
        "the current members of the gang vote on every application.",
        color=EMBED_COLOR,
    )
    if interaction.guild is not None and interaction.guild.icon:
        embed.set_thumbnail(url=interaction.guild.icon.url)
    _hero(embed)
    embed.add_field(
        name="How it works",
        value=(
            "1️⃣  Tap **📨 Apply** — name, role, activity, montages, pick a voucher\n"
            "2️⃣  Current members vote ✅ / ❌ on your application\n"
            "3️⃣  When voting closes, the result is posted here 🎉"
        ),
        inline=False,
    )
    embed.add_field(
        name="⚠️ Heads up",
        value=(
            "• **No montage = instant deny** — put at least one in your application.\n"
            "• Invite your mates and have them fill this out too."
        ),
        inline=False,
    )
    _brand(embed, interaction.guild, "Applications are voted on by current members")
    await _send_card(interaction.channel, [embed], EOIPromptView)
    await interaction.response.send_message("EOI prompt posted. ✅", ephemeral=True)


class EOIPromptView(GatedView):
    """A persistent 'Apply' button that opens the EOI modal for anyone."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Apply",
        style=discord.ButtonStyle.primary,
        emoji="📨",
        custom_id="eoi:apply",
    )
    async def apply(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if await _block_if_already_member(interaction):
            return
        await interaction.response.send_modal(EOIModal())


# ---- /gang_help ------------------------------------------------------------- #

_HELP_PAGES: dict[str, tuple[str, str, str]] = {
    "home": ("🏠", "Gang Manager — Command Guide",
             "**Pick a category below** to see its commands.\n\n"
             "**Quick start**\n"
             "1️⃣ Open the **web panel → ⚙️ Settings** — channels, roles and logs in one place\n"
             "2️⃣ `/roster import_roles` — your ranks from your server roles\n"
             "3️⃣ `/ticket type_add` → `/ticket panel` — open for orders\n"
             "4️⃣ `/eoi_post` — put up the Apply panel for recruits\n"
             "5️⃣ `/gang dashboard` — your live command center"),
    "gang": ("👑", "Gang",
             "`/gang dashboard` — live command center with graphs + 🔄 refresh\n"
             "`/gang profile [@member]` — rank, KOS, strikes, tickets\n"
             "`/strike add` · `list` · `remove` · `clear` · `limit`\n"
             "`/war start <enemy> [first_to]` — live scoreboard; staff tap 🏆 won / 💀 lost each round\n"
             "`/war view` · `history` · `panel` · `end`"),
    "roster": ("🎯", "Roster",
               "`/roster view` — live from your rank roles, with 🔄 refresh\n"
               "`/roster import_roles` — every server role as a rank ⭐\n"
               "`/roster rank_set <name> <role>` · `rank_list` · `rank_remove`\n"
               "`/roster add` · `remove` · `reset`"),
    "kos": ("💀", "KOS",
            "`/kos view` · `/kos add @members [reason]` · `/kos remove @member`\n"
            "`/kos start <text> <size>` — sign-up → 🎲 Draw → pick → ✅ approve → posts to announcements + roster (channels remembered)\n"
            "`/kos reset` — full reset, keeps your saved channels"),
    "tickets": ("🎫", "Tickets",
                "`/ticket type_add` · `type_remove` · `type_list` · `set_category`\n"
                "`/ticket panel` — post the order menu · `/ticket toggle` — open/close orders\n"
                "`/ticket board` — open orders, urgent first\n"
                "`/ticket stats` — analytics + staff leaderboard\n"
                "`/ticket autoclose <hours>` · `/ticket add` · `/ticket remove`\n"
                "`/close [reason]` — transcript to staff + a short DM to the customer\n"
                "In a ticket: 🙋 Claim · 💰 Paid · 🚩 Priority · 🔒 Close"),
    "community": ("📨", "Recruitment · Radio · Roles",
                  "`/eoi` · `/eoi_post` — apply / post the Apply panel\n"
                  "EOI form: role + activity, montage (auto YouTube preview), and pick a voucher\n"
                  "Members vote ✅/❌ · ⏱️ auto 24h timer (leadership can change) · vouch runs by DM\n"
                  "`/radio new` · `view` · `panel` — rolling gang frequency\n"
                  "`/role_menu add` · `remove` · `list` · `post` — self-serve roles"),
    "settings": ("⚙️", "Settings & Website",
                 "**Easiest:** the web panel's **⚙️ Settings** tab sets everything from dropdowns\n"
                 "`/settings view` — setup progress, everything set, 🩺 health check\n"
                 "`/settings channel` · `/settings role` — set one (leave empty to clear)\n"
                 "`/settings logs #channel` — every log type to one channel\n"
                 "`/settings logs_setup` — auto-create a channel for every log type\n"
                 "`/subscription status` — this server's plan and paid-until date"),
}


def _help_embed(key: str, guild: discord.Guild | None, avatar_url: str | None = None) -> discord.Embed:
    emoji, title, body = _HELP_PAGES.get(key, _HELP_PAGES["home"])
    e = discord.Embed(title=f"{emoji}  {title}", description=body, color=EMBED_COLOR)
    _hero(e)  # the GM banner brands every page of the guide
    _brand(e, guild, "Staff commands need the leadership role or admin")
    return e


class HelpView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.select(
        custom_id="help:menu",
        placeholder="📚  Pick a category…",
        options=[
            discord.SelectOption(label=t, value=k, emoji=e) for k, (e, t, _) in _HELP_PAGES.items()
        ],
    )
    async def pick(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        avatar = interaction.client.user.display_avatar.url if interaction.client.user else None
        await _edit_card(
            interaction.message,
            [_help_embed(select.values[0], interaction.guild, avatar)],
            HelpView,
            interaction=interaction,
        )


@bot.tree.command(name="gang_help", description="Interactive guide to every command")
async def gang_help(interaction: discord.Interaction) -> None:
    avatar = interaction.client.user.display_avatar.url if interaction.client.user else None
    await _send_card(
        interaction, [_help_embed("home", interaction.guild, avatar)], HelpView, ephemeral=True
    )


# export everything (incl. _helpers) to the modules that build on this one
__all__ = [n for n in list(globals()) if not n.startswith("__")]
