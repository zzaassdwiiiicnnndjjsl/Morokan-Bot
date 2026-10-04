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
        await interaction.followup.send("❌ У вас нет прав для этой команды!", ephemeral=True)
    else:
        await interaction.response.send_message("❌ У вас нет прав для этой команды!", ephemeral=True)


# ============================================================
# МЕНЮ ОТКРЫТИЯ СУНДУКОВ (КНОПКИ)
# ============================================================

class ChestView(discord.ui.View):
    """Кнопки для открытия сундуков."""

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

    async def _refresh_embed(self, interaction: discord.Interaction):
        """Перестраивает embed с актуальным инвентарём."""
        return await build_chests_embed(self.user_id)

    @discord.ui.button(label="Открыть Mega", emoji="📦", style=discord.ButtonStyle.blurple, custom_id="open_mega")
    async def open_mega(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._open(interaction, "Mega")

    @discord.ui.button(label="Открыть Ultra", emoji="💎", style=discord.ButtonStyle.blurple, custom_id="open_ultra")
    async def open_ultra(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._open(interaction, "Ultra")

    @discord.ui.button(label="Открыть Super", emoji="⚡", style=discord.ButtonStyle.blurple, custom_id="open_super")
    async def open_super(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._open(interaction, "Super")

    @discord.ui.button(label="Открыть все Mega", emoji="🎁", style=discord.ButtonStyle.green, custom_id="open_all_mega")
    async def open_all_mega(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._open(interaction, "Mega", open_all=True)

    @discord.ui.button(label="Открыть все Ultra", emoji="🎁", style=discord.ButtonStyle.green, custom_id="open_all_ultra")
    async def open_all_ultra(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._open(interaction, "Ultra", open_all=True)

    @discord.ui.button(label="Открыть все Super", emoji="🎁", style=discord.ButtonStyle.green, custom_id="open_all_super")
    async def open_all_super(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._open(interaction, "Super", open_all=True)

    async def _open(self, interaction: discord.Interaction, chest_type: str, open_all: bool = False):
        """Открывает один или все сундуки указанного типа."""
        # Сколько у пользователя сундуков этого типа?
        qty = await get_item_quantity(self.user_id, "chest", chest_type)

        if qty <= 0:
            await interaction.response.send_message(
                f"❌ У вас нет сундуков типа **{chest_type}**!",
                ephemeral=True,
            )
            return

        count = qty if open_all else 1

        # Списываем сундуки
        ok = await remove_item(self.user_id, "chest", chest_type, count)
        if not ok:
            await interaction.response.send_message(
                "❌ Не удалось списать сундук. Попробуйте ещё раз.",
                ephemeral=True,
            )
            return

        # Открываем и получаем награды
        rewards = await drop_chest_reward(self.user_id, count)

        # Собираем красивый ответ
        title = f"🎉 Открыто: **{count}x {chest_type}**" if open_all else f"🎉 Открыт **{chest_type}** сундук"

        # Группируем награды по типам для красоты
        grouped: dict[str, int] = {}
        for r in rewards:
            grouped[r] = grouped.get(r, 0) + 1

        reward_text = "\n".join(
            f"{r} × {c}" if c > 1 else r
            for r, c in grouped.items()
        )

        embed = discord.Embed(
            title=title,
            description=f"**Выпало:**\n{reward_text}",
            color=discord.Color.gold(),
        )

        # Обновляем основное сообщение с новым инвентарём
        new_embed = await build_chests_embed(self.user_id)

        await interaction.response.edit_message(embed=new_embed, view=self)

        # И отдельным эфемерным сообщением показываем награды
        await interaction.followup.send(embed=embed, ephemeral=True)


async def build_chests_embed(user_id: int) -> discord.Embed:
    """Строит embed с текущим инвентарём сундуков."""
    inv = await get_inventory(user_id)

    chest_map = {c["name"]: c["quantity"] for c in inv if c["type"] == "chest"}
    fert_map = {f["name"]: f["quantity"] for f in inv if f["type"] == "fertilizer"}

    mega = chest_map.get("Mega", 0)
    ultra = chest_map.get("Ultra", 0)
    super_ = chest_map.get("Super", 0)
    total = mega + ultra + super_

    embed = discord.Embed(
        title="🎁 Ваши сундуки",
        color=discord.Color.gold(),
    )

    embed.add_field(
        name="📦 Сундуки",
        value=(
            f"📦 Mega-сундук: **{mega}**\n"
            f"💎 Ultra-сундук: **{ultra}**\n"
            f"⚡ Super-сундук: **{super_}**\n"
            f"\n**Всего: {total} сундуков**"
        ),
        inline=False,
    )

    if fert_map:
        fert_text = "\n".join(f"🌱 {name}: **{qty}**" for name, qty in fert_map.items())
        embed.add_field(name="🌱 Удобрения", value=fert_text, inline=False)

    embed.set_footer(text="Нажмите кнопку ниже, чтобы открыть сундук")

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
            f"❌ У {member.mention} недостаточно **{chest_type.value}** сундуков!",
            ephemeral=True,
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
    embed = await build_chests_embed(interaction.user.id)
    view = ChestView(interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@tree.command(name="tree", description="Показать ваше дерево и баланс")
async def tree_info(interaction: discord.Interaction):
    data = await get_user(interaction.user.id)

    embed = discord.Embed(title="🌳 Ваше дерево", color=discord.Color.green())
    embed.add_field(name="Уровень дерева", value=f"**{data['tree_level']}**", inline=True)
    embed.add_field(name="Баланс", value=f"**{data['money']}** монет", inline=True)

    await interaction.response.send_message(embed=embed, ephemeral=True)


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
