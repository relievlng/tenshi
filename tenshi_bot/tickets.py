"""Ticket / order system: panels, tickets, transcripts, priority, auto-close, stats."""

from .core import *  # noqa: F401,F403
from .roster import *  # noqa: F401,F403
from .kos import *  # noqa: F401,F403
from .settings import *  # noqa: F401,F403
from .panels import *  # noqa: F401,F403


# --------------------------------------------------------------------------- #
# Ticket / order system (multiple types: gang orders, vendors, gun crafting…)
# --------------------------------------------------------------------------- #

def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:80] or "ticket"


def _new_order_id(store: dict[str, Any]) -> int:
    oid = store.get("next_order_id", 1)
    store["next_order_id"] = oid + 1
    return oid


def _compose_channel_name(oid, type_name: str, paid: bool, customer_name: str | None) -> str:
    """Ticket channel name: a ✅/❌ paid marker, the type, the customer and the id,
    e.g. '❌-ppsh-kenji-14' → '✅-ppsh-kenji-14' once paid."""
    mark = "✅" if paid else "❌"
    ttype = _slug(type_name or "ticket")
    who = _slug(customer_name) if customer_name else "order"
    return f"{mark}-{ttype}-{who}-{oid}"[:100]


def _order_channel_name(order: dict, guild) -> str:
    uid = order.get("user_id")
    m = guild.get_member(uid) if uid else None
    cust = m.display_name if m is not None else None
    return _compose_channel_name(order.get("id"), order.get("type"), bool(order.get("paid")), cust)


async def _rename_order_channel(guild, order: dict) -> None:
    """Rename the order's channel to match its customer + paid status. Best-effort
    — Discord rate-limits renames to 2 / 10 min, so a failure is ignored."""
    ch = guild.get_channel(order.get("channel_id") or 0)
    if ch is None:
        return
    name = _order_channel_name(order, guild)
    if getattr(ch, "name", None) == name:
        return
    try:
        await ch.edit(name=name, reason="Ticket name / paid status update")
    except discord.HTTPException:
        pass


def _find_order_by_channel(store: dict[str, Any], channel_id: int) -> dict | None:
    for o in store["orders"]:
        if o.get("channel_id") == channel_id:
            return o
    return None


def _find_ticket_type(store: dict[str, Any], name: str) -> dict | None:
    for t in store.get("ticket_types", []):
        if t["name"].lower() == name.lower():
            return t
    return None


PRIORITIES = [
    ("low", "🟢 Low"),
    ("normal", "🔵 Normal"),
    ("high", "🟠 High"),
    ("urgent", "🔴 Urgent"),
]
_PRIO_RANK = {k: i for i, (k, _) in enumerate(PRIORITIES)}


def _prio_label(order: dict[str, Any] | None) -> str:
    key = (order or {}).get("priority", "normal")
    return dict(PRIORITIES).get(key, "🔵 Normal")


_ORDER_STEPS = ("Opened", "Claimed", "Paid", "Closed")


def _order_progress(order: dict[str, Any]) -> str:
    """🟢 Opened → 🟢 Claimed → ⚪ Paid → ⚪ Closed — fills in as staff work it."""
    reached = (
        True,
        bool(order.get("claimed_by")),
        bool(order.get("paid")),
        order.get("status") == "closed",
    )
    return "  →  ".join(f"{'🟢' if r else '⚪'} {s}" for s, r in zip(_ORDER_STEPS, reached))


def _order_embed(order: dict[str, Any], guild: "discord.Guild | None" = None) -> discord.Embed:
    """The ticket's one live card: greeting, status panel, items, progress
    tracker. The accent shows the state at a glance — green once paid, red /
    amber for urgent / high priority, grey when closed."""
    paid = order.get("paid", False)
    closed = order.get("status", "open") == "closed"
    prio = order.get("priority", "normal")
    ttype = order.get("type") or "Order"
    uid = order.get("user_id")
    claimed = order.get("claimed_by")
    created = order.get("created_ts")

    if closed:
        color = MUTED
    elif paid:
        color = OK
    elif prio == "urgent":
        color = BAD
    elif prio == "high":
        color = WARN
    else:
        color = EMBED_COLOR

    emoji, member = "🎫", None
    if guild is not None:
        t = _find_ticket_type(guild_store(DATA, guild.id), ttype)
        emoji = (t or {}).get("emoji") or "🎫"
        member = guild.get_member(uid) if uid else None

    if uid:
        greet = (
            f"Hey <@{uid}> — thanks for the order! Staff will be with you shortly.\n"
            "Drop anything else below: prices, timing, drop spot. You'll get the full "
            "transcript in your DMs when it's closed."
        )
    else:
        greet = "Opened from the website. Staff — bring the customer in with `/ticket add`."

    embed = discord.Embed(
        title=f"{emoji}  {ttype} Order",
        description=greet,
        color=color,
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )
    embed.set_author(name=f"TICKET #{order['id']}")
    if member is not None:
        embed.set_thumbnail(url=member.display_avatar.url)

    status = "🔒 Closed" if closed else ("🟢 Paid" if paid else "🔴 Unpaid")
    embed.add_field(name="Status", value=status, inline=True)
    embed.add_field(name="Priority", value=_prio_label(order), inline=True)
    embed.add_field(name="Handled by", value=f"<@{claimed}>" if claimed else "*unclaimed*", inline=True)
    if created:
        embed.add_field(name="Opened", value=f"<t:{created}:R>", inline=True)
    embed.add_field(name="Quantity", value=(order.get("quantity") or "—")[:1024], inline=True)
    embed.add_field(name="📦 Items", value=(order.get("guns") or "—")[:1024], inline=False)
    if order.get("notes"):
        embed.add_field(name="📝 Notes", value=order["notes"][:1024], inline=False)
    embed.add_field(
        name="📍 Progress",
        value=f"{_order_progress(order)}\n-# Staff: `/ticket add` · `/close [reason]`",
        inline=False,
    )
    return embed


def _order_cards(order: dict[str, Any], guild: discord.Guild) -> list[discord.Embed]:
    return [_brand(_order_embed(order, guild), guild)]


def _orders_board_embed(store: dict[str, Any]) -> discord.Embed:
    active = [o for o in store["orders"] if o.get("status", "open") == "open"]
    emoji_by_type = {
        t["name"]: (t.get("emoji") or "🎫") for t in store.get("ticket_types", [])
    }

    def fmt(o: dict[str, Any]) -> str:
        items = (o.get("guns") or "—").replace("\n", " ")
        qty = (o.get("quantity") or "—").replace("\n", " ")
        mark = "💰" if o.get("paid") else "⏳"
        prio = _prio_label(o).split(" ")[0]
        line = f"{prio}{mark} `#{o['id']}` <@{o['user_id']}> — {items} ×{qty}"
        return line[:200]

    embed = discord.Embed(
        title="🎫  Active Orders",
        color=EMBED_COLOR,
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )
    if not active:
        embed.description = "✨ No active orders right now."
        return embed

    total_paid = sum(1 for o in active if o.get("paid"))
    embed.description = (
        f"**{len(active)}** open · 🟢 **{total_paid}** paid · "
        f"🔴 **{len(active) - total_paid}** unpaid"
    )

    types: dict[str, list[dict[str, Any]]] = {}
    active.sort(key=lambda o: -_PRIO_RANK.get(o.get("priority", "normal"), 1))
    for o in active:
        types.setdefault(o.get("type") or "Order", []).append(o)
    for ttype, orders in types.items():
        paid = sum(1 for o in orders if o.get("paid"))
        val = "\n".join(fmt(o) for o in orders) or "None"
        if len(val) > 1024:
            val = val[:1010] + "\n…"
        em = emoji_by_type.get(ttype, "🎫")
        embed.add_field(
            name=f"{em}  {ttype}  ·  {paid}/{len(orders)} paid",
            value=val,
            inline=False,
        )
    embed.set_footer(text="🔴🟠🔵🟢 priority · 💰 paid · ⏳ unpaid — urgent first")
    return embed


def _open_for(order: dict | None) -> str | None:
    """Human 'open for' duration, e.g. '2h 14m', from the order's created_ts."""
    ts = (order or {}).get("created_ts")
    if not ts:
        return None
    secs = max(0, int(datetime.datetime.now(datetime.timezone.utc).timestamp()) - ts)
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    parts = [f"{d}d"] if d else []
    if h:
        parts.append(f"{h}h")
    parts.append(f"{m}m")
    return " ".join(parts)


# ---- transcripts ------------------------------------------------------------ #

def _component_text(components) -> list[str]:
    """Pull readable text out of a V2 message (containers / sections / text
    blocks), dropping markdown heading markers so transcripts read cleanly."""
    out: list[str] = []

    def walk(c) -> None:
        content = getattr(c, "content", None)
        if isinstance(content, str) and content.strip():
            lines = []
            for line in content.split("\n"):
                for marker in ("### ", "## ", "# ", "-# "):
                    if line.startswith(marker):
                        line = line[len(marker):]
                        break
                if line.strip():
                    lines.append(line)
            if lines:
                out.append(" / ".join(lines))
        for ch in getattr(c, "children", None) or []:
            walk(ch)

    for c in components or []:
        walk(c)
    return out


async def _collect_history(channel: discord.TextChannel) -> list[dict[str, Any]]:
    """Read the whole channel once; both transcript formats render from this."""
    rows: list[dict[str, Any]] = []
    try:
        async for m in channel.history(limit=None, oldest_first=True):
            embeds = []
            for e in m.embeds:
                parts = []
                if e.title:
                    parts.append(e.title)
                if e.description:
                    parts.append(e.description)
                for f in e.fields:
                    parts.append(f"{f.name}: {f.value}")
                if parts:
                    embeds.append(parts)
            v2_text = _component_text(m.components)
            if v2_text:
                embeds.append(v2_text)
            rows.append(
                {
                    "ts": m.created_at,
                    "author": str(m.author),
                    "name": m.author.display_name,
                    "avatar": m.author.display_avatar.url,
                    "bot": m.author.bot,
                    "content": m.content or "",
                    "embeds": embeds,
                    "files": [(a.filename, a.url) for a in m.attachments],
                }
            )
    except discord.HTTPException:
        pass
    return rows


def _render_text(rows: list[dict[str, Any]]) -> str:
    out = []
    for r in rows:
        line = r["content"]
        for parts in r["embeds"]:
            line += (" " if line else "") + "[embed] " + " | ".join(parts)
        for fn, url in r["files"]:
            line += f" [attachment: {fn} {url}]"
        out.append(f"[{r['ts'].strftime('%Y-%m-%d %H:%M UTC')}] {r['author']}: {line}")
    return "\n".join(out) if out else "(no messages)"


def _render_html(rows: list[dict[str, Any]], title: str, guild_name: str) -> str:
    import html as _h

    def esc(s: str) -> str:
        return _h.escape(s).replace("\n", "<br>")

    msgs = []
    for r in rows:
        body = f'<div class="txt">{esc(r["content"])}</div>' if r["content"] else ""
        for parts in r["embeds"]:
            inner = "".join(f"<div>{esc(p)}</div>" for p in parts)
            body += f'<div class="emb">{inner}</div>'
        for fn, url in r["files"]:
            body += f'<div class="file">📎 <a href="{_h.escape(url)}">{esc(fn)}</a></div>'
        tag = '<span class="bot">BOT</span>' if r["bot"] else ""
        msgs.append(
            f'<div class="msg"><img class="av" src="{_h.escape(r["avatar"])}">'
            f'<div><div class="head"><b>{esc(r["name"])}</b>{tag}'
            f'<span class="ts">{r["ts"].strftime("%d %b %Y %H:%M UTC")}</span></div>'
            f"{body}</div></div>"
        )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{_h.escape(title)}</title><style>"
        "body{margin:0;background:#313338;color:#dbdee1;font:15px/1.4 system-ui,sans-serif}"
        ".top{background:#1e1f22;padding:18px 24px;border-bottom:3px solid #e11d48}"
        ".top h1{margin:0;font-size:20px;color:#fff}.top p{margin:4px 0 0;color:#949ba4}"
        ".msg{display:flex;gap:14px;padding:8px 24px}.msg:hover{background:#2e3035}"
        ".av{width:40px;height:40px;border-radius:50%}.head b{color:#fff}"
        ".bot{background:#5865f2;color:#fff;font-size:10px;padding:1px 4px;border-radius:3px;margin-left:6px}"
        ".ts{color:#949ba4;font-size:12px;margin-left:8px}"
        ".emb{border-left:4px solid #e11d48;background:#2b2d31;padding:8px 12px;margin-top:4px;border-radius:4px;max-width:560px}"
        ".file a{color:#00a8fc}"
        "</style></head><body>"
        f"<div class='top'><h1>{_h.escape(title)}</h1><p>{_h.escape(guild_name)} · "
        f"{len(rows)} messages</p></div>{''.join(msgs)}</body></html>"
    )


async def _build_transcript(channel: discord.TextChannel) -> str:
    return _render_text(await _collect_history(channel))


# ---- post-close rating ------------------------------------------------------ #

class RatingView(discord.ui.View):
    """⭐ 1–5 buttons in the customer's DM after a ticket closes."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        for n in range(1, 6):
            b = discord.ui.Button(
                label="⭐" * n if n <= 3 else f"{n} ⭐",
                style=discord.ButtonStyle.success if n >= 4 else discord.ButtonStyle.secondary,
                custom_id=f"rate:{n}",
            )
            b.callback = self._make_cb(n)
            self.add_item(b)

    def _make_cb(self, n: int):
        async def cb(interaction: discord.Interaction) -> None:
            pending = DATA.setdefault("rating_msgs", {})
            link = pending.pop(str(interaction.message.id), None)
            if not link:
                await interaction.response.edit_message(
                    content="Thanks — this ticket was already rated. 🙏", view=None
                )
                return
            g = bot.get_guild(link["guild_id"])
            store = guild_store(DATA, link["guild_id"])
            order = next(
                (o for o in store["orders"] if o.get("id") == link["order_id"]), None
            )
            if order is not None:
                order["rating"] = n
            save_data(DATA)
            await interaction.response.edit_message(
                content=f"Thanks for rating us **{'⭐' * n}** ({n}/5)! 🙏", view=None
            )
            if g is not None and order is not None:
                await send_log(
                    g, store,
                    f"⭐ Ticket #{order['id']} rated **{n}/5** by <@{order['user_id']}>.",
                    category="tickets",
                    fields=[
                        ("Handled by", f"<@{order['claimed_by']}>" if order.get("claimed_by") else "—", True),
                        ("Type", order.get("type") or "—", True),
                    ],
                )
        return cb


# ---- closing ----------------------------------------------------------------- #

async def _close_ticket_core(
    guild: discord.Guild,
    channel: discord.TextChannel,
    closer: "discord.abc.User",
    store: dict[str, Any],
    order: dict | None,
    reason: str | None = None,
) -> None:
    """Transcript (HTML + text) → transcript channel, short DM → customer,
    log, then delete the channel. Works from a click or the auto-close loop."""
    oid = order["id"] if order else channel.name
    ttype = (order.get("type") if order else "Ticket") or "Ticket"
    rows = await _collect_history(channel)
    text = _render_text(rows)
    html_doc = _render_html(rows, f"{ttype} #{oid}", guild.name)
    reason = (reason or "").strip() or None
    open_for = _open_for(order)

    def files() -> list[discord.File]:
        return [
            discord.File(io.BytesIO(html_doc.encode("utf-8")), filename=f"ticket-{oid}.html"),
            discord.File(io.BytesIO(text.encode("utf-8")), filename=f"ticket-{oid}.txt"),
        ]

    summary = discord.Embed(
        title=f"🧾  Transcript · {ttype} #{oid}",
        description="Open the **.html** file for a Discord-style view of the whole ticket.",
        color=EMBED_COLOR,
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )
    if order:
        summary.add_field(name="Customer", value=f"<@{order['user_id']}>", inline=True)
        summary.add_field(name="Paid", value="🟢 Yes" if order.get("paid") else "🔴 No", inline=True)
        summary.add_field(name="Priority", value=_prio_label(order), inline=True)
        if order.get("claimed_by"):
            summary.add_field(name="Handled by", value=f"<@{order['claimed_by']}>", inline=True)
        summary.add_field(name="Items", value=order.get("guns") or "—", inline=False)
    if reason:
        summary.add_field(name="Reason", value=reason[:1024], inline=False)
    if open_for:
        summary.add_field(name="Open for", value=open_for, inline=True)
    summary.add_field(name="Closed by", value=getattr(closer, "mention", str(closer)), inline=True)
    summary.add_field(name="Messages", value=str(len(rows)), inline=True)
    _brand(summary, guild)

    dest_id = (
        store.get("transcript_channel_id")
        or store.get("ticket_log_channel_id")
        or store.get("log_channel_id")
    )
    dest = guild.get_channel(dest_id) if dest_id else None
    if dest is not None:
        try:
            await dest.send(embed=summary, files=files())
        except discord.HTTPException:
            pass

    if order is not None:
        order["status"] = "closed"
        order["closed_ts"] = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        order["closed_by"] = getattr(closer, "id", None)
        save_data(DATA)

        member = guild.get_member(order["user_id"])
        if member is None:
            try:
                member = await guild.fetch_member(order["user_id"])
            except (discord.NotFound, discord.HTTPException):
                member = None
        if member is not None:
            dm = discord.Embed(
                title=f"🔒  Your {ttype} ticket is closed",
                description=(
                    f"Thanks for dealing with **{guild.name}**! The full chat log is attached.\n\n"
                    "**How did we do?** Tap a rating below — it goes straight to the team."
                ),
                color=EMBED_COLOR,
                timestamp=datetime.datetime.now(datetime.timezone.utc),
            )
            dm.add_field(name="Ticket", value=f"#{oid}", inline=True)
            dm.add_field(name="Paid", value="🟢 Yes" if order.get("paid") else "🔴 No", inline=True)
            if open_for:
                dm.add_field(name="Open for", value=open_for, inline=True)
            if order.get("claimed_by"):
                dm.add_field(name="Handled by", value=f"<@{order['claimed_by']}>", inline=True)
            if order.get("guns"):
                dm.add_field(name="Items", value=order["guns"][:1024], inline=False)
            if reason:
                dm.add_field(name="Reason", value=reason[:1024], inline=False)
            if guild.icon:
                dm.set_author(name=guild.name, icon_url=guild.icon.url)
                dm.set_thumbnail(url=guild.icon.url)
            else:
                dm.set_author(name=guild.name)
            _brand(dm, guild, "Full chat log attached")
            try:
                sent = await member.send(
                    embed=dm,
                    file=discord.File(io.BytesIO(text.encode("utf-8")), filename=f"ticket-{oid}.txt"),
                    view=RatingView(),
                )
                # remember which ticket this DM rates (RatingView looks it up)
                pending = DATA.setdefault("rating_msgs", {})
                pending[str(sent.id)] = {"guild_id": guild.id, "order_id": order["id"]}
                for old in list(pending)[:-500]:  # keep the backlog bounded
                    pending.pop(old, None)
                save_data(DATA)
            except discord.HTTPException:
                pass  # customer has DMs closed

        await send_log(
            guild,
            store,
            f"🔒 **{order.get('type', 'order')}** #{order['id']} closed.",
            category="tickets",
            actor=closer if isinstance(closer, (discord.Member, discord.User)) else None,
            fields=[
                ("Order", f"#{order['id']}", True),
                ("Customer", f"<@{order['user_id']}>", True),
                ("Paid", "🟢 Yes" if order.get("paid") else "🔴 No", True),
                ("Open for", open_for or "—", True),
                ("Items", order.get("guns") or "—", False),
                ("Reason", reason or "—", False),
            ],
        )

    gone = int(datetime.datetime.now(datetime.timezone.utc).timestamp()) + 6
    who = getattr(closer, "display_name", None) or str(closer)
    bye = discord.Embed(
        title="🔒  Ticket closed",
        description=(
            f"Closed by **{who}**" + (f" — {reason}" if reason else "") + ".\n"
            f"🧾 Transcript saved · this channel deletes <t:{gone}:R>"
        ),
        color=MUTED,
    )
    try:
        await _send_card(channel, [bye])
    except discord.HTTPException:
        pass
    await asyncio.sleep(5)
    try:
        await channel.delete(reason=f"Ticket closed by {closer}")
    except discord.HTTPException:
        pass


async def _close_ticket(
    interaction: discord.Interaction,
    store: dict[str, Any],
    order: dict | None,
    reason: str | None = None,
) -> None:
    await _close_ticket_core(
        interaction.guild, interaction.channel, interaction.user, store, order, reason
    )


async def create_order(
    guild: "discord.Guild",
    store: dict,
    type_name: str,
    owner: "discord.Member | None",
    guns: str,
    quantity: str,
    notes: str,
    *,
    fallback_channel=None,
    actor=None,
) -> dict:
    """Open a ticket: make the private channel, record the order, post the order
    card (with its buttons), and log it. Shared by the Discord order modal and
    the website's 'new order' button. Returns {ok, order, channel} or
    {ok: False, error}. `owner` is the customer the ticket is for."""
    me = guild.me
    if me is None or not me.guild_permissions.manage_channels:
        return {"ok": False, "error": "I need the **Manage Channels** permission to open tickets."}

    ttype = _find_ticket_type(store, type_name)
    oid = _new_order_id(store)

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    if owner is not None:
        overwrites[owner] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    lead = _leadership_role(guild, store)
    if lead is not None:
        overwrites[lead] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

    category = None
    if ttype and ttype.get("category_id"):
        category = guild.get_channel(ttype["category_id"])
    if category is None and store.get("ticket_category_id"):
        category = guild.get_channel(store["ticket_category_id"])
    if category is None and fallback_channel is not None:
        category = fallback_channel.category

    cust_name = owner.display_name if owner is not None else None
    chname = _compose_channel_name(oid, type_name, False, cust_name)
    try:
        channel = await guild.create_text_channel(
            chname,
            overwrites=overwrites,
            category=category,
            reason=f"{type_name} order" + (f" by {owner}" if owner is not None else " (website)"),
        )
    except discord.Forbidden:
        return {"ok": False, "error": "Couldn't open a channel — check my Manage Channels permission and role position."}
    except discord.HTTPException:
        return {"ok": False, "error": "Something went wrong opening the ticket channel."}

    order = {
        "id": oid,
        "type": type_name,
        "user_id": owner.id if owner is not None else None,
        "guns": (guns or "").strip(),
        "quantity": (quantity or "").strip(),
        "notes": (notes or "").strip(),
        "paid": False,
        "status": "open",
        "claimed_by": None,
        "channel_id": channel.id,
        "created_at": now_stamp(),
        "created_ts": int(datetime.datetime.now(datetime.timezone.utc).timestamp()),
    }
    store["orders"].append(order)
    save_data(DATA)

    ping = ((f"<@{owner.id}>" if owner is not None else "") + (f" {lead.mention}" if lead else "")).strip()
    try:
        msg = await _send_card(
            channel,
            _order_cards(order, guild),
            lambda: OrderTicketView(order),
            header=ping or None,
            allowed_mentions=discord.AllowedMentions(users=True, roles=True),
        )
        if msg is not None:
            order["message_id"] = msg.id
            save_data(DATA)
    except discord.HTTPException:
        pass

    await send_log(
        guild, store,
        f"🆕 New **{type_name}** ticket opened" + (" via the website" if owner is None or actor is None else "") + ".",
        category="tickets", actor=actor, channel_ctx=channel,
        fields=[
            ("Order", f"#{oid}", True),
            ("Type", type_name, True),
            ("Items", order["guns"] or "—", False),
            ("Quantity", order["quantity"] or "—", True),
        ],
    )
    return {"ok": True, "order": order, "channel": channel}


class OrderModal(discord.ui.Modal):
    def __init__(self, ticket_type: str) -> None:
        super().__init__(title=f"{ticket_type} Order"[:45])
        self.ticket_type = ticket_type
        self.guns = discord.ui.TextInput(
            label="What do you want?",
            placeholder="e.g. AK-47, Combat Pistol, parts…",
            style=discord.TextStyle.paragraph,
            max_length=500,
        )
        self.quantity = discord.ui.TextInput(
            label="How many? (per item)",
            placeholder="e.g. AK x2, Pistol x5",
            max_length=200,
        )
        self.notes = discord.ui.TextInput(
            label="Notes (optional)",
            placeholder="Delivery spot, timing, budget…",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=500,
        )
        self.add_item(self.guns)
        self.add_item(self.quantity)
        self.add_item(self.notes)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # Ack immediately — creating the channel is an API call that can take
        # longer than Discord's 3s window, which would otherwise 404 the modal.
        await interaction.response.defer(ephemeral=True)
        store = guild_store(DATA, interaction.guild_id)
        res = await create_order(
            interaction.guild, store, self.ticket_type, interaction.user,
            self.guns.value, self.quantity.value, self.notes.value,
            fallback_channel=interaction.channel, actor=interaction.user,
        )
        if not res["ok"]:
            await interaction.followup.send(res["error"], ephemeral=True)
            return
        await interaction.followup.send(
            f"Your {self.ticket_type} ticket is open: {res['channel'].mention} ✅",
            ephemeral=True,
        )


class TicketPanelSelect(discord.ui.Select):
    """Dropdown of configured ticket types; opens the order modal for the pick."""

    def __init__(self, options: list[discord.SelectOption] | None = None) -> None:
        empty = not options
        opts = options or [discord.SelectOption(label="—", value="0")]
        super().__init__(
            custom_id="ticket:open",
            placeholder="No active orders right now" if empty else "Open a ticket…",
            min_values=1,
            max_values=1,
            options=opts,
            disabled=empty,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        store = guild_store(DATA, interaction.guild_id)
        if not store.get("tickets_open", True):
            await interaction.response.send_message(
                "🔒 Ticket orders are currently **closed**. Check back later.",
                ephemeral=True,
            )
            await _reset_panel_select(interaction, store)
            return
        chosen = self.values[0]
        if _find_ticket_type(store, chosen) is None:
            await interaction.response.send_message(
                "That ticket type no longer exists.", ephemeral=True
            )
            await _reset_panel_select(interaction, store)
            return
        # one open ticket per member per type — send them to the existing one
        for o in store["orders"]:
            if (
                o.get("user_id") == interaction.user.id
                and o.get("type") == chosen
                and o.get("status", "open") != "closed"
            ):
                ch = interaction.guild.get_channel(o.get("channel_id") or 0)
                if ch is not None:
                    await interaction.response.send_message(
                        f"You already have an open **{chosen}** ticket: {ch.mention}",
                        ephemeral=True,
                    )
                    await _reset_panel_select(interaction, store)
                    return
        await interaction.response.send_modal(OrderModal(chosen))
        # Reset the dropdown so the same option can be picked again — otherwise a
        # single-option menu stays "selected" and Discord won't fire a new pick.
        await _reset_panel_select(interaction, store)


class TicketPanelView(GatedView):
    def __init__(self, options: list[discord.SelectOption] | None = None) -> None:
        super().__init__(timeout=None)
        self.add_item(TicketPanelSelect(options))


async def _reset_panel_select(interaction: discord.Interaction, store: dict) -> None:
    """Re-attach a fresh panel dropdown to the message so its selection clears
    and the same option can be opened again. Best-effort."""
    msg = getattr(interaction, "message", None)
    if msg is None:
        return
    try:
        embed, options = _build_ticket_panel(interaction.guild, store, store.get("ticket_panel_message"))
        await _edit_card(msg, [embed], lambda: TicketPanelView(options))
    except (discord.HTTPException, AttributeError):
        pass


class OrderTicketView(GatedView):
    """Staff buttons on a ticket: Claim, Mark Paid/Unpaid, Priority and Close.
    Pass the order so the buttons show its state: Claim turns into Take Over once
    someone has it, Paid flips to Unpaid, Priority wears the current level's
    colour (🟢 🔵 🟠 🔴) — short labels keep all four on one row."""

    def __init__(self, order: dict | None = None) -> None:
        super().__init__(timeout=None)
        if order is None:
            return
        for c in self.children:
            cid = getattr(c, "custom_id", None)
            if cid == "order:paid":
                if order.get("paid"):
                    c.label, c.style, c.emoji = "Mark Unpaid", discord.ButtonStyle.secondary, "↩️"
                else:
                    c.label, c.style, c.emoji = "Mark Paid", discord.ButtonStyle.success, "💰"
            elif cid == "order:claim" and order.get("claimed_by"):
                c.label, c.style, c.emoji = "Take Over", discord.ButtonStyle.secondary, "🔁"
            elif cid == "order:priority":
                c.emoji = _prio_label(order).partition(" ")[0]

    @discord.ui.button(
        label="Claim",
        style=discord.ButtonStyle.primary,
        emoji="🙋",
        custom_id="order:claim",
    )
    async def claim(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if await _deny(interaction, "claim tickets", who="staff"):
            return
        await interaction.response.defer()
        store = guild_store(DATA, interaction.guild_id)
        order = _find_order_by_channel(store, interaction.channel_id)
        if order is None:
            await interaction.followup.send(
                "Couldn't find this order.", ephemeral=True
            )
            return
        prev = order.get("claimed_by")
        order["claimed_by"] = interaction.user.id
        save_data(DATA)
        await _edit_card(
            interaction.message, _order_cards(order, interaction.guild),
            lambda: OrderTicketView(order),
        )
        took_over = bool(prev and prev != interaction.user.id)
        note = discord.Embed(
            description=(
                f"{'🔁' if took_over else '🙋'} **{interaction.user.display_name}** "
                f"{'took over' if took_over else 'claimed'} this ticket.\n"
                "-# They'll handle it from here."
            ),
            color=SKY,
        )
        note.set_thumbnail(url=interaction.user.display_avatar.url)
        try:
            await _send_card(interaction.channel, [note])
        except discord.HTTPException:
            pass
        await interaction.followup.send(
            "You're now handling this ticket. 🙋", ephemeral=True
        )
        await send_log(
            interaction.guild,
            store,
            f"🙋 **{order.get('type', 'order')}** #{order['id']} claimed.",
            category="tickets",
            actor=interaction.user,
            channel_ctx=interaction.channel,
            fields=[
                ("Order", f"#{order['id']}", True),
                ("Customer", f"<@{order['user_id']}>", True),
            ],
        )

    @discord.ui.button(
        label="Mark Paid",
        style=discord.ButtonStyle.success,
        emoji="💰",
        custom_id="order:paid",
    )
    async def mark_paid(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if await _deny(interaction, "mark orders paid", who="staff"):
            return
        await interaction.response.defer()
        store = guild_store(DATA, interaction.guild_id)
        order = _find_order_by_channel(store, interaction.channel_id)
        if order is None:
            await interaction.followup.send(
                "Couldn't find this order.", ephemeral=True
            )
            return
        order["paid"] = not order.get("paid", False)
        paid = order["paid"]
        save_data(DATA)
        await _edit_card(
            interaction.message, _order_cards(order, interaction.guild),
            lambda: OrderTicketView(order),
        )
        await _rename_order_channel(interaction.guild, order)
        await interaction.followup.send(
            f"Order #{order['id']} marked **{'paid 💰' if paid else 'unpaid'}**.", ephemeral=True
        )
        await send_log(
            interaction.guild,
            store,
            f"{'💰' if paid else '↩️'} **{order.get('type', 'order')}** #{order['id']} marked **{'PAID' if paid else 'UNPAID'}**.",
            category="orders",
            actor=interaction.user,
            channel_ctx=interaction.channel,
            fields=[
                ("Order", f"#{order['id']}", True),
                ("Customer", f"<@{order['user_id']}>", True),
                ("Items", order.get("guns") or "—", False),
            ],
        )

    @discord.ui.button(
        label="Priority",
        style=discord.ButtonStyle.secondary,
        emoji="🚩",
        custom_id="order:priority",
    )
    async def priority(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if await _deny(interaction, "set priority", who="staff"):
            return
        store = guild_store(DATA, interaction.guild_id)
        order = _find_order_by_channel(store, interaction.channel_id)
        if order is None:
            await interaction.response.send_message("Couldn't find this order.", ephemeral=True)
            return
        keys = [k for k, _ in PRIORITIES]
        cur = order.get("priority", "normal")
        order["priority"] = keys[(keys.index(cur) + 1) % len(keys)] if cur in keys else "high"
        save_data(DATA)
        await _edit_card(
            interaction.message,
            _order_cards(order, interaction.guild),
            lambda: OrderTicketView(order),
            interaction=interaction,
        )
        await send_log(
            interaction.guild, store,
            f"🚩 Ticket #{order['id']} priority → **{_prio_label(order)}**.",
            category="tickets", actor=interaction.user, channel_ctx=interaction.channel,
        )

    @discord.ui.button(
        label="Close",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="order:close",
    )
    async def close_order(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        store = guild_store(DATA, interaction.guild_id)
        order = _find_order_by_channel(store, interaction.channel_id)
        is_owner = order is not None and order.get("user_id") == interaction.user.id
        if not (is_leadership(interaction.user) or is_owner):
            await interaction.response.send_message(
                "Only staff or the ticket owner can close this.", ephemeral=True
            )
            return
        if order is not None and order.get("status") == "closed":
            await interaction.response.send_message(
                "This ticket is already closing.", ephemeral=True
            )
            return
        if order is not None:
            order["status"] = "closed"  # claim it now so a double-click can't re-fire
            save_data(DATA)
        await interaction.response.defer()
        await interaction.followup.send(
            "Closing — transcript saved, channel will delete shortly. 🔒",
            ephemeral=True,
        )
        await _close_ticket(interaction, store, order)


async def _refresh_order_card(guild, order: dict) -> None:
    """Re-render the order card in its ticket channel (status, items, buttons) to
    match the current order — used when the website changes paid status, so the
    Discord card stays in sync. Best-effort."""
    cid = order.get("channel_id")
    mid = order.get("message_id")
    if not cid or not mid:
        return
    ch = guild.get_channel(cid)
    if ch is None:
        return
    try:
        msg = await ch.fetch_message(mid)
        await _edit_card(msg, _order_cards(order, guild), lambda: OrderTicketView(order))
    except discord.HTTPException:
        pass


TICKET_PANEL_VIEW = TicketPanelView()
ORDER_TICKET_VIEW = OrderTicketView()


def _build_ticket_panel(guild, store, message: str | None = None):
    """Build the ticket-panel embed + dropdown options from the current ticket
    types. Shared by `/ticket panel` and the auto-refresh."""
    options: list[discord.SelectOption] = []
    menu_lines: list[str] = []
    for t in store.get("ticket_types", []):
        em = t.get("emoji") or "🎫"
        options.append(
            discord.SelectOption(label=t["name"][:100], value=t["name"], emoji=t.get("emoji") or None)
        )
        menu_lines.append(f"{em}  **{t['name']}**")
    if options:
        embed = discord.Embed(
            title="🎫  Open a Ticket",
            description=message
            or "Need hardware or a job done? Pick an option from the menu below and a "
            "**private ticket** opens just for you and staff.",
            color=EMBED_COLOR,
        )
        embed.add_field(name="What we handle", value="\n".join(menu_lines), inline=False)
        embed.add_field(
            name="How it works",
            value=(
                "1️⃣  Pick a category below\n"
                "2️⃣  Tell us what you want & how many\n"
                "3️⃣  A private channel opens — staff take it from there"
            ),
            inline=False,
        )
    else:
        # No order types configured — show a clean "nothing open" state.
        embed = discord.Embed(
            title="🎫  Orders",
            description="🔕  **No active orders right now.**\nCheck back soon — order types will appear here when they're open.",
            color=MUTED,
        )
    if getattr(guild, "icon", None):
        embed.set_thumbnail(url=guild.icon.url)
    _hero(embed)
    _brand(embed, guild, "Tickets are private · one click to open")
    return embed, options


async def _refresh_ticket_panel(guild, store) -> bool:
    """Re-edit the posted ticket panel in place so its dropdown matches the
    current types. No-op (returns False) if no panel has been posted or it was
    deleted. Best-effort — never raises."""
    cid = store.get("ticket_panel_channel_id")
    mid = store.get("ticket_panel_message_id")
    if not cid or not mid:
        return False
    channel = guild.get_channel(cid)
    if channel is None:
        return False
    try:
        msg = await channel.fetch_message(mid)
    except discord.HTTPException:
        store["ticket_panel_channel_id"] = None
        store["ticket_panel_message_id"] = None
        save_data(DATA)
        return False
    embed, options = _build_ticket_panel(guild, store, store.get("ticket_panel_message"))
    try:
        await _edit_card(msg, [embed], lambda: TicketPanelView(options))
    except discord.HTTPException:
        return False
    return True

ticket_group = app_commands.Group(
    name="ticket", description="Gun order / vendor / crafting ticket system"
)


async def ticket_type_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    store = guild_store(DATA, interaction.guild_id)
    cur = current.lower()
    return [
        app_commands.Choice(name=t["name"], value=t["name"])
        for t in store.get("ticket_types", [])
        if cur in t["name"].lower()
    ][:25]


@ticket_group.command(
    name="type_add",
    description="Add a ticket type and the category its tickets open under (leadership)",
)
@app_commands.describe(
    name="Type name, e.g. 'Gang Orders', 'Vendors', 'Gun Crafting'",
    category="Channel category the tickets of this type should open under",
    emoji="Optional emoji shown in the dropdown",
)
async def ticket_type_add(
    interaction: discord.Interaction,
    name: str,
    category: discord.CategoryChannel | None = None,
    emoji: str | None = None,
) -> None:
    if await _deny(interaction, "add ticket types"):
        return
    store = guild_store(DATA, interaction.guild_id)
    types = store["ticket_types"]
    name_clean = name.strip()
    entry = {
        "name": name_clean,
        "category_id": category.id if category else None,
        "emoji": (emoji.strip() if emoji else None),
    }
    for t in types:
        if t["name"].lower() == name_clean.lower():
            t.update(entry)
            break
    else:
        if len(types) >= 25:
            await interaction.response.send_message(
                "You can have at most 25 ticket types.", ephemeral=True
            )
            return
        types.append(entry)
    save_data(DATA)
    refreshed = False
    try:
        refreshed = await _refresh_ticket_panel(interaction.guild, store)
    except discord.HTTPException:
        pass
    where = f" under {category.mention}" if category else ""
    tail = "The panel updated automatically. ✅" if refreshed else "Run `/ticket panel` to (re)post the menu. ✅"
    await interaction.response.send_message(
        f"Ticket type **{name_clean}** saved{where}. {tail}",
        ephemeral=True,
    )


@ticket_group.command(
    name="type_remove", description="Remove a ticket type (leadership)"
)
@app_commands.describe(name="The ticket type to remove")
@app_commands.autocomplete(name=ticket_type_autocomplete)
async def ticket_type_remove(interaction: discord.Interaction, name: str) -> None:
    if await _deny(interaction, "remove ticket types"):
        return
    store = guild_store(DATA, interaction.guild_id)
    before = len(store["ticket_types"])
    store["ticket_types"] = [
        t for t in store["ticket_types"] if t["name"].lower() != name.lower()
    ]
    if len(store["ticket_types"]) == before:
        await interaction.response.send_message(
            f"No ticket type called **{name}**.", ephemeral=True
        )
        return
    save_data(DATA)
    refreshed = False
    try:
        refreshed = await _refresh_ticket_panel(interaction.guild, store)
    except discord.HTTPException:
        pass
    tail = "Panel updated automatically. 🗑️" if refreshed else "Repost with `/ticket panel`. 🗑️"
    await interaction.response.send_message(
        f"Removed ticket type **{name}**. {tail}",
        ephemeral=True,
    )
    await send_log(
        interaction.guild,
        store,
        f"🎫 Ticket type **{name}** removed.",
        category="tickets",
        actor=interaction.user,
    )


@ticket_group.command(
    name="type_clear",
    description="Remove ALL ticket types at once (leadership)",
)
async def ticket_type_clear(interaction: discord.Interaction) -> None:
    if await _deny(interaction, "clear ticket types"):
        return
    store = guild_store(DATA, interaction.guild_id)
    count = len(store["ticket_types"])
    if count == 0:
        await interaction.response.send_message(
            "There are no ticket types to remove.", ephemeral=True
        )
        return
    store["ticket_types"] = []
    save_data(DATA)
    await interaction.response.send_message(
        f"Cleared **{count}** ticket type{'s' if count != 1 else ''}. "
        "Repost the menu with `/ticket panel`. 🗑️",
        ephemeral=True,
    )
    await send_log(
        interaction.guild,
        store,
        f"🎫 All ticket types cleared ({count}).",
        category="tickets",
        actor=interaction.user,
    )


@ticket_group.command(name="type_list", description="Show the configured ticket types")
async def ticket_type_list(interaction: discord.Interaction) -> None:
    store = guild_store(DATA, interaction.guild_id)
    types = store["ticket_types"]
    embed = discord.Embed(title="🎫 Ticket Types", color=EMBED_COLOR)
    if not types:
        embed.description = "No ticket types yet. Add some with `/ticket type_add`."
    else:
        lines = []
        for t in types:
            cat = f" → <#{t['category_id']}>" if t.get("category_id") else ""
            emoji = f"{t['emoji']} " if t.get("emoji") else ""
            lines.append(f"{emoji}**{t['name']}**{cat}")
        embed.description = "\n".join(lines)
    await _send_v2(interaction, embed, ephemeral=True)


@ticket_group.command(
    name="set_category",
    description="Choose the category new tickets open under (leadership)",
)
@app_commands.describe(
    category="Channel category tickets open under (leave empty to clear)"
)
async def ticket_set_category(
    interaction: discord.Interaction,
    category: discord.CategoryChannel | None = None,
) -> None:
    if await _deny(interaction, "set the ticket category"):
        return
    store = guild_store(DATA, interaction.guild_id)
    store["ticket_category_id"] = category.id if category else None
    save_data(DATA)
    where = (
        category.mention
        if category
        else "`cleared` — tickets open under the panel channel's category"
    )
    await interaction.response.send_message(
        f"New tickets will now open under {where}. ✅\n"
        "(A ticket **type** with its own category still overrides this.)",
        ephemeral=True,
    )


@ticket_group.command(
    name="panel",
    description="Post the ticket menu (one dropdown with all types) (leadership)",
)
@app_commands.describe(message="Optional text shown above the menu")
async def ticket_panel(
    interaction: discord.Interaction, message: str | None = None
) -> None:
    if await _deny(interaction, "post the ticket panel"):
        return
    store = guild_store(DATA, interaction.guild_id)
    if not store.get("ticket_types"):
        await interaction.response.send_message(
            "Add at least one ticket type first with `/ticket type_add`.",
            ephemeral=True,
        )
        return
    embed, options = _build_ticket_panel(interaction.guild, store, message)
    msg = await _send_card(interaction.channel, [embed], lambda: TicketPanelView(options))
    # remember where the panel lives so type changes can refresh it in place
    if msg is not None:
        store["ticket_panel_channel_id"] = interaction.channel.id
        store["ticket_panel_message_id"] = msg.id
        store["ticket_panel_message"] = message
        save_data(DATA)
    await interaction.response.send_message(
        "Ticket panel posted. ✅ It updates on its own when you add or remove types.",
        ephemeral=True,
    )


@ticket_group.command(
    name="toggle",
    description="Open or close new ticket orders for everyone (leadership)",
)
async def ticket_toggle(interaction: discord.Interaction) -> None:
    if await _deny(interaction, "open/close ticket orders"):
        return
    store = guild_store(DATA, interaction.guild_id)
    now_open = not store.get("tickets_open", True)
    store["tickets_open"] = now_open
    save_data(DATA)
    state = "🟢 **open** — people can order again" if now_open else "🔒 **closed** — no new orders"
    await interaction.response.send_message(f"Ticket orders are now {state}.", ephemeral=True)
    await send_log(
        interaction.guild,
        store,
        f"🎫 Ticket orders turned **{'OPEN' if now_open else 'CLOSED'}**.",
        category="tickets",
        actor=interaction.user,
    )


@ticket_group.command(
    name="board",
    description="Show the board of active orders, grouped by type (staff)",
)
async def ticket_board(interaction: discord.Interaction) -> None:
    if await _deny(interaction, "view the orders board", who="staff"):
        return
    store = guild_store(DATA, interaction.guild_id)
    await interaction.response.send_message(
        embed=_brand(_orders_board_embed(store), interaction.guild)
    )


async def _ticket_member_access(
    interaction: discord.Interaction, member: discord.Member, allow: bool
) -> None:
    if await _deny(interaction, "change who's in a ticket", who="staff"):
        return
    store = guild_store(DATA, interaction.guild_id)
    order = _find_order_by_channel(store, interaction.channel_id)
    if order is None:
        await interaction.response.send_message(
            "Run this inside a ticket channel.", ephemeral=True
        )
        return
    if not allow and member.id == order.get("user_id"):
        await interaction.response.send_message(
            "You can't remove the ticket owner — close the ticket instead.",
            ephemeral=True,
        )
        return
    try:
        if allow:
            await interaction.channel.set_permissions(
                member, view_channel=True, send_messages=True, read_message_history=True,
                reason=f"Added to ticket by {interaction.user}",
            )
        else:
            await interaction.channel.set_permissions(
                member, overwrite=None, reason=f"Removed from ticket by {interaction.user}"
            )
    except discord.Forbidden:
        await interaction.response.send_message(
            "I can't edit this channel's permissions — check my **Manage Channels**.",
            ephemeral=True,
        )
        return
    verb = "added to" if allow else "removed from"
    await interaction.response.send_message(f"{member.mention} {verb} this ticket. ✅")
    await send_log(
        interaction.guild,
        store,
        f"🎫 {member.mention} {verb} ticket #{order['id']}.",
        category="tickets",
        actor=interaction.user,
        channel_ctx=interaction.channel,
    )


@ticket_group.command(name="add", description="Add someone to the ticket you're in (staff)")
@app_commands.describe(member="Who to let into this ticket")
async def ticket_add(interaction: discord.Interaction, member: discord.Member) -> None:
    await _ticket_member_access(interaction, member, allow=True)


@ticket_group.command(
    name="remove", description="Remove someone from the ticket you're in (staff)"
)
@app_commands.describe(member="Who to take out of this ticket")
async def ticket_remove(interaction: discord.Interaction, member: discord.Member) -> None:
    await _ticket_member_access(interaction, member, allow=False)


# ---- auto-close inactive tickets -------------------------------------------- #

@tasks.loop(minutes=15)
async def ticket_autoclose_loop() -> None:
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    for guild in bot.guilds:
        if not guild_active(guild.id):
            continue  # paused until the server's subscription is active
        store = guild_store(DATA, guild.id)
        hours = store.get("ticket_autoclose_hours") or 0
        if hours <= 0:
            continue
        idle_limit = hours * 3600
        grace = max(3600, min(12 * 3600, idle_limit // 2))
        for order in list(store["orders"]):
            if order.get("status", "open") == "closed" or not order.get("channel_id"):
                continue
            ch = guild.get_channel(order["channel_id"])
            if ch is None:
                continue
            last_id = ch.last_message_id
            last_ts = (
                discord.utils.snowflake_time(last_id).timestamp()
                if last_id
                else order.get("created_ts", now)
            )
            warn_id = order.get("autoclose_warn_msg")
            if warn_id and last_id and last_id != warn_id:
                # someone talked after the warning — reset
                order.pop("autoclose_warn_msg", None)
                order.pop("autoclose_warned_ts", None)
                save_data(DATA)
                warn_id = None
            if not warn_id:
                if now - last_ts >= idle_limit:
                    try:
                        w = await ch.send(
                            content=f"<@{order['user_id']}>",
                            embed=discord.Embed(
                                description=(
                                    f"⏰ This ticket has been quiet for **{hours}h**. It will "
                                    f"**auto-close in {grace // 3600}h** unless someone replies."
                                ),
                                color=WARN,
                            ),
                        )
                        order["autoclose_warn_msg"] = w.id
                        order["autoclose_warned_ts"] = int(now)
                        save_data(DATA)
                    except discord.HTTPException:
                        pass
            elif now - order.get("autoclose_warned_ts", now) >= grace:
                order["status"] = "closed"
                save_data(DATA)
                await _close_ticket_core(
                    guild, ch, guild.me, store, order,
                    f"Auto-closed after {hours}h of inactivity",
                )


@ticket_autoclose_loop.before_loop
async def _before_autoclose() -> None:
    await bot.wait_until_ready()


@ticket_autoclose_loop.error
async def _autoclose_error(exc: BaseException) -> None:
    log.exception("Auto-close loop error: %s", exc)


@ticket_group.command(
    name="autoclose",
    description="Auto-close tickets after N hours of no messages (0 = off) (leadership)",
)
@app_commands.describe(hours="Hours of silence before a warning, then auto-close (0 turns it off)")
async def ticket_autoclose(interaction: discord.Interaction, hours: app_commands.Range[int, 0, 720]) -> None:
    if await _deny(interaction, "change auto-close"):
        return
    store = guild_store(DATA, interaction.guild_id)
    store["ticket_autoclose_hours"] = hours
    save_data(DATA)
    if hours:
        grace = max(1, min(12, hours // 2))
        msg = (
            f"⏰ Auto-close **on**: after **{hours}h** with no messages a ticket gets a "
            f"warning, then closes **{grace}h** later if nobody replies."
        )
    else:
        msg = "⏰ Auto-close **off**."
    await interaction.response.send_message(msg, ephemeral=True)
    await send_log(
        interaction.guild, store, f"⏰ Ticket auto-close set to **{hours or 'off'}**{'h' if hours else ''}.",
        category="tickets", actor=interaction.user,
    )


@ticket_group.command(name="stats", description="Ticket analytics & staff leaderboard (staff)")
async def ticket_stats(interaction: discord.Interaction) -> None:
    if await _deny(interaction, "view stats", who="staff"):
        return
    store = guild_store(DATA, interaction.guild_id)
    orders = store["orders"]
    closed = [o for o in orders if o.get("status") == "closed"]
    open_n = len(orders) - len(closed)
    paid = sum(1 for o in orders if o.get("paid"))
    durations = [
        o["closed_ts"] - o["created_ts"] for o in closed if o.get("closed_ts") and o.get("created_ts")
    ]

    def fmt_dur(secs: float) -> str:
        h, rem = divmod(int(secs), 3600)
        return f"{h}h {rem // 60}m" if h else f"{rem // 60}m"

    daily = _daily_counts([o["created_ts"] for o in orders if o.get("created_ts")])
    summary = discord.Embed(
        title="📊  Ticket Analytics",
        description=f"**{open_n}** open · **{len(closed)}** closed · 💰 **{paid}** paid",
        color=EMBED_COLOR,
    )
    summary.add_field(name="⏱️ Avg time open", value=fmt_dur(sum(durations) / len(durations)) if durations else "—")
    ratings = [o["rating"] for o in orders if o.get("rating")]
    summary.add_field(
        name="⭐ Customer rating",
        value=f"**{sum(ratings) / len(ratings):.1f}**/5 · {len(ratings)} rated" if ratings else "—",
    )
    summary.add_field(
        name="📈 Opened per day · last 14d",
        value=f"`{_spark(daily)}`  peak **{max(daily)}**/day · **{sum(daily)}** total",
        inline=False,
    )

    board: dict[int, dict[str, Any]] = {}
    for o in closed:
        h = o.get("claimed_by")
        if h:
            b = board.setdefault(h, {"n": 0, "stars": []})
            b["n"] += 1
            if o.get("rating"):
                b["stars"].append(o["rating"])
    lb = discord.Embed(title="🏆  Staff Leaderboard", color=WARN)
    if board:
        medals = ["🥇", "🥈", "🥉"]
        ranked = sorted(board.items(), key=lambda kv: -kv[1]["n"])[:10]
        top = ranked[0][1]["n"]

        def stars(b: dict) -> str:
            s = b["stars"]
            return f" · ⭐ {sum(s) / len(s):.1f}" if s else ""

        lb.description = "\n".join(
            f"{medals[i] if i < 3 else f'`{i + 1}.`'} <@{uid}>\n"
            f"`{_bar(b['n'], top, 12)}` **{b['n']}** handled{stars(b)}"
            for i, (uid, b) in enumerate(ranked)
        )
    else:
        lb.description = "*No claimed tickets closed yet — hit 🙋 Claim on tickets to get on the board.*"
    _brand(lb, interaction.guild)
    await _send_card(interaction, [summary, lb])


@bot.tree.command(
    name="close", description="Close the ticket you're in (staff or ticket owner)"
)
@app_commands.describe(reason="Optional reason — shown in the transcript and the customer's DM")
async def close_ticket_cmd(
    interaction: discord.Interaction, reason: str | None = None
) -> None:
    store = guild_store(DATA, interaction.guild_id)
    order = _find_order_by_channel(store, interaction.channel_id)
    if order is None:
        await interaction.response.send_message(
            "This isn't a ticket channel.", ephemeral=True
        )
        return
    if not (is_leadership(interaction.user) or order.get("user_id") == interaction.user.id):
        await interaction.response.send_message(
            "Only staff or the ticket owner can close this.", ephemeral=True
        )
        return
    if order.get("status") == "closed":
        await interaction.response.send_message(
            "This ticket is already closing.", ephemeral=True
        )
        return
    order["status"] = "closed"  # claim it so a second /close can't re-fire
    save_data(DATA)
    await interaction.response.send_message(
        "Closing — transcript saved, channel will delete shortly. 🔒", ephemeral=True
    )
    await _close_ticket(interaction, store, order, reason)


# export everything (incl. _helpers) to the modules that build on this one
__all__ = [n for n in list(globals()) if not n.startswith("__")]
