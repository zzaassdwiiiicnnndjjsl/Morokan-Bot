import os
import discord
from discord import app_commands
from discord.ext import commands

from config import TOKEN, ADMIN_ROLE_ID, CHEST_TYPES, FERTILIZER_TYPES
from storage import (
    init_db, close_db,
    get_user, update_user,
    get_inventory, add_item, remove_item,
    drop_chest_reward,
)

# ============================================================
# НАСТРОЙКИ БОТА
# ============================================================

intents = discord.Intents.default()
# message_content НЕ включаем — для слеш-команд он не нужен,
# и без него бот не будет требовать Privileged Intent в портале.

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


# ============================================================
# ПРОВЕРКА ПРАВ
# ============================================================

def has_admin_role(interaction: discord.Interaction) -> bool:
    """Проверяет, есть ли у пользователя админ-роль."""
    if not interaction.guild:
        return False
    member = interaction.guild.get_member(interaction.user.id)
    if not member:
        return False
    return any(role.id == ADMIN_ROLE_ID for role in member.roles)


def debug_roles(interaction: discord.Interaction):
    """Выводит в логи информацию о ролях пользователя — для отладки."""
    member = interaction.guild.get_member(interaction.user.id) if interaction.guild else None
    print("=" * 60)
    print(f"🔍 Команда вызвана: {interaction.command.name if interaction.command else '?'}")
    print(f"🔍 guild:  {interaction.guild}")
    print(f"🔍 user:   {interaction.user} (id={interaction.user.id})")
    print(f"🔍 member: {member}")
    if member:
        roles = [(r.name, r.id) for r in member.roles]
        print(f"🔍 roles:  {roles}")
        print(f"🔍 ищу:    ADMIN_ROLE_ID={ADMIN_ROLE_ID}")
        print(f"🔍 есть:   {ADMIN_ROLE_ID in [r.id for r in member.roles]}")
    print(f"🔍 итог has_admin_role: {has_admin_role(interaction)}")
    print("=" * 60)


async def deny(interaction: discord.Interaction):
    """Отправляет отказ в правах."""
    if interaction.response.is_done():
        await interaction.followup.send("❌ У вас нет прав для этой команды!", ephemeral=True)
    else:
        await interaction.response.send_message("❌ У вас нет прав для этой команды!", ephemeral=True)


# ============================================================
# АДМИН-КОМАНДЫ
# ============================================================

@tree.command(name="money-drop", description="Выдать деньги пользователю")
@app_commands.describe(amount="Сколько монет выдать", member="Кому (по умолчанию — вы)")
async def money_drop(interaction: discord.Interaction, amount: int, member: discord.Member = None):
    debug_roles(interaction)
    if not has_admin_role(interaction):
        return await deny(interaction)

    target = member or interaction.user
    await update_user(target.id, money=amount)
    await interaction.response.send_message(
        f"✅ {target.mention} получил **{amount}** монет!"
    )


@tree.command(name="chest-drop", description="Выдать сундук")
@app_commands.describe(count="Количество", chest_type="Тип сундука", member="Кому")
@app_commands.choices(chest_type=[
    app_commands.Choice(name="Mega", value="Mega"),
    app_commands.Choice(name="Ultra", value="Ultra"),
    app_commands.Choice(name="Super", value="Super"),
])
async def chest_drop(
    interaction: discord.Interaction,
    count: int,
    chest_type: app_commands.Choice[str],
    member: discord.Member = None,
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
    interaction: discord.Interaction,
    count: int,
    chest_type: app_commands.Choice[str],
    member: discord.Member,
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
    interaction: discord.Interaction,
    count: int,
    chest_type: app_commands.Choice[str],
    member: discord.Member,
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
async def tree_grow_up(
    interaction: discord.Interaction,
    amount: int,
    member: discord.Member = None,
):
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

@tree.command(name="chests", description="Показать ваши сундуки и удобрения")
async def chests(interaction: discord.Interaction):
    inv = await get_inventory(interaction.user.id)

    chest_list = [i for i in inv if i["type"] == "chest"]
    fert_list = [i for i in inv if i["type"] == "fertilizer"]

    embed = discord.Embed(title="📦 Ваш инвентарь", color=discord.Color.blue())

    embed.add_field(
        name="Сундуки",
        value="\n".join(f"**{c['name']}**: {c['quantity']}" for c in chest_list) or "Пусто",
        inline=False,
    )
    embed.add_field(
        name="Удобрения",
        value="\n".join(f"**{f['name']}**: {f['quantity']}" for f in fert_list) or "Пусто",
        inline=False,
    )

    await interaction.response.send_message(embed=embed, ephemeral=True)


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
    print(f"✅ Бот {bot.user} запущен и готов к работе!")
    print(f"📋 Зарегистрировано команд: {len(tree.get_commands())}")


@bot.event
async def on_disconnect():
    await close_db()


# ============================================================
# ЗАПУСК С ДИАГНОСТИКОЙ
# ============================================================

if __name__ == "__main__":
    raw = os.environ.get("DISCORD_TOKEN", "")
    print(f"🔍 ENV     : длина={len(raw)}, начало={raw[:8]!r}, конец={raw[-4:]!r}")
    if TOKEN:
        print(f"🔍 CONFIG  : длина={len(TOKEN)}, начало={TOKEN[:8]!r}, конец={TOKEN[-4:]!r}")
        print(f"🔍 MATCH   : {'✅ да' if TOKEN == raw else '❌ НЕТ (config != env)'}")
    else:
        print("🔍 CONFIG  : None")

    if not TOKEN:
        raise SystemExit("❌ DISCORD_TOKEN пустой — проверь переменные окружения в Railway!")
    if len(TOKEN) < 50:
        raise SystemExit(f"❌ DISCORD_TOKEN слишком короткий ({len(TOKEN)} символов) — это не токен бота!")

    bot.run(TOKEN)
