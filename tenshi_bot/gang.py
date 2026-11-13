"""/gang dashboard & profile, /strike, /war (live scoreboards)."""

from .core import *  # noqa: F401,F403
from .roster import *  # noqa: F401,F403
from .kos import *  # noqa: F401,F403
from .settings import *  # noqa: F401,F403
from .panels import *  # noqa: F401,F403
from .tickets import *  # noqa: F401,F403


# --------------------------------------------------------------------------- #
# Gang features: strikes · wars · dashboard · member profile
# --------------------------------------------------------------------------- #

strike_group = app_commands.Group(name="strike", description="Warnings / strikes system")
war_group = app_commands.Group(name="war", description="Gang war tracker")
gang_group = app_commands.Group(name="gang", description="Gang dashboard & member profiles")




# ---- strikes ---------------------------------------------------------------- #

@strike_group.command(name="add", description="Give a member a strike (leadership)")
@app_commands.describe(member="Who gets the strike", reason="Why")
async def strike_add(interaction: discord.Interaction, member: discord.Member, reason: str) -> None:
    if await _deny(interaction, "give strikes"):
        return
    store = guild_store(DATA, interaction.guild_id)
    sid = store.get("next_strike_id", 1)
    store["next_strike_id"] = sid + 1
    store["strikes"].append(
        {
            "id": sid,
            "user_id": member.id,
            "reason": reason[:300],
            "by": interaction.user.id,
            "ts": int(datetime.datetime.now(datetime.timezone.utc).timestamp()),
        }
    )
    save_data(DATA)
    count = sum(1 for s in store["strikes"] if s["user_id"] == member.id)
    limit = store.get("strike_limit", 3)
    bar = "🟥" * min(count, limit) + "⬛" * max(0, limit - count)
    embed = discord.Embed(
        title="⚠️  Strike issued",
        description=f"{member.mention} now has **{count}/{limit}** strikes\n{bar}",
        color=WARN if count < limit else BAD,
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )
    embed.add_field(name="Reason", value=reason[:1024], inline=False)
    embed.add_field(name="Strike ID", value=f"`#{sid}`", inline=True)
    embed.add_field(name="Issued by", value=interaction.user.mention, inline=True)
    embed.set_thumbnail(url=member.display_avatar.url)
    if count >= limit:
        embed.add_field(
            name="🚨 Limit reached",
            value=f"{member.mention} has hit the **{limit}-strike** limit — leadership should review.",
            inline=False,
        )
    _brand(embed, interaction.guild)
    await interaction.response.send_message(embed=embed)
    try:
        dm = discord.Embed(
            title=f"⚠️  You received a strike in {interaction.guild.name}",
            description=f"You're on **{count}/{limit}** strikes.\n**Reason:** {reason[:1000]}",
            color=WARN,
        )
        await member.send(embed=dm)
    except discord.HTTPException:
        pass
    await send_log(
        interaction.guild, store,
        f"⚠️ Strike `#{sid}` → {member.mention} ({count}/{limit}).",
        category="roster", actor=interaction.user, fields=[("Reason", reason[:1024], False)],
    )


@strike_group.command(name="list", description="Show strikes for a member, or everyone")
@app_commands.describe(member="Leave empty to see everyone with strikes")
async def strike_list(interaction: discord.Interaction, member: discord.Member | None = None) -> None:
    store = guild_store(DATA, interaction.guild_id)
    limit = store.get("strike_limit", 3)
    embed = discord.Embed(color=WARN, timestamp=datetime.datetime.now(datetime.timezone.utc))
    if member is not None:
        mine = [s for s in store["strikes"] if s["user_id"] == member.id]
        embed.title = f"⚠️  Strikes · {member.display_name} ({len(mine)}/{limit})"
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.description = (
            "\n".join(
                f"`#{s['id']}` <t:{s['ts']}:d> — {s['reason']} *(by <@{s['by']}>)*" for s in mine
            )[:4000]
            or "✨ Clean record."
        )
    else:
        counts: dict[int, int] = {}
        for s in store["strikes"]:
            counts[s["user_id"]] = counts.get(s["user_id"], 0) + 1
        embed.title = "⚠️  Strike board"
        ranked = sorted(counts.items(), key=lambda kv: -kv[1])
        embed.description = (
            "\n".join(
                f"{'🚨' if n >= limit else '⚠️'} <@{uid}> — **{n}/{limit}**" for uid, n in ranked
            )[:4000]
            or "✨ Nobody has strikes."
        )
    _brand(embed, interaction.guild, f"Limit: {limit} strikes")
    await _send_v2(interaction, embed, ephemeral=True)


@strike_group.command(name="remove", description="Remove a strike by its ID (leadership)")
@app_commands.describe(strike_id="The # shown in /strike list")
async def strike_remove(interaction: discord.Interaction, strike_id: int) -> None:
    if await _deny(interaction, "remove strikes"):
        return
    store = guild_store(DATA, interaction.guild_id)
    hit = next((s for s in store["strikes"] if s["id"] == strike_id), None)
    if hit is None:
        await interaction.response.send_message(f"No strike `#{strike_id}`.", ephemeral=True)
        return
    store["strikes"].remove(hit)
    save_data(DATA)
    await notify_strike_change(interaction.guild, store, hit["user_id"], kind="remove", strike_id=strike_id)
    await interaction.response.send_message(f"🗑️ Removed strike `#{strike_id}` from <@{hit['user_id']}>.")
    await send_log(interaction.guild, store, f"🗑️ Strike `#{strike_id}` removed from <@{hit['user_id']}>.",
                   category="roster", actor=interaction.user)


@strike_group.command(name="clear", description="Clear all of a member's strikes (leadership)")
async def strike_clear(interaction: discord.Interaction, member: discord.Member) -> None:
    if await _deny(interaction, "clear strikes"):
        return
    store = guild_store(DATA, interaction.guild_id)
    before = len(store["strikes"])
    store["strikes"] = [s for s in store["strikes"] if s["user_id"] != member.id]
    n = before - len(store["strikes"])
    save_data(DATA)
    if n:
        await notify_strike_change(interaction.guild, store, member.id, kind="clear")
    await interaction.response.send_message(f"🧹 Cleared **{n}** strike(s) from {member.mention}.")
    await send_log(interaction.guild, store, f"🧹 Cleared {n} strike(s) from {member.mention}.",
                   category="roster", actor=interaction.user)


@strike_group.command(name="limit", description="Set how many strikes is the limit (leadership)")
async def strike_limit(interaction: discord.Interaction, limit: app_commands.Range[int, 1, 20]) -> None:
    if await _deny(interaction, "change the strike limit"):
        return
    store = guild_store(DATA, interaction.guild_id)
    store["strike_limit"] = limit
    save_data(DATA)
    await interaction.response.send_message(f"⚠️ Strike limit set to **{limit}**.", ephemeral=True)


# ---- wars: live scoreboard with staff round buttons ------------------------ #

def _war_by_panel(store: dict[str, Any], message_id: int) -> dict | None:
    for w in store["wars"]:
        if (w.get("panel") or {}).get("message_id") == message_id:
            return w
    return None


def _find_war(store, enemy: str, active_only: bool = True) -> dict | None:
    for war in store["wars"]:
        if war["enemy"].lower() == enemy.lower() and (war.get("active", True) or not active_only):
            return war
    return None


def _war_verdict(war: dict) -> tuple[str, int]:
    w, l = war.get("wins", 0), war.get("losses", 0)
    if w > l:
        return "🏆 VICTORY", OK
    if l > w:
        return "💀 DEFEAT", MUTED
    return "🤝 DRAW", WARN


def _war_card(war: dict, guild: discord.Guild) -> discord.Embed:
    w, l = war.get("wins", 0), war.get("losses", 0)
    total = w + l
    active = war.get("active", True)
    target = war.get("first_to")
    if active:
        status = f"🔴 **LIVE** · round **{total + 1}**" + (f" · first to **{target}**" if target else "")
        color = BAD
    else:
        verdict, color = _war_verdict(war)
        status = f"**{verdict}** · ended <t:{war.get('end', war['start'])}:R>"
    e = discord.Embed(
        title=f"⚔️  {guild.name}  vs  {war['enemy']}",
        description=f"# 🏆 {w}  —  {l} 💀\n{status}\n-# started <t:{war['start']}:R>",
        color=color,
    )
    pct = f"{w / total * 100:.0f}%" if total else "—"
    e.add_field(name="📈 Win rate", value=f"`{_bar(w, total, 16)}` **{pct}**", inline=False)
    rounds = war.get("log", [])
    form = "".join("🟩" if x["r"] == "win" else "🟥" for x in rounds[-20:]) or "*No rounds yet*"
    e.add_field(name=f"🧾 Rounds ({total})", value=form, inline=False)
    if target and active:
        e.add_field(
            name="🎯 First to " + str(target),
            value=f"🏆 **{max(0, target - w)}** more to win · 💀 **{max(0, target - l)}** from defeat",
            inline=False,
        )
    last = rounds[-1] if rounds else None
    foot = (
        f"Last round logged by {last['by_name']}"
        if last and last.get("by_name")
        else "Staff: tap a button after each round"
    )
    _brand(e, guild, foot)
    return e


def _war_link(guild: discord.Guild, war: dict) -> str:
    p = war.get("panel") or {}
    if p.get("channel_id") and p.get("message_id"):
        return f"https://discord.com/channels/{guild.id}/{p['channel_id']}/{p['message_id']}"
    return ""


async def _post_war_panel(channel, war: dict, guild: discord.Guild) -> None:
    msg = await _send_card(
        channel,
        [_war_card(war, guild)],
        WarPanelView if war.get("active", True) else None,
    )
    if msg is not None:
        war["panel"] = {"channel_id": msg.channel.id, "message_id": msg.id}
        save_data(DATA)


async def _refresh_war_panel(guild: discord.Guild, war: dict) -> None:
    """Edit the war's scoreboard wherever it lives (used by /war end)."""
    p = war.get("panel") or {}
    ch = guild.get_channel(p.get("channel_id") or 0)
    if ch is None:
        return
    try:
        msg = await ch.fetch_message(p["message_id"])
        await _edit_card(
            msg, [_war_card(war, guild)], WarPanelView if war.get("active", True) else None
        )
    except discord.HTTPException:
        pass


async def _announce_war_end(channel, war: dict, guild: discord.Guild, actor=None) -> None:
    verdict, color = _war_verdict(war)
    e = discord.Embed(
        title=f"🏳️  War over — {verdict}",
        description=f"**{guild.name}** vs **{war['enemy']}** finished **{war['wins']} — {war['losses']}**",
        color=color,
    )
    _brand(e, guild)
    try:
        await channel.send(embed=e)
    except discord.HTTPException:
        pass
    await send_log(
        guild, guild_store(DATA, guild.id),
        f"🏳️ War vs **{war['enemy']}** ended {war['wins']}–{war['losses']} ({verdict}).",
        category="kos", actor=actor,
    )


class WarPanelView(GatedView):
    """Staff buttons on a war scoreboard."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    async def _click(self, interaction: discord.Interaction, action: str) -> None:
        if await _deny(interaction, "update the war score", who="staff"):
            return
        store = guild_store(DATA, interaction.guild_id)
        war = _war_by_panel(store, interaction.message.id)
        if war is None:
            await interaction.response.send_message(
                "This scoreboard isn't linked to a war anymore — use `/war panel`.", ephemeral=True
            )
            return
        if not war.get("active", True):
            await interaction.response.send_message("This war is already over.", ephemeral=True)
            return
        now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        log_ = war.setdefault("log", [])
        if action in ("win", "loss"):
            war["wins" if action == "win" else "losses"] += 1
            log_.append({
                "r": action, "ts": now,
                "by": interaction.user.id, "by_name": interaction.user.display_name,
            })
            war["log"] = log_[-200:]
        elif action == "undo":
            if not log_:
                await interaction.response.send_message("No rounds to undo.", ephemeral=True)
                return
            last = log_.pop()
            war["wins" if last["r"] == "win" else "losses"] -= 1
        target = war.get("first_to")
        ended = action == "end" or (
            target and (war["wins"] >= target or war["losses"] >= target)
        )
        if ended:
            war["active"] = False
            war["end"] = now
        save_data(DATA)
        await _edit_card(
            interaction.message,
            [_war_card(war, interaction.guild)],
            None if ended else WarPanelView,
            interaction=interaction,
        )
        if ended:
            await _announce_war_end(interaction.channel, war, interaction.guild, interaction.user)

    @discord.ui.button(label="Round won", style=discord.ButtonStyle.success, emoji="🏆", custom_id="war:win")
    async def won(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._click(interaction, "win")

    @discord.ui.button(label="Round lost", style=discord.ButtonStyle.danger, emoji="💀", custom_id="war:loss")
    async def lost(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._click(interaction, "loss")

    @discord.ui.button(label="Undo", style=discord.ButtonStyle.secondary, emoji="↩️", custom_id="war:undo")
    async def undo(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._click(interaction, "undo")

    @discord.ui.button(label="End war", style=discord.ButtonStyle.secondary, emoji="🏳️", custom_id="war:end")
    async def end(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._click(interaction, "end")


async def war_autocomplete(interaction: discord.Interaction, current: str):
    store = guild_store(DATA, interaction.guild_id)
    return [
        app_commands.Choice(name=w["enemy"], value=w["enemy"])
        for w in store["wars"]
        if w.get("active", True) and current.lower() in w["enemy"].lower()
    ][:25]


@war_group.command(name="start", description="Start a war and post a live scoreboard (leadership)")
@app_commands.describe(
    enemy="Who you're at war with",
    first_to="Optional: rounds needed to win — the war ends itself when a side gets there",
)
async def war_start(
    interaction: discord.Interaction,
    enemy: str,
    first_to: app_commands.Range[int, 1, 100] | None = None,
) -> None:
    if await _deny(interaction, "start wars"):
        return
    store = guild_store(DATA, interaction.guild_id)
    if _find_war(store, enemy):
        await interaction.response.send_message(
            f"Already at war with **{enemy}** — `/war panel` re-posts its scoreboard.", ephemeral=True
        )
        return
    war = {
        "enemy": enemy.strip()[:60],
        "wins": 0,
        "losses": 0,
        "start": int(datetime.datetime.now(datetime.timezone.utc).timestamp()),
        "active": True,
        "first_to": first_to,
        "log": [],
    }
    store["wars"].append(war)
    save_data(DATA)
    await interaction.response.send_message(
        f"⚔️ War vs **{war['enemy']}** started — scoreboard posted below.", ephemeral=True
    )
    await _post_war_panel(interaction.channel, war, interaction.guild)
    await send_log(interaction.guild, store, f"⚔️ War started vs **{war['enemy']}**.",
                   category="kos", actor=interaction.user)


@war_group.command(name="panel", description="Re-post a war's live scoreboard here (leadership)")
@app_commands.autocomplete(enemy=war_autocomplete)
async def war_panel(interaction: discord.Interaction, enemy: str) -> None:
    if await _deny(interaction, "post war scoreboards"):
        return
    store = guild_store(DATA, interaction.guild_id)
    war = _find_war(store, enemy)
    if war is None:
        await interaction.response.send_message(f"No active war with **{enemy}**.", ephemeral=True)
        return
    await interaction.response.send_message("Scoreboard posted. ⚔️", ephemeral=True)
    await _post_war_panel(interaction.channel, war, interaction.guild)


@war_group.command(name="end", description="End a war (leadership)")
@app_commands.autocomplete(enemy=war_autocomplete)
async def war_end(interaction: discord.Interaction, enemy: str) -> None:
    if await _deny(interaction, "end wars"):
        return
    store = guild_store(DATA, interaction.guild_id)
    war = _find_war(store, enemy)
    if war is None:
        await interaction.response.send_message(f"No active war with **{enemy}**.", ephemeral=True)
        return
    war["active"] = False
    war["end"] = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    save_data(DATA)
    await interaction.response.send_message("🏳️ War ended.", ephemeral=True)
    await _refresh_war_panel(interaction.guild, war)
    await _announce_war_end(interaction.channel, war, interaction.guild, interaction.user)


@war_group.command(name="view", description="All active wars, with live scores and links")
async def war_view(interaction: discord.Interaction) -> None:
    store = guild_store(DATA, interaction.guild_id)
    active = [w for w in store["wars"] if w.get("active", True)]
    embed = discord.Embed(title="⚔️  Active Wars", color=BAD)
    if not active:
        embed.description = "☮️ No active wars — `/war start` to begin one."
    for w in active[:20]:
        total = w["wins"] + w["losses"]
        pct = f"{w['wins'] / total * 100:.0f}%" if total else "—"
        link = _war_link(interaction.guild, w)
        embed.add_field(
            name=f"vs {w['enemy']}  ·  🏆 {w['wins']} — {w['losses']} 💀",
            value=(
                f"`{_bar(w['wins'], total, 14)}` {pct}"
                + (f" · first to {w['first_to']}" if w.get("first_to") else "")
                + (f"\n[Open scoreboard ↗]({link})" if link else "")
            ),
            inline=False,
        )
    _brand(embed, interaction.guild)
    await _send_v2(interaction, embed)


@war_group.command(name="history", description="Past wars and how they ended")
async def war_history(interaction: discord.Interaction) -> None:
    store = guild_store(DATA, interaction.guild_id)
    done = [w for w in store["wars"] if not w.get("active", True)][-10:][::-1]
    won = sum(1 for w in store["wars"] if not w.get("active", True) and w["wins"] > w["losses"])
    lost = sum(1 for w in store["wars"] if not w.get("active", True) and w["losses"] > w["wins"])
    embed = discord.Embed(
        title="📜  War History",
        description=f"🏆 **{won}** won · 💀 **{lost}** lost" if done else "No finished wars yet.",
        color=MUTED,
    )
    for w in done:
        verdict, _ = _war_verdict(w)
        embed.add_field(
            name=f"{verdict}  vs {w['enemy']}",
            value=f"**{w['wins']} — {w['losses']}** · <t:{w.get('end', w['start'])}:R>",
            inline=False,
        )
    _brand(embed, interaction.guild)
    await _send_v2(interaction, embed)


# ---- dashboard + member profile -------------------------------------------- #

def _dashboard_cards(guild: discord.Guild, store: dict[str, Any]) -> list[discord.Embed]:
    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    ranked_ids: set[int] = set()
    rank_rows: list[tuple[str, int, int]] = []
    for r in store.get("ranks", []):
        role = guild.get_role(r["role_id"])
        if role is not None:
            members = role.members
            ranked_ids.update(m.id for m in members)
            rank_rows.append((r["name"], len(members), role.position))
    rank_rows.sort(key=lambda x: -x[2])
    orders = store["orders"]
    open_o = [o for o in orders if o.get("status", "open") != "closed"]
    recent_strikes = sum(1 for s in store["strikes"] if s["ts"] >= now - 30 * 86400)
    active_wars = [w for w in store["wars"] if w.get("active", True)]
    eoi_timers = store.get("eoi_timers", {})
    open_eois = sum(
        1 for mid in store.get("eoi_apps", {}) if not eoi_timers.get(mid, {}).get("done")
    )

    hq = discord.Embed(
        title=f"👑  {guild.name} — Command Center",
        description=f"-# 🟢 Live · updated <t:{now}:R> · {guild.member_count or len(guild.members)} in server",
        color=EMBED_COLOR,
    )
    if guild.icon:
        hq.set_thumbnail(url=guild.icon.url)
    hq.add_field(name="👥 Ranked", value=f"**{len(ranked_ids)}**")
    hq.add_field(name="💀 On KOS", value=f"**{len(store['kos'])}**")
    hq.add_field(name="⚠️ Strikes 30d", value=f"**{recent_strikes}**")
    hq.add_field(name="📨 Pending EOIs", value=f"**{open_eois}**")
    hq.add_field(name="📻 Radio", value=f"`{store.get('radio_freq') or '—'}`")
    if guild.banner:
        hq.set_image(url=guild.banner.url)
    else:
        _hero(hq)

    daily = _daily_counts([o["created_ts"] for o in orders if o.get("created_ts")])
    ops = discord.Embed(
        title="🎫  Tickets",
        description=f"**{len(open_o)}** open · **{len(orders)}** all-time",
        color=SKY,
    )
    ops.add_field(
        name="📊 Opened per day · last 14d",
        value=f"`{_spark(daily)}`  peak **{max(daily)}**/day · **{sum(daily)}** total",
        inline=False,
    )
    if open_o:
        lines = []
        for key, label in reversed(PRIORITIES):
            n = sum(1 for o in open_o if o.get("priority", "normal") == key)
            dot, name = label.split(" ", 1)
            lines.append(f"{dot} `{_bar(n, len(open_o), 10)}` **{n}** {name}")
        ops.add_field(name="🚩 Open by priority", value="\n".join(lines), inline=False)

    wars = discord.Embed(title="⚔️  Wars", color=BAD)
    if active_wars:
        rows = []
        for w in active_wars[:5]:
            total = w["wins"] + w["losses"]
            pct = f"{w['wins'] / total * 100:.0f}%" if total else "—"
            rows.append(f"**vs {w['enemy']}** · {w['wins']}–{w['losses']}\n`{_bar(w['wins'], total, 14)}` {pct} win rate")
        wars.description = "\n".join(rows)
    else:
        wars.description = "☮️ No active wars."

    cards = [hq, ops, wars]
    if rank_rows:
        top = max(n for _, n, _ in rank_rows) or 1
        cards.append(
            discord.Embed(
                title="🎖️  Ranks",
                description="\n".join(
                    f"`{_bar(n, top, 10)}` **{n}** · {name}" for name, n, _ in rank_rows[:8]
                ),
                color=PURPLE,
            )
        )
    _brand(cards[-1], guild, "🔄 tap Refresh for live numbers")
    return cards


class DashboardView(GatedView):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Refresh", style=discord.ButtonStyle.secondary, emoji="🔄",
        custom_id="gang:dashboard",
    )
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        store = guild_store(DATA, interaction.guild_id)
        await _edit_card(
            interaction.message,
            _dashboard_cards(interaction.guild, store),
            DashboardView,
            interaction=interaction,
        )


@gang_group.command(name="dashboard", description="Live command center for the whole gang")
async def gang_dashboard(interaction: discord.Interaction) -> None:
    store = guild_store(DATA, interaction.guild_id)
    await _send_card(interaction, _dashboard_cards(interaction.guild, store), DashboardView)


def _profile_parts(member: discord.Member, store: dict[str, Any]) -> dict[str, Any]:
    rank_names = [
        r["name"] for r in store.get("ranks", [])
        if any(role.id == r["role_id"] for role in member.roles)
    ]
    strikes = [s for s in store["strikes"] if s["user_id"] == member.id]
    limit = store.get("strike_limit", 3)
    n = len(strikes)
    return {
        "rank": rank_names[0] if rank_names else "Unranked",
        "extra_ranks": rank_names[1:],
        "strikes": n,
        "limit": limit,
        "strike_squares": "🟥" * min(n, limit) + "🟩" * max(0, limit - n),
        "on_kos": any(k.get("id") == member.id for k in store["kos"]),
        "opened": sum(1 for o in store["orders"] if o.get("user_id") == member.id),
        "handled": sum(
            1 for o in store["orders"]
            if o.get("claimed_by") == member.id and o.get("status") == "closed"
        ),
        "roles": [r for r in sorted(member.roles, key=lambda r: -r.position) if not r.is_default()][:6],
        "colour": member.colour if member.colour.value else discord.Colour(EMBED_COLOR),
    }


def _profile_layout(member: discord.Member, p: dict[str, Any], banner: str | None, guild: discord.Guild):
    """Profile card: banner → avatar + name header → stats → roles."""
    c = discord.ui.Container(accent_colour=p["colour"])
    if banner:
        c.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(banner)))
    joined = f"joined <t:{int(member.joined_at.timestamp())}:R>" if member.joined_at else ""
    c.add_item(
        discord.ui.Section(
            discord.ui.TextDisplay(
                f"# {member.display_name}\n-# @{member.name} · {joined}"
            ),
            discord.ui.TextDisplay(f"### 🎖️ {p['rank']}"
                                   + (f"  ·  {' · '.join(p['extra_ranks'])}" if p["extra_ranks"] else "")),
            accessory=discord.ui.Thumbnail(member.display_avatar.url),
        )
    )
    c.add_item(discord.ui.Separator())
    status = "💀 **On KOS**" if p["on_kos"] else "🟢 **Not on KOS**"
    c.add_item(discord.ui.TextDisplay(
        f"**⚠️ Strikes**  {p['strikes']}/{p['limit']}\n{p['strike_squares']}\n\n"
        f"{status}\n"
        f"🎫 **{p['opened']}** tickets opened  ·  🙋 **{p['handled']}** handled"
    ))
    if p["roles"]:
        c.add_item(discord.ui.Separator(visible=False))
        c.add_item(discord.ui.TextDisplay("🏷️ " + " ".join(r.mention for r in p["roles"])))
    c.add_item(discord.ui.Separator(visible=False))
    c.add_item(discord.ui.TextDisplay(
        f"-# {guild.name} · account made <t:{int(member.created_at.timestamp())}:R>"
    ))
    lv = discord.ui.LayoutView()
    lv.add_item(c)
    return lv


def _profile_embed(member: discord.Member, p: dict[str, Any], banner: str | None, guild) -> discord.Embed:
    e = discord.Embed(
        title=member.display_name,
        description=f"@{member.name} · 🎖️ **{p['rank']}**",
        color=p["colour"],
    )
    e.set_thumbnail(url=member.display_avatar.url)
    if banner:
        e.set_image(url=banner)
    e.add_field(name="⚠️ Strikes", value=f"{p['strikes']}/{p['limit']} {p['strike_squares']}")
    e.add_field(name="💀 KOS", value="Yes" if p["on_kos"] else "No")
    e.add_field(name="🎫 Tickets", value=f"{p['opened']} opened · {p['handled']} handled")
    if p["roles"]:
        e.add_field(name="🏷️ Roles", value=" ".join(r.mention for r in p["roles"]), inline=False)
    _brand(e, guild)
    return e


@gang_group.command(name="profile", description="A member's gang profile card")
@app_commands.describe(member="Who to look up (defaults to you)")
async def gang_profile(interaction: discord.Interaction, member: discord.Member | None = None) -> None:
    member = member or interaction.user
    await interaction.response.defer()
    store = guild_store(DATA, interaction.guild_id)
    p = _profile_parts(member, store)
    banner = None
    try:  # profile banners only come back from a full user fetch
        u = await interaction.client.fetch_user(member.id)
        banner = u.banner.url if u.banner else None
    except discord.HTTPException:
        pass
    try:
        await interaction.followup.send(
            view=_profile_layout(member, p, banner, interaction.guild),
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("V2 profile failed (%s) — falling back to embed", exc)
        await interaction.followup.send(
            embed=_profile_embed(member, p, banner, interaction.guild),
            allowed_mentions=discord.AllowedMentions.none(),
        )


# export everything (incl. _helpers) to the modules that build on this one
__all__ = [n for n in list(globals()) if not n.startswith("__")]
