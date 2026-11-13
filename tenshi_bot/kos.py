"""/kos — KOS list, sign-up → draw → staff approval → announce flow."""

from .core import *  # noqa: F401,F403
from .roster import *  # noqa: F401,F403


# ---- /kos ------------------------------------------------------------------- #

def _kos_role(guild: discord.Guild, store: dict[str, Any]) -> discord.Role | None:
    """The configured KOS role for this guild, or None if unset/deleted."""
    rid = store.get("kos_role_id")
    return guild.get_role(rid) if rid else None


def _kos_embed(entries: list[dict[str, Any]]) -> discord.Embed:
    embed = discord.Embed(
        title="💀  Kill On Sight",
        color=BAD,
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )
    if not entries:
        embed.description = "*Empty.* Add targets with `/kos add`."
        return embed
    lines = []
    for i, e in enumerate(entries, start=1):
        who = f"<@{e['id']}>" if e.get("id") else e.get("name", "Unknown")
        tag = f"  ·  `{e['note']}`" if e.get("note") else ""
        lines.append(f"`{i:>2}`  {who}{tag}")
    embed.description = "\n".join(lines)
    n = len(entries)
    embed.set_footer(text=f"🎯 {n} on sight")
    return embed


@kos_group.command(name="view", description="Show the Kill-On-Sight list")
async def kos_view(interaction: discord.Interaction) -> None:
    store = guild_store(DATA, interaction.guild_id)
    embed = _brand(_kos_embed(store["kos"]), interaction.guild)
    await _send_v2(interaction, embed)


@kos_group.command(
    name="add",
    description="Add up to 30 members to the KOS list and give them the KOS role",
)
@app_commands.describe(
    member="Member to KOS (@mention them)",
    more="Paste extra @mentions or IDs here to KOS many at once (up to 30 total)",
    reason="Optional reason applied to everyone added",
)
async def kos_add(
    interaction: discord.Interaction,
    member: discord.Member,
    more: str | None = None,
    reason: str | None = None,
) -> None:
    await interaction.response.defer()
    store = guild_store(DATA, interaction.guild_id)
    role = _kos_role(interaction.guild, store)
    targets = await _collect_members(interaction.guild, member, more, limit=30)

    added, skipped, role_fail = [], [], []
    for m in targets:
        if any(k.get("id") == m.id for k in store["kos"]):
            skipped.append(m.mention)
            continue
        if role is not None:
            try:
                await m.add_roles(role, reason=f"KOS add by {interaction.user}")
            except (discord.Forbidden, discord.HTTPException):
                role_fail.append(m.mention)
        store["kos"].append(
            {
                "id": m.id,
                "name": m.display_name,
                "note": reason,
                "added_by": str(interaction.user),
                "at": now_stamp(),
            }
        )
        added.append(m.mention)
    save_data(DATA)

    lines = []
    if added:
        lines.append(f"💀 Added **{len(added)}** to the KOS list: {', '.join(added)}")
    if skipped:
        lines.append(f"ℹ️ Already KOS: {', '.join(skipped)}.")
    if role is None:
        lines.append(
            "⚠️ No KOS role set — set the **KOS role** with `/settings role` so they "
            "get one automatically."
        )
    elif role_fail:
        lines.append(
            f"⚠️ Couldn't give the {role.mention} role to {', '.join(role_fail)} "
            "— check I have **Manage Roles** and my role sits above it."
        )
    await interaction.followup.send("\n".join(lines)[:2000] or "Nothing to add.")

    if added:
        await send_log(
            interaction.guild,
            store,
            f"💀 Added **{len(added)}** to the KOS list.",
            category="kos",
            actor=interaction.user,
            fields=[
                ("Targets", ", ".join(added), False),
                ("Reason", reason or "—", True),
                ("KOS size", str(len(store["kos"])), True),
            ],
        )


@kos_group.command(
    name="remove",
    description="Remove a member from the KOS list and take the role back",
)
@app_commands.describe(member="The member to remove (@mention them)")
async def kos_remove(
    interaction: discord.Interaction, member: discord.Member
) -> None:
    store = guild_store(DATA, interaction.guild_id)
    before = len(store["kos"])
    store["kos"] = [k for k in store["kos"] if k.get("id") != member.id]
    if len(store["kos"]) == before:
        await interaction.response.send_message(
            f"{member.mention} isn't on the KOS list.", ephemeral=True
        )
        return

    role = _kos_role(interaction.guild, store)
    note = ""
    if role is not None and role in member.roles:
        try:
            await member.remove_roles(role, reason=f"KOS remove by {interaction.user}")
        except (discord.Forbidden, discord.HTTPException):
            note = f"\n⚠️ Couldn't take the {role.mention} role off them."

    save_data(DATA)
    await interaction.response.send_message(
        f"Removed {member.mention} from the KOS list. 🗑️{note}"
    )
    await send_log(
        interaction.guild,
        store,
        f"{interaction.user.mention} removed {member.mention} from the KOS list.",
        category="kos",
        actor=interaction.user,
    )


@kos_group.command(
    name="reset",
    description="Full KOS roster reset — clears the list and takes the KOS role off everyone (leadership)",
)
async def kos_reset(interaction: discord.Interaction) -> None:
    if await _deny(interaction, "reset the KOS roster"):
        return
    await interaction.response.defer()
    guild = interaction.guild
    store = guild_store(DATA, interaction.guild_id)
    count = len(store["kos"])

    # take the KOS role off everyone who has it (list members + anyone holding it)
    role = _kos_role(guild, store)
    removed, failed = 0, 0
    if role is not None:
        holders = {m.id: m for m in role.members}
        for e in store["kos"]:
            if e.get("id") and e["id"] not in holders:
                mem = guild.get_member(e["id"])
                if mem is not None and role in mem.roles:
                    holders[mem.id] = mem
        for mem in holders.values():
            try:
                await mem.remove_roles(role, reason=f"KOS reset by {interaction.user}")
                removed += 1
            except (discord.Forbidden, discord.HTTPException):
                failed += 1

    # clear the roster + any open sign-ups/draws; saved channels (kos_defaults) stay
    store["kos"] = []
    store["kos_musters"] = {}
    store["kos_draws"] = {}
    store["kos_proposals"] = {}
    save_data(DATA)

    msg = f"💀 KOS roster reset — **{count}** cleared"
    if role is not None:
        msg += f", {role.mention} taken off **{removed}** member(s)"
    msg += ". Your saved KOS channels are kept. 🧹"
    if failed:
        msg += f"\n⚠️ Couldn't remove the role from {failed} — check my role is above {role.mention}."
    await interaction.followup.send(msg, allowed_mentions=discord.AllowedMentions.none())
    await send_log(
        guild,
        store,
        f"{interaction.user.mention} reset the KOS roster ({count} cleared, role removed from {removed}).",
        category="kos",
        actor=interaction.user,
    )


# ---- /kos start → party sign-up → draw → staff verify → approve ------------- #


def _member_name(guild: discord.Guild, uid: int) -> str:
    m = guild.get_member(uid)
    return m.display_name if m else str(uid)


def _muster_embed(text: str, attendees: list[int], size: int) -> discord.Embed:
    embed = discord.Embed(
        title="🎯  KOS Roster",
        description=text or "*No details given.*",
        color=EMBED_COLOR,
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )
    if attendees:
        lst = "\n".join(f"`{i:>2}` <@{uid}>" for i, uid in enumerate(attendees, 1))
    else:
        lst = "*Nobody yet — tap **Join** below.*"
    embed.add_field(name=f"✅ In the pool · {len(attendees)}", value=lst[:1024], inline=False)
    embed.set_footer(text=f"Tap to join · tap again to leave · {size} will be drawn")
    return embed


class MusterView(GatedView):
    """Public sign-up — just the Join button."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Join the KOS roster",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id="kos:muster",
    )
    async def toggle(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        store = guild_store(DATA, interaction.guild_id)
        key = str(interaction.message.id)
        rec = store["kos_musters"].get(key)
        if rec is None:
            rec = {"text": "", "attendees": [], "size": 0, "channel_id": interaction.channel_id}
            store["kos_musters"][key] = rec
        uid = interaction.user.id
        if uid in rec["attendees"]:
            rec["attendees"].remove(uid)
            note = "You've **left** the pool."
        else:
            rec["attendees"].append(uid)
            note = "You're **in** the pool. ✅"
        save_data(DATA)
        await _edit_card(
            interaction.message,
            [_brand(_muster_embed(rec["text"], rec["attendees"], rec.get("size", 0)), interaction.guild)],
            MusterView,
            interaction=interaction,
        )
        try:
            await interaction.followup.send(note, ephemeral=True)
        except discord.HTTPException:
            pass


MUSTER_VIEW = MusterView()


def _draw_control_embed(target: discord.abc.GuildChannel, size: int) -> discord.Embed:
    return discord.Embed(
        title="🎲  KOS Roster — Draw",
        description=(
            f"Sign-up is live in {target.mention}.\n"
            f"When enough have joined, tap **Draw** to randomly pick **{size}** "
            "and review them here before they go on the roster."
        ),
        color=EMBED_COLOR,
    )


class StaffDrawView(GatedView):
    """Leaders-chat control — the Draw button."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Draw roster",
        style=discord.ButtonStyle.primary,
        emoji="🎲",
        custom_id="kos:draw",
    )
    async def draw(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if await _deny(interaction, "draw the roster"):
            return
        store = guild_store(DATA, interaction.guild_id)
        link = store["kos_draws"].get(str(interaction.message.id))
        if not link:
            await interaction.response.send_message(
                "This draw control isn't linked to a sign-up anymore.", ephemeral=True
            )
            return
        rec = store["kos_musters"].get(link["signup_msg_id"])
        if not rec or not rec.get("attendees"):
            await interaction.response.send_message(
                "Nobody has joined the pool yet.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)

        pool = list(rec["attendees"])
        size = rec.get("size") or len(pool)
        random.shuffle(pool)
        prop = {
            "text": rec.get("text", ""),
            "roster": pool[:size],
            "bench": pool[size:],
            "size": size,
            "announce_channel_id": link["signup_channel_id"],
            "roster_channel_id": link.get("roster_channel_id"),
            "staff_channel_id": interaction.channel_id,
            "status": "pending",
        }
        role = (
            interaction.guild.get_role(link["ping_role_id"])
            if link.get("ping_role_id")
            else None
        )
        head = "🎲 **KOS draw is ready** — pick the members, then approve."
        pmsg = await _send_card(
            interaction.channel,
            [_proposal_embed(interaction.guild, prop)],
            RosterProposalView,
            header=f"{role.mention} {head}" if role else head,
            allowed_mentions=discord.AllowedMentions(roles=True),
        )
        store["kos_proposals"][str(pmsg.id)] = prop
        save_data(DATA)
        await interaction.followup.send(
            f"Drawn **{len(prop['roster'])}** from {len(pool)} — approve it below. ✅",
            ephemeral=True,
        )


STAFF_DRAW_VIEW = StaffDrawView()


# ---- staff verification / approval ----------------------------------------- #

def _proposal_embed(guild: discord.Guild, prop: dict[str, Any]) -> discord.Embed:
    approved = prop.get("status") == "approved"
    embed = discord.Embed(
        title="💀  KOS Roster — Approved" if approved else "🗳️  KOS Roster — Approve",
        description=prop.get("text") or "*No details.*",
        color=OK if approved else EMBED_COLOR,
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )
    roster = prop.get("roster", [])
    if roster:
        rlist = "\n".join(
            f"`{i:>2}` <@{uid}>" for i, uid in enumerate(roster, 1)
        )
    else:
        rlist = "*empty*"
    embed.add_field(
        name=f"🎯 Roster · {len(roster)}/{prop.get('size', len(roster))}",
        value=rlist[:1024],
        inline=False,
    )
    bench = prop.get("bench", [])
    if bench and not approved:
        blist = ", ".join(f"<@{uid}>" for uid in bench[:30])
        embed.add_field(name=f"🪑 Bench · {len(bench)}", value=blist[:1024], inline=False)
    _brand(
        embed,
        guild,
        "Approved ✅" if approved else "Remove / Add / Reshuffle, then Approve (leadership)",
    )
    return embed


async def _refresh_proposal(
    guild: discord.Guild, store: dict[str, Any], key: str
) -> None:
    prop = store["kos_proposals"].get(key)
    if not prop:
        return
    ch = guild.get_channel(prop["staff_channel_id"])
    if ch is None:
        return
    try:
        msg = await ch.fetch_message(int(key))
        await _edit_card(msg, [_proposal_embed(guild, prop)], RosterProposalView)
    except discord.HTTPException:
        pass


def _member_options(guild: discord.Guild, ids: list[int]) -> list[discord.SelectOption]:
    opts = [
        discord.SelectOption(label=_member_name(guild, uid)[:100], value=str(uid))
        for uid in ids[:25]
    ]
    return opts or [discord.SelectOption(label="—", value="0")]


class _ProposalRemoveSelect(discord.ui.Select):
    def __init__(self, guild: discord.Guild, key: str, roster: list[int]) -> None:
        self.key = key
        super().__init__(
            placeholder="Pick someone to remove…",
            options=_member_options(guild, roster),
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        store = guild_store(DATA, interaction.guild_id)
        prop = store["kos_proposals"].get(self.key)
        if not prop:
            await interaction.response.send_message("Proposal expired.", ephemeral=True)
            return
        uid = int(self.values[0])
        if uid in prop["roster"]:
            prop["roster"].remove(uid)
            if uid not in prop["bench"]:
                prop["bench"].insert(0, uid)
            save_data(DATA)
        await _refresh_proposal(interaction.guild, store, self.key)
        await interaction.response.send_message(
            f"Removed <@{uid}> from the roster.", ephemeral=True
        )


class _ProposalAddSelect(discord.ui.Select):
    def __init__(self, guild: discord.Guild, key: str, bench: list[int]) -> None:
        self.key = key
        super().__init__(
            placeholder="Pick someone to add…",
            options=_member_options(guild, bench),
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        store = guild_store(DATA, interaction.guild_id)
        prop = store["kos_proposals"].get(self.key)
        if not prop:
            await interaction.response.send_message("Proposal expired.", ephemeral=True)
            return
        uid = int(self.values[0])
        if uid in prop["bench"]:
            prop["bench"].remove(uid)
            if uid not in prop["roster"]:
                prop["roster"].append(uid)
            save_data(DATA)
        await _refresh_proposal(interaction.guild, store, self.key)
        await interaction.response.send_message(
            f"Added <@{uid}> to the roster.", ephemeral=True
        )


class _SubSelectView(GatedView):
    def __init__(self, item: discord.ui.Select) -> None:
        super().__init__(timeout=120)
        self.add_item(item)


class RosterProposalView(GatedView):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    def _get(self, interaction: discord.Interaction):
        store = guild_store(DATA, interaction.guild_id)
        return store, store["kos_proposals"].get(str(interaction.message.id))

    @discord.ui.button(
        label="Approve", style=discord.ButtonStyle.success, emoji="✅",
        custom_id="kosprop:approve", row=0,
    )
    async def approve(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if await _deny(interaction, "approve"):
            return
        store, prop = self._get(interaction)
        if not prop:
            await interaction.response.send_message("Proposal expired.", ephemeral=True)
            return
        if prop.get("status") == "approved":
            await interaction.response.send_message(
                "Already approved.", ephemeral=True
            )
            return
        await interaction.response.defer()
        guild = interaction.guild
        prop["status"] = "approved"
        role = _kos_role(guild, store)
        for uid in prop["roster"]:
            mem = guild.get_member(uid)
            if mem is None:
                try:
                    mem = await guild.fetch_member(uid)
                except (discord.NotFound, discord.HTTPException):
                    mem = None
            if role is not None and mem is not None:
                try:
                    await mem.add_roles(role, reason=f"KOS roster by {interaction.user}")
                except (discord.Forbidden, discord.HTTPException):
                    pass
            if not any(k.get("id") == uid for k in store["kos"]):
                store["kos"].append(
                    {
                        "id": uid,
                        "name": _member_name(guild, uid),
                        "note": "roster",
                        "added_by": str(interaction.user),
                        "at": now_stamp(),
                    }
                )
        save_data(DATA)

        # final roster post, pinging the KOS role
        final = discord.Embed(
            title="💀  KOS Roster",
            description=prop.get("text") or "",
            color=BAD,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        final.add_field(
            name=f"On the roster · {len(prop['roster'])}",
            value="\n".join(f"`{i:>2}` <@{u}>" for i, u in enumerate(prop["roster"], 1))
            or "*empty*",
            inline=False,
        )
        if guild.icon:
            final.set_thumbnail(url=guild.icon.url)
        _brand(final, guild, f"Approved by {interaction.user.display_name}")

        # post to KOS announcements and the KOS roster channel (once each)
        targets = []
        for cid in (prop.get("announce_channel_id"), prop.get("roster_channel_id")):
            ch = guild.get_channel(cid) if cid else None
            if ch is not None and ch not in targets:
                targets.append(ch)
        content = role.mention if role is not None else None
        posted = []
        for ch in targets:
            try:
                await _send_card(
                    ch,
                    [final],
                    None,
                    header=content,
                    allowed_mentions=discord.AllowedMentions(roles=True),
                )
                posted.append(ch.mention)
            except discord.HTTPException:
                pass

        await _edit_card(interaction.message, [_proposal_embed(guild, prop)], None)
        await interaction.followup.send(
            f"✅ Approved — posted to {', '.join(posted) or 'nowhere (check my permissions)'} "
            f"and gave **{len(prop['roster'])}** the KOS role.",
            ephemeral=True,
        )
        await send_log(
            guild, store,
            f"💀 {interaction.user.mention} approved a KOS roster "
            f"({len(prop['roster'])} members).",
            category="kos", actor=interaction.user,
        )

    @discord.ui.button(
        label="Reshuffle", style=discord.ButtonStyle.secondary, emoji="🔀",
        custom_id="kosprop:reshuffle", row=0,
    )
    async def reshuffle(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if await _deny(interaction, "reshuffle"):
            return
        store, prop = self._get(interaction)
        if not prop or prop.get("status") == "approved":
            await interaction.response.send_message(
                "Nothing to reshuffle.", ephemeral=True
            )
            return
        pool = prop["roster"] + prop["bench"]
        random.shuffle(pool)
        size = prop.get("size") or len(pool)
        prop["roster"] = pool[:size]
        prop["bench"] = pool[size:]
        save_data(DATA)
        await _edit_card(
            interaction.message,
            [_proposal_embed(interaction.guild, prop)],
            RosterProposalView,
            interaction=interaction,
        )

    @discord.ui.button(
        label="Remove", style=discord.ButtonStyle.danger, emoji="➖",
        custom_id="kosprop:remove", row=1,
    )
    async def remove(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if await _deny(interaction, "edit the roster"):
            return
        store, prop = self._get(interaction)
        if not prop or not prop.get("roster") or prop.get("status") == "approved":
            await interaction.response.send_message(
                "Nothing to remove.", ephemeral=True
            )
            return
        key = str(interaction.message.id)
        await interaction.response.send_message(
            "Who should come off the roster?",
            view=_SubSelectView(_ProposalRemoveSelect(interaction.guild, key, prop["roster"])),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Add", style=discord.ButtonStyle.secondary, emoji="➕",
        custom_id="kosprop:add", row=1,
    )
    async def add(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if await _deny(interaction, "edit the roster"):
            return
        store, prop = self._get(interaction)
        if not prop or not prop.get("bench") or prop.get("status") == "approved":
            await interaction.response.send_message(
                "Nobody on the bench to add.", ephemeral=True
            )
            return
        key = str(interaction.message.id)
        await interaction.response.send_message(
            "Who should go onto the roster?",
            view=_SubSelectView(_ProposalAddSelect(interaction.guild, key, prop["bench"])),
            ephemeral=True,
        )


ROSTER_PROPOSAL_VIEW = RosterProposalView()


@kos_group.command(
    name="start",
    description="Start a KOS roster sign-up; leadership draws & approves (leadership)",
)
@app_commands.describe(
    text="What the sign-up post should say",
    size="How many to draw onto the roster",
    channel="KOS announcements — sign-up & final roster (remembered after first use)",
    draw_channel="Where the Draw & member picking happens (remembered)",
    roster_channel="KOS roster channel — approved roster also posts here (remembered)",
    ping_role="Role to ping on sign-up and when the draw is ready (remembered)",
)
async def kos_start(
    interaction: discord.Interaction,
    text: str,
    size: int,
    channel: discord.TextChannel | None = None,
    draw_channel: discord.TextChannel | None = None,
    roster_channel: discord.TextChannel | None = None,
    ping_role: discord.Role | None = None,
) -> None:
    if await _deny(interaction, "start a roster"):
        return
    if size < 1:
        await interaction.response.send_message(
            "Size must be at least 1.", ephemeral=True
        )
        return
    store = guild_store(DATA, interaction.guild_id)
    guild = interaction.guild

    # Remember what you pick: anything passed now is saved, anything left out
    # falls back to what you saved last time (survives /kos reset).
    saved = store.setdefault("kos_defaults", {})
    for key, val in (
        ("channel_id", channel),
        ("draw_channel_id", draw_channel),
        ("roster_channel_id", roster_channel),
        ("ping_role_id", ping_role),
    ):
        if val is not None:
            saved[key] = val.id
    if channel is None and saved.get("channel_id"):
        channel = guild.get_channel(saved["channel_id"])
    if draw_channel is None and saved.get("draw_channel_id"):
        draw_channel = guild.get_channel(saved["draw_channel_id"])
    if roster_channel is None and saved.get("roster_channel_id"):
        roster_channel = guild.get_channel(saved["roster_channel_id"])
    if ping_role is None and saved.get("ping_role_id"):
        ping_role = guild.get_role(saved["ping_role_id"])

    # Draw control goes to: draw channel → configured staff channel → here.
    staff = draw_channel
    if staff is None and store.get("staff_channel_id"):
        staff = guild.get_channel(store["staff_channel_id"])
    if staff is None:
        staff = interaction.channel
    target = channel or interaction.channel
    ping = ping_role.mention if ping_role else None
    pings = discord.AllowedMentions(roles=True)
    try:
        # fresh view instance per message — sharing the registered one breaks clicks
        msg = await _send_card(
            target,
            [_brand(_muster_embed(text, [], size), guild)],
            MusterView,
            header=ping,
            allowed_mentions=pings,
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            f"I can't post in {target.mention} — check my permissions there.",
            ephemeral=True,
        )
        return
    store["kos_musters"][str(msg.id)] = {
        "text": text,
        "attendees": [],
        "size": size,
        "channel_id": target.id,
    }
    try:
        control = await _send_card(
            staff, [_brand(_draw_control_embed(target, size), guild)], StaffDrawView
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            f"Posted the sign-up, but I can't post the Draw control in {staff.mention} "
            "— check my permissions there.",
            ephemeral=True,
        )
        save_data(DATA)
        return
    store["kos_draws"][str(control.id)] = {
        "signup_msg_id": str(msg.id),
        "signup_channel_id": target.id,
        "roster_channel_id": roster_channel.id if roster_channel else None,
        "ping_role_id": ping_role.id if ping_role else None,
    }
    save_data(DATA)
    await interaction.response.send_message(
        f"Sign-up posted in {target.mention}; the **Draw** control is in "
        f"{staff.mention} for leadership. ✅",
        ephemeral=True,
    )
    await send_log(
        interaction.guild,
        store,
        f"🎯 {interaction.user.mention} started a KOS roster sign-up in {target.mention}.",
        category="kos",
        actor=interaction.user,
    )


# export everything (incl. _helpers) to the modules that build on this one
__all__ = [n for n in list(globals()) if not n.startswith("__")]
