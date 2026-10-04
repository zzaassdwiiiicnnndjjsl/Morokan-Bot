import discord
from discord import app_commands
from discord.ext import commands
from config import TOKEN, ADMIN_ROLE_ID, CHEST_TYPES, FERTILIZER_TYPES
from storage import (
    init_db, close_db, get_user, update_user,
    get_inventory, add_item, remove_item, drop_chest_reward,
)

# Инициализация бота
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

def has_admin_role(interaction: discord.Interaction) -> bool:
    """Проверка наличия админской роли"""
    if not interaction.guild:
        return False
    
    member = interaction.guild.get_member(interaction.user.id)
    if not member:
        return False
    
    return any(role.id == ADMIN_ROLE_ID for role in member.roles)

# ==================== АДМИНСКИЕ КОМАНДЫ ====================

@tree.command(name="money-drop", description="Выдать деньги пользователю")
@app_commands.describe(
    amount="Количество денег",
    member="Пользователь (по умолчанию - вы)"
)
async def money_drop(
    interaction: discord.Interaction, 
    amount: int,
    member: discord.Member = None
):
    """Админская команда: выдать деньги"""
    
    # Проверка роли
    if not has_admin_role(interaction):
        await interaction.response.send_message(
            "❌ У вас нет прав для использования этой команды!", 
            ephemeral=True
        )
        return
    
    target = member or interaction.user
    update_user(target.id, money=amount)
    
    await interaction.response.send_message(
        f"✅ {target.mention} получил **{amount}** монет!",
        ephemeral=False
    )

@tree.command(name="chest-drop", description="Выдать сундук пользователю")
@app_commands.describe(
    count="Количество сундуков",
    chest_type="Тип сундука",
    member="Пользователь (по умолчанию - вы)"
)
@app_commands.choices(chest_type=[
    app_commands.Choice(name="Mega", value="Mega"),
    app_commands.Choice(name="Ultra", value="Ultra"),
    app_commands.Choice(name="Super", value="Super"),
])
async def chest_drop(
    interaction: discord.Interaction,
    count: int,
    chest_type: app_commands.Choice[str],
    member: discord.Member = None
):
    """Админская команда: выдать сундук"""
    
    if not has_admin_role(interaction):
        await interaction.response.send_message(
            "❌ У вас нет прав для использования этой команды!", 
            ephemeral=True
        )
        return
    
    target = member or interaction.user
    add_item(target.id, "chest", chest_type.value, count)
    
    await interaction.response.send_message(
        f"✅ {target.mention} получил **{count}x {chest_type.value}** сундук(ов)!",
        ephemeral=False
    )

@tree.command(name="give-chest", description="Выдать сундуки пользователю")
@app_commands.describe(
    count="Количество сундуков",
    chest_type="Тип сундука",
    member="Пользователь"
)
@app_commands.choices(chest_type=[
    app_commands.Choice(name="Mega", value="Mega"),
    app_commands.Choice(name="Ultra", value="Ultra"),
    app_commands.Choice(name="Super", value="Super"),
])
async def give_chest(
    interaction: discord.Interaction,
    count: int,
    chest_type: app_commands.Choice[str],
    member: discord.Member
):
    """Админская команда: выдать сундуки конкретному пользователю"""
    
    if not has_admin_role(interaction):
        await interaction.response.send_message(
            "❌ У вас нет прав для использования этой команды!", 
            ephemeral=True
        )
        return
    
    add_item(member.id, "chest", chest_type.value, count)
    
    await interaction.response.send_message(
        f"✅ {member.mention} получил **{count}x {chest_type.value}** сундук(ов)!",
        ephemeral=False
    )

@tree.command(name="remove-chest", description="Удалить сундуки у пользователя")
@app_commands.describe(
    count="Количество сундуков для удаления",
    chest_type="Тип сундука",
    member="Пользователь"
)
@app_commands.choices(chest_type=[
    app_commands.Choice(name="Mega", value="Mega"),
    app_commands.Choice(name="Ultra", value="Ultra"),
    app_commands.Choice(name="Super", value="Super"),
])
async def remove_chest(
    interaction: discord.Interaction,
    count: int,
    chest_type: app_commands.Choice[str],
    member: discord.Member
):
    """Админская команда: удалить сундуки"""
    
    if not has_admin_role(interaction):
        await interaction.response.send_message(
            "❌ У вас нет прав для использования этой команды!", 
            ephemeral=True
        )
        return
    
    success = remove_item(member.id, "chest", chest_type.value, count)
    
    if success:
        await interaction.response.send_message(
            f"✅ У {member.mention} удалено **{count}x {chest_type.value}** сундук(ов)!",
            ephemeral=False
        )
    else:
        await interaction.response.send_message(
            f"❌ У {member.mention} недостаточно **{chest_type.value}** сундуков!",
            ephemeral=True
        )

@tree.command(name="tree-grow-up", description="Увеличить уровень дерева")
@app_commands.describe(
    amount="Насколько увеличить уровень дерева",
    member="Пользователь (по умолчанию - вы)"
)
async def tree_grow_up(
    interaction: discord.Interaction,
    amount: int,
    member: discord.Member = None
):
    """Админская команда: увеличить уровень дерева"""
    
    if not has_admin_role(interaction):
        await interaction.response.send_message(
            "❌ У вас нет прав для использования этой команды!", 
            ephemeral=True
        )
        return
    
    target = member or interaction.user
    update_user(target.id, tree_level=amount)
    
    await interaction.response.send_message(
        f"✅ Дерево {target.mention} выросло на **{amount}** уровней!",
        ephemeral=False
    )

# ==================== КОМАНДЫ ДЛЯ УЧАСТНИКОВ ====================

@tree.command(name="chests", description="Показать ваши сундуки")
async def chests(interaction: discord.Interaction):
    """Команда: показать инвентарь сундуков"""
    inventory = get_inventory(interaction.user.id)
    
    chests = [item for item in inventory if item["type"] == "chest"]
    fertilizers = [item for item in inventory if item["type"] == "fertilizer"]
    
    embed = discord.Embed(
        title="📦 Ваш инвентарь",
        color=discord.Color.blue()
    )
    
    if chests:
        chest_text = "\n".join(
            f"**{c['name']}**: {c['quantity']} шт." for c in chests
        )
        embed.add_field(name="Сундуки", value=chest_text, inline=False)
    else:
        embed.add_field(name="Сундуки", value="Нет сундуков", inline=False)
    
    if fertilizers:
        fert_text = "\n".join(
            f"**{f['name']}**: {f['quantity']} шт." for f in fertilizers
        )
        embed.add_field(name="Удобрения", value=fert_text, inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="tree", description="Показать ваше дерево")
async def tree_info(interaction: discord.Interaction):
    """Команда: показать уровень дерева и деньги"""
    user_data = get_user(interaction.user.id)
    
    embed = discord.Embed(
        title="🌳 Ваше дерево",
        color=discord.Color.green()
    )
    embed.add_field(name="Уровень дерева", value=f"**{user_data['tree_level']}**", inline=True)
    embed.add_field(name="💰 Баланс", value=f"**{user_data['money']}** монет", inline=True)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ==================== ЗАПУСК БОТА ====================

@bot.event
async def on_ready():
    """Событие: бот готов"""
    init_db()  # Инициализация БД
    await tree.sync()  # Синхронизация слеш-команд
    print(f"✅ Бот {bot.user} запущен!")

if __name__ == "__main__":
    if not TOKEN:
        print("❌ Ошибка: DISCORD_TOKEN не установлен в .env")
    else:
        bot.run(TOKEN)
