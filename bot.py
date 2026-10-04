import os
import discord
from discord import app_commands
from discord.ext import commands

from config import TOKEN, ADMIN_ROLE_ID, CHEST_TYPES, FERTILIZER_TYPES
from storage import (
    init_db, close_db,
    get_user, update_user,
    get_inventory, add_item, remove_item, get_item_quantity,
    drop_chest_reward,
)

# ============================================================
# НАСТРОЙКИ
# ============================================================

intents = discord.Intents.default()
intents.members = True          # для get_member()
intents.message_content = True  # чтобы не было warning

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


# ============================================================
# ПРОВЕРКА ПРАВ
# ============================================================

def has_admin_role(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        return False
    member = interaction.guild.get_member(interaction.user.id)
    if not member:
        return False
    return any(role.id == ADMIN_ROLE_ID for role in member.roles)


def debug_roles(interaction: discord.Interaction):
    member = interaction.guild.get_member(interaction.user.id) if interaction.guild else None
    print("=" * 60)
    print(f"🔍 guild:  {interaction.guild}")
    print(f"🔍 user:   {interaction.user} (id={interaction.user.id})")
    print(f"🔍 member: {member}")
    if member:
        print(f"🔍 roles:  {[(r.name, r.id) for r in member.roles]}")
        print(f"🔍 ищу:    {ADMIN_ROLE_ID}")
        print(f"🔍 есть:   {ADMIN_ROLE_ID in [r.id for r in member.roles]}")
    print(f"🔍 итог:   {has_admin_role(interaction)}")
    print("=" * 60)


async def deny(interaction: discord.Interaction):
    if interaction.response.is_done():
        await interaction.followup.send("❌ У вас нет прав для этой команды!")
    else:
        await interaction.response.send_message("❌ У вас нет прав для этой команды!")


# ============================================================
# ПОДМЕНЮ: Открыть 1 / Открыть все
# ============================================================

class OpenChoiceView(discord.ui.View):
    """Подменю: открыть 1 сундук или все сундуки этого типа."""

    def __init__(self, user_id: int, chest_type: str, parent_view: "ChestView"):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.chest_type = chest_type
        self.parent_view = parent_view

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Это меню не для вас!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Открыть 1", emoji="1️⃣", style=discord.ButtonStyle.green)
    async def open_one(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.parent_view._open(interaction, self.chest_type, count=1)

    @discord.ui.button(label="Открыть все", emoji="🎁", style=discord.ButtonStyle.danger)
    async def open_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        qty = await get_item_quantity(self.user_id, "chest", self.chest_type)
        if qty <= 0:
            await interaction.response.send_message(
                f"❌ У вас нет сундуков типа **{self.chest_type}**!",
                ephemeral=True,
            )
            return
        await self.parent_view._open(interaction, self.chest_type, count=qty)

    @discord.ui.button(label="Назад", emoji="◀️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = await build_chests_embed(self.user_id, interaction.user)
        await interaction.response.edit_message(embed=embed, view=self.parent_view)


# ============================================================
# ГЛАВНОЕ МЕНЮ СУНДУКОВ
# ============================================================

class ChestView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=180)
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ Это меню не для вас! Откройте своё через `/chests`.",
                ephemeral=True,
            )
            return False
        return True

    async def _show_choice(self, interaction: discord.Interaction, chest_type: str):
        """Показывает подменю выбора для конкретного типа сундука."""
        qty = await get_item_quantity(self.user_id, "chest", chest_type)
        if qty <= 0:
            await interaction.response.send_message(
                f"❌ У вас нет сундуков типа **{chest_type}**!",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"🎁 Открыть {chest_type}",
            description=f"У вас **{qty}** шт. Что будем делать?",
            color=discord.Color.gold(),
        )
        sub_view = OpenChoiceView(self.user_id, chest_type, parent_view=self)
        await interaction.response.edit_message(embed=embed, view=sub_view)

    @discord.ui.button(label="Обычный", emoji="🎁", style=discord.ButtonStyle.secondary)
    async def open_common(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_choice(interaction, "Common")

    @discord.ui.button(label="Mega", emoji="📦", style=discord.ButtonStyle.blurple)
    async def open_mega(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_choice(interaction, "Mega")

    @discord.ui.button(label="Ultra", emoji="💎", style=discord.ButtonStyle.blurple)
    async def open_ultra(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_choice(interaction, "Ultra")

    @discord.ui.button(label="Super", emoji="⚡", style=discord.ButtonStyle.blurple)
    async def open_super(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_choice(interaction, "Super")

    async def _open(self, interaction: discord.Interaction, chest_type: str, count: int):
        """Открывает count сундуков указанного типа."""
        ok = await remove_item(self.user_id, "chest", chest_type, count)
        if not ok:
            await interaction.response.send_message(
                f"❌ Не удалось списать **{count}x {chest_type}** — возможно, их уже нет.",
                ephemeral=True,
            )
            return

        rewards = await drop_chest_reward(self.user_id, count)

        grouped: dict[str, int] = {}
        for r in rewards:
            grouped[r] = grouped.get(r, 0) + 1

        reward_text = "\n".join(
            f"{r} × {c}" if c > 1 else r
            for r, c in grouped.items()
        )

        title = f"🎉 Открыто: **{count}x {chest_type}**"

        reward_embed = discord.Embed(
            title=title,
            description=f"**Выпало:**\n{reward_text}",
            color=discord.Color.gold(),
        )
        reward_embed.set_footer(text=f"Игрок: {interaction.user.display_name}")

        # Возвращаемся в главное меню сундуков
        new_embed = await build_chests_embed(self.user_id, interaction.user)
        await interaction.response.edit_message(embed=new_embed, view=ChestView(self.user_id))

        # Награды — публично
        await interaction.followup.send(embed=reward_embed)


async def build_chests_embed(user_id: int, user: discord.User = None) -> discord.Embed:
    inv = await get_inventory(user_id)
    chest_map = {c["name"]: c["quantity"] for c in inv if c["type"] == "chest"}

    common = chest_map.get("Common", 0)
    mega = chest_map.get("Mega", 0)
    ultra = chest_map.get("Ultra", 0)
    super_ = chest_map.get("Super", 0)
    total = common + mega + ultra + super_

    embed = discord.Embed(
        title="📦 Ваши сундуки",
        description=(
            f"🎁 Обычный: **{common}**\n"
            f"📦 Mega: **{mega}**\n"
            f"💎 Ultra: **{ultra}**\n"
            f"⚡ Super: **{super_}**\n"
            f"\n**Всего: {total}**"
        ),
        color=discord.Color.gold(),
    )
    if user:
        embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
    embed.set_footer(text="Нажмите на тип сундука, чтобы открыть")
    return embed


# ============================================================
# МЕНЮ ДЕРЕВА
# ============================================================

class TreeView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=180)
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Это меню не для вас!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Fast", emoji="🌱", style=discord.ButtonStyle.green)
    async def use_fast(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._use(interaction, "Fast", 1)

    @discord.ui.button(label="Fast x2", emoji="🌿", style=discord.ButtonStyle.green)
    async def use_fast2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._use(interaction, "Fast x2", 2)

    @discord.ui.button(label="Fast x4", emoji="🍀", style=discord.ButtonStyle.green)
    async def use_fast4(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._use(interaction, "Fast x4", 4)

    async def _use(self, interaction: discord.Interaction, fert_type: str, grow: int):
        qty = await get_item_quantity(self.user_id, "fertilizer", fert_type)
        if qty <= 0:
            await interaction.response.send_message(
                f"❌ У вас нет удобрения **{fert_type}**!",
                ephemeral=True,
            )
            return

        ok = await remove_item(self.user_id, "fertilizer", fert_type, 1)
        if not ok:
            await interaction.response.send_message("❌ Не удалось использовать удобрение.", ephemeral=True)
            return

        await update_user(self.user_id, tree_level=grow)

        new_embed = await build_tree_embed(self.user_id, interaction.user)

        reward_embed = discord.Embed(
            title=f"🌱 Использовано: {fert_type}",
            description=f"Дерево выросло на **+{grow}** уровень!",
            color=discord.Color.green(),
        )
        reward_embed.set_footer(text=f"Игрок: {interaction.user.display_name}")

        await interaction.response.edit_message(embed=new_embed, view=self)
        await interaction.followup.send(embed=reward_embed)


async def build_tree_embed(user_id: int, user: discord.User = None) -> discord.Embed:
    data = await get_user(user_id)
    inv = await get_inventory(user_id)
    fert_map = {f["name"]: f["quantity"] for f in inv if f["type"] == "fertilizer"}

    fert_text = "\n".join(
        f"🌱 {name}: **{qty}**" for name, qty in fert_map.items()
    ) or "Пусто"

    embed = discord.Embed(title="🌳 Ваше дерево", color=discord.Color.green())
    embed.add_field(name="Уровень дерева", value=f"**{data['tree_level']}**", inline=True)
    embed.add_field(name="💰 Баланс", value=f"**{data['money']}** монет", inline=True)
    embed.add_field(name="🌱 Удобрения", value=fert_text, inline=False)
    if user:
        embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
    embed.set_footer(text="Нажмите кнопку, чтобы использовать удобрение")
    return embed


# ============================================================
# АДМИН-КОМАНДЫ
# ============================================================

@tree.command(name="money-drop", description="Выдать деньги пользователю")
@app_commands.describe(amount="Сколько монет", member="Кому (по умолчанию — вы)")
async def money_drop(interaction: discord.Interaction, amount: int, member: discord.Member = None):
    debug_roles(interaction)
    if not has_admin_role(interaction):
        return await deny(interaction)

    target = member or interaction.user
    await update_user(target.id, money=amount)
    await interaction.response.send_message(f"✅ {target.mention} получил **{amount}** монет!")


@tree.command(name="chest-drop", description="Выдать сундук")
@app_commands.describe(count="Количество", chest_type="Тип сундука", member="Кому")
@app_commands.choices(chest_type=[
    app_commands.Choice(name="Обычный (Common)", value="Common"),
    app_commands.Choice(name="Mega", value="Mega"),
    app_commands.Choice(name="Ultra", value="Ultra"),
    app_commands.Choice(name="Super", value="Super"),
])
async def chest_drop(
    interaction: discord.Interaction, count: int,
    chest_type: app_commands.Choice[str], member: discord.Member = None,
):
    debug_roles(interaction)
    if not has_admin_role(interaction):
        return await deny(interaction)

    target = member or interaction.user
    await add_item(target.id, "chest", chest_type.value, count)
    await interaction.response.send_message(
        f"✅ {target.mention} получил **{count}x {chest_type.value}** сундук(ов)!"
    )


@tree.command(name="give-chest", description="Выдать сундуки конкретному пользователю")
@app_commands.describe(count="Количество", chest_type="Тип сундука", member="Кому")
@app_commands.choices(chest_type=[
    app_commands.Choice(name="Обычный (Common)", value="Common"),
    app_commands.Choice(name="Mega", value="Mega"),
    app_commands.Choice(name="Ultra", value="Ultra"),
    app_commands.Choice(name="Super", value="Super"),
])
async def give_chest(
    interaction: discord.Interaction, count: int,
    chest_type: app_commands.Choice[str], member: discord.Member,
):
    debug_roles(interaction)
    if not has_admin_role(interaction):
        return await deny(interaction)

    await add_item(member.id, "chest", chest_type.value, count)
    await interaction.response.send_message(
        f"✅ {member.mention} получил **{count}x {chest_type.value}** сундук(ов)!"
    )


@tree.command(name="remove-chest", description="Удалить сундуки у пользователя")
@app_commands.describe(count="Количество", chest_type="Тип сундука", member="Кому")
@app_commands.choices(chest_type=[
    app_commands.Choice(name="Обычный (Common)", value="Common"),
    app_commands.Choice(name="Mega", value="Mega"),
    app_commands.Choice(name="Ultra", value="Ultra"),
    app_commands.Choice(name="Super", value="Super"),
])
async def remove_chest(
    interaction: discord.Interaction, count: int,
    chest_type: app_commands.Choice[str], member: discord.Member,
):
    debug_roles(interaction)
    if not has_admin_role(interaction):
        return await deny(interaction)

    ok = await remove_item(member.id, "chest", chest_type.value, count)
    if ok:
        await interaction.response.send_message(
            f"✅ У {member.mention} удалено **{count}x {chest_type.value}**!"
        )
    else:
        await interaction.response.send_message(
            f"❌ У {member.mention} недостаточно **{chest_type.value}** сундуков!"
        )


@tree.command(name="tree-grow-up", description="Увеличить уровень дерева")
@app_commands.describe(amount="Насколько увеличить", member="Кому (по умолчанию — вы)")
async def tree_grow_up(interaction: discord.Interaction, amount: int, member: discord.Member = None):
    debug_roles(interaction)
    if not has_admin_role(interaction):
        return await deny(interaction)

    target = member or interaction.user
    await update_user(target.id, tree_level=amount)
    await interaction.response.send_message(
        f"✅ Дерево {target.mention} выросло на **{amount}** уровней!"
    )


# ============================================================
# КОМАНДЫ УЧАСТНИКОВ
# ============================================================

@tree.command(name="chests", description="Показать и открыть ваши сундуки")
async def chests(interaction: discord.Interaction):
    embed = await build_chests_embed(interaction.user.id, interaction.user)
    view = ChestView(interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view)


@tree.command(name="balance", description="Показать ваш баланс монет")
@app_commands.describe(member="Чей баланс показать (по умолчанию — ваш)")
async def balance(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    data = await get_user(target.id)

    embed = discord.Embed(
        title="💰 Баланс",
        color=discord.Color.gold(),
    )
    embed.set_author(name=target.display_name, icon_url=target.display_avatar.url)
    embed.add_field(name="Монеты", value=f"**{data['money']}** 💰", inline=False)
    await interaction.response.send_message(embed=embed)


@tree.command(name="tree", description="Показать ваше дерево и использовать удобрения")
async def tree_info(interaction: discord.Interaction):
    embed = await build_tree_embed(interaction.user.id, interaction.user)
    view = TreeView(interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view)


@tree.command(name="inventory", description="Показать весь инвентарь")
async def inventory(interaction: discord.Interaction):
    user_id = interaction.user.id
    data = await get_user(user_id)
    inv = await get_inventory(user_id)

    chest_map = {c["name"]: c["quantity"] for c in inv if c["type"] == "chest"}
    fert_map = {f["name"]: f["quantity"] for f in inv if f["type"] == "fertilizer"}

    embed = discord.Embed(title="🎒 Инвентарь", color=discord.Color.purple())
    embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)

    embed.add_field(
        name="📊 Общее",
        value=f"💰 Монеты: **{data['money']}**\n🌳 Уровень дерева: **{data['tree_level']}**",
        inline=False,
    )

    chest_text = "\n".join(f"📦 {name}: **{qty}**" for name, qty in chest_map.items()) or "Пусто"
    embed.add_field(name="🎁 Сундуки", value=chest_text, inline=False)

    fert_text = "\n".join(f"🌱 {name}: **{qty}**" for name, qty in fert_map.items()) or "Пусто"
    embed.add_field(name="🌱 Удобрения", value=fert_text, inline=False)

    await interaction.response.send_message(embed=embed)


# ============================================================
# СОБЫТИЯ
# ============================================================

@bot.event
async def on_ready():
    await init_db()
    await tree.sync()
    print(f"✅ Бот {bot.user} запущен!")
    print(f"📋 Команд зарегистрировано: {len(tree.get_commands())}")


@bot.event
async def on_disconnect():
    await close_db()


# ============================================================
# ЗАПУСК
# ============================================================

if __name__ == "__main__":
    raw = os.environ.get("DISCORD_TOKEN", "")
    print(f"🔍 ENV    : длина={len(raw)}, начало={raw[:8]!r}, конец={raw[-4:]!r}")
    if TOKEN:
        print(f"🔍 CONFIG : длина={len(TOKEN)}, начало={TOKEN[:8]!r}")
        print(f"🔍 MATCH  : {'✅ да' if TOKEN == raw else '❌ НЕТ'}")
    if not TOKEN:
        raise SystemExit("❌ DISCORD_TOKEN пустой!")
    if len(TOKEN) < 50:
        raise SystemExit(f"❌ DISCORD_TOKEN короткий: {len(TOKEN)} символов")

    bot.run(TOKEN)
