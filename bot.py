import os
import random
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from config import TOKEN, ADMIN_ROLE_ID, CHEST_TYPES, FERTILIZER_TYPES
from storage import (
    init_db, close_db,
    get_user, update_user,
    get_inventory, add_item, remove_item, get_item_quantity,
    drop_chest_reward, get_top_players,
    set_user_luck, set_user_money, set_user_tree,
)

# ============================================================
# НАСТРОЙКИ
# ============================================================

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


# ============================================================
# КОНФИГ МАГАЗИНА
# ============================================================

SHOP_LUCK = {
    "small":  {"name": "🍀 Удача +0.5", "price": 500,  "value": 0.5},
    "medium": {"name": "🍀 Удача +2.0", "price": 2000, "value": 2.0},
    "big":    {"name": "🍀 Удача +5.0", "price": 5000, "value": 5.0},
}

SHOP_CHESTS = {
    "Common": {"name": "🎁 Обычный сундук", "price": 100,  "qty": 1},
    "Mega":   {"name": "📦 Mega-сундук",    "price": 500,  "qty": 1},
    "Ultra":  {"name": "💎 Ultra-сундук",   "price": 1500, "qty": 1},
    "Super":  {"name": "⚡ Super-сундук",   "price": 5000, "qty": 1},
}

SHOP_FERT = {
    "Fast":    {"name": "🌱 Fast",    "price": 200,  "qty": 1},
    "Fast x2": {"name": "🌿 Fast x2", "price": 500,  "qty": 1},
    "Fast x4": {"name": "🍀 Fast x4", "price": 1200, "qty": 1},
}

# Крафт: 3 предмета → 1 получше
CRAFT_RECIPES = {
    "Common":  {"need": 3, "give": "Mega",  "name": "🎁×3 → 📦 Mega"},
    "Mega":    {"need": 3, "give": "Ultra", "name": "📦×3 → 💎 Ultra"},
    "Ultra":   {"need": 3, "give": "Super", "name": "💎×3 → ⚡ Super"},
    "Fast":    {"need": 3, "give": "Fast x2", "name": "🌱×3 → 🌿 Fast x2"},
    "Fast x2": {"need": 3, "give": "Fast x4", "name": "🌿×3 → 🍀 Fast x4"},
}

DAILY_REWARD = 500
DAILY_COOLDOWN_HOURS = 24


# ============================================================
# ХЕЛПЕРЫ
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


def _chest_emoji(name: str) -> str:
    return {"Common": "🎁", "Mega": "📦", "Ultra": "💎", "Super": "⚡"}.get(name, "📦")


def _fert_emoji(name: str) -> str:
    return {"Fast": "🌱", "Fast x2": "🌿", "Fast x4": "🍀"}.get(name, "🌱")


def calc_luck(tree_level: int, shop_luck: float = 0.0) -> float:
    """Итоговая удача: от дерева + купленная. Максимум ×10."""
    tree_luck = min(1 + tree_level * 0.02, 5.0)
    return min(tree_luck + shop_luck, 10.0)


def _chest_order(name: str) -> int:
    return {"Common": 0, "Mega": 1, "Ultra": 2, "Super": 3}.get(name, 99)


def _fert_order(name: str) -> int:
    return {"Fast": 0, "Fast x2": 1, "Fast x4": 2}.get(name, 99)


# ============================================================
# ПОДМЕНЮ: Открыть 1 / Открыть все
# ============================================================

class OpenChoiceView(discord.ui.View):
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
        qty = await get_item_quantity(self.user_id, "chest", chest_type)
        if qty <= 0:
            await interaction.response.send_message(
                f"❌ У вас нет сундуков типа **{chest_type}**!",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"{_chest_emoji(chest_type)} Открыть {chest_type}",
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
        await interaction.response.defer()

        ok = await remove_item(self.user_id, "chest", chest_type, count)
        if not ok:
            await interaction.followup.send(
                f"❌ Не удалось списать **{count}x {chest_type}** — возможно, их уже нет.",
                ephemeral=True,
            )
            return

        rewards = await drop_chest_reward(self.user_id, chest_type, count)

        money_total = 0
        chest_rewards: dict[str, int] = {}
        fert_rewards: dict[str, int] = {}

        for r in rewards:
            if r["kind"] == "money":
                money_total += r["amount"]
            elif r["kind"] == "chest":
                chest_rewards[r["name"]] = chest_rewards.get(r["name"], 0) + r["amount"]
            elif r["kind"] == "fertilizer":
                fert_rewards[r["name"]] = fert_rewards.get(r["name"], 0) + r["amount"]

        user_data = await get_user(self.user_id)
        luck = calc_luck(user_data["tree_level"], user_data.get("luck_bonus", 0.0))

        emoji = _chest_emoji(chest_type)

        reward_embed = discord.Embed(
            title=f"{emoji} Открыто: {count}x {chest_type}",
            color=discord.Color.gold(),
        )
        reward_embed.set_author(
            name=interaction.user.display_name,
            icon_url=interaction.user.display_avatar.url,
        )

        if money_total > 0:
            reward_embed.add_field(
                name="💰 Деньги",
                value=f"💰 Монеты × **{money_total}**",
                inline=False,
            )

        if chest_rewards:
            sorted_chests = sorted(chest_rewards.items(), key=lambda x: _chest_order(x[0]))
            chest_text = "\n".join(
                f"{_chest_emoji(name)} {name} × **{qty}**"
                for name, qty in sorted_chests
            )
            reward_embed.add_field(name="📦 Сундуки", value=chest_text, inline=False)

        if fert_rewards:
            sorted_fert = sorted(fert_rewards.items(), key=lambda x: _fert_order(x[0]))
            fert_text = "\n".join(
                f"{_fert_emoji(name)} {name} × **{qty}**"
                for name, qty in sorted_fert
            )
            reward_embed.add_field(name="🌱 Удобрения", value=fert_text, inline=False)

        if not money_total and not chest_rewards and not fert_rewards:
            reward_embed.description = "😔 Ничего не выпало..."

        reward_embed.set_footer(
            text=f"Всего наград: {len(rewards)} • Удача: ×{luck:.2f}"
        )

        new_embed = await build_chests_embed(self.user_id, interaction.user)
        await interaction.edit_original_response(
            embed=new_embed, view=ChestView(self.user_id)
        )
        await interaction.followup.send(embed=reward_embed)


async def build_chests_embed(user_id: int, user: discord.User = None) -> discord.Embed:
    inv = await get_inventory(user_id)
    chest_map = {c["name"]: c["quantity"] for c in inv if c["type"] == "chest"}

    common = chest_map.get("Common", 0)
    mega = chest_map.get("Mega", 0)
    ultra = chest_map.get("Ultra", 0)
    super_ = chest_map.get("Super", 0)
    total = common + mega + ultra + super_

    data = await get_user(user_id)
    luck = calc_luck(data["tree_level"], data.get("luck_bonus", 0.0))

    embed = discord.Embed(
        title="📦 Ваши сундуки",
        description=(
            f"🎁 Обычный: **{common}**\n"
            f"📦 Mega: **{mega}**\n"
            f"💎 Ultra: **{ultra}**\n"
            f"⚡ Super: **{super_}**\n"
            f"\n**Всего: {total}**\n"
            f"🍀 Удача: **×{luck:.2f}**"
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
                f"❌ У вас нет удобрения **{fert_type}**!", ephemeral=True,
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
        f"{_fert_emoji(name)} {name}: **{qty}**" for name, qty in fert_map.items()
    ) or "Пусто"

    luck = calc_luck(data["tree_level"], data.get("luck_bonus", 0.0))

    embed = discord.Embed(title="🌳 Ваше дерево", color=discord.Color.green())
    embed.add_field(name="Уровень дерева", value=f"**{data['tree_level']}**", inline=True)
    embed.add_field(name="💰 Баланс", value=f"**{data['money']}** монет", inline=True)
    embed.add_field(name="🍀 Удача", value=f"**×{luck:.2f}**", inline=True)
    embed.add_field(name="🌱 Удобрения", value=fert_text, inline=False)
    if user:
        embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
    embed.set_footer(text="Нажмите кнопку, чтобы использовать удобрение")
    return embed


# ============================================================
# МАГАЗИН
# ============================================================

def build_store_main_embed() -> discord.Embed:
    return discord.Embed(
        title="🛒 Магазин",
        description=(
            "Выберите категорию:\n\n"
            "🍀 **Удача** — постоянный бонус к шансам редких дропов\n"
            "📦 **Сундуки** — покупайте сундуки за монеты\n"
            "🌱 **Удобрения** — быстрый рост дерева"
        ),
        color=discord.Color.gold(),
    )


def build_store_luck_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🍀 Магазин удачи",
        description="Каждая покупка **постоянно** увеличивает удачу.\nСуммируется с бонусом от дерева.",
        color=discord.Color.green(),
    )
    for item in SHOP_LUCK.values():
        embed.add_field(name=item["name"], value=f"💰 **{item['price']}** монет", inline=False)
    return embed


def build_store_chests_embed() -> discord.Embed:
    embed = discord.Embed(title="📦 Магазин сундуков", color=discord.Color.blurple())
    for item in SHOP_CHESTS.values():
        embed.add_field(name=item["name"], value=f"💰 **{item['price']}** монет", inline=False)
    return embed


def build_store_fert_embed() -> discord.Embed:
    embed = discord.Embed(title="🌱 Магазин удобрений", color=discord.Color.green())
    for item in SHOP_FERT.values():
        embed.add_field(name=item["name"], value=f"💰 **{item['price']}** монет", inline=False)
    return embed


class StoreView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=180)
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Это меню не для вас!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Удача", emoji="🍀", style=discord.ButtonStyle.green)
    async def shop_luck(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=build_store_luck_embed(), view=LuckShopView(self.user_id))

    @discord.ui.button(label="Сундуки", emoji="📦", style=discord.ButtonStyle.blurple)
    async def shop_chests(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=build_store_chests_embed(), view=ChestShopView(self.user_id))

    @discord.ui.button(label="Удобрения", emoji="🌱", style=discord.ButtonStyle.green)
    async def shop_fert(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=build_store_fert_embed(), view=FertShopView(self.user_id))


class LuckShopView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=180)
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Это меню не для вас!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="+0.5 удачи", emoji="🍀", style=discord.ButtonStyle.green)
    async def buy_small(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._buy(interaction, "small")

    @discord.ui.button(label="+2.0 удачи", emoji="🍀", style=discord.ButtonStyle.green)
    async def buy_medium(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._buy(interaction, "medium")

    @discord.ui.button(label="+5.0 удачи", emoji="🍀", style=discord.ButtonStyle.green)
    async def buy_big(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._buy(interaction, "big")

    @discord.ui.button(label="Назад", emoji="◀️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=build_store_main_embed(), view=StoreView(self.user_id))

    async def _buy(self, interaction: discord.Interaction, key: str):
        item = SHOP_LUCK[key]
        user = await get_user(self.user_id)

        if user["money"] < item["price"]:
            await interaction.response.send_message(
                f"❌ Недостаточно монет! Нужно **{item['price']}**, у вас **{user['money']}**.",
                ephemeral=True,
            )
            return

        await update_user(self.user_id, money=-item["price"], luck_bonus=item["value"])
        new_user = await get_user(self.user_id)

        embed = discord.Embed(
            title="✅ Покупка успешна!",
            description=(
                f"Куплено: **{item['name']}**\n"
                f"Баланс: 💰 **{new_user['money']}**\n"
                f"Бонус удачи: 🍀 **+{new_user['luck_bonus']:.2f}**"
            ),
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class ChestShopView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=180)
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Это меню не для вас!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Обычный", emoji="🎁", style=discord.ButtonStyle.secondary)
    async def buy_common(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._buy(interaction, "Common")

    @discord.ui.button(label="Mega", emoji="📦", style=discord.ButtonStyle.blurple)
    async def buy_mega(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._buy(interaction, "Mega")

    @discord.ui.button(label="Ultra", emoji="💎", style=discord.ButtonStyle.blurple)
    async def buy_ultra(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._buy(interaction, "Ultra")

    @discord.ui.button(label="Super", emoji="⚡", style=discord.ButtonStyle.blurple)
    async def buy_super(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._buy(interaction, "Super")

    @discord.ui.button(label="Назад", emoji="◀️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=build_store_main_embed(), view=StoreView(self.user_id))

    async def _buy(self, interaction: discord.Interaction, key: str):
        item = SHOP_CHESTS[key]
        user = await get_user(self.user_id)

        if user["money"] < item["price"]:
            await interaction.response.send_message(
                f"❌ Недостаточно монет! Нужно **{item['price']}**, у вас **{user['money']}**.",
                ephemeral=True,
            )
            return

        await update_user(self.user_id, money=-item["price"])
        await add_item(self.user_id, "chest", key, item["qty"])
        new_user = await get_user(self.user_id)

        embed = discord.Embed(
            title="✅ Покупка успешна!",
            description=(
                f"Куплено: **{item['name']}** ×{item['qty']}\n"
                f"Баланс: 💰 **{new_user['money']}**"
            ),
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class FertShopView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=180)
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Это меню не для вас!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Fast", emoji="🌱", style=discord.ButtonStyle.green)
    async def buy_fast(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._buy(interaction, "Fast")

    @discord.ui.button(label="Fast x2", emoji="🌿", style=discord.ButtonStyle.green)
    async def buy_fast2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._buy(interaction, "Fast x2")

    @discord.ui.button(label="Fast x4", emoji="🍀", style=discord.ButtonStyle.green)
    async def buy_fast4(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._buy(interaction, "Fast x4")

    @discord.ui.button(label="Назад", emoji="◀️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=build_store_main_embed(), view=StoreView(self.user_id))

    async def _buy(self, interaction: discord.Interaction, key: str):
        item = SHOP_FERT[key]
        user = await get_user(self.user_id)

        if user["money"] < item["price"]:
            await interaction.response.send_message(
                f"❌ Недостаточно монет! Нужно **{item['price']}**, у вас **{user['money']}**.",
                ephemeral=True,
            )
            return

        await update_user(self.user_id, money=-item["price"])
        await add_item(self.user_id, "fertilizer", key, item["qty"])
        new_user = await get_user(self.user_id)

        embed = discord.Embed(
            title="✅ Покупка успешна!",
            description=(
                f"Куплено: **{item['name']}** ×{item['qty']}\n"
                f"Баланс: 💰 **{new_user['money']}**"
            ),
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ============================================================
# МЕНЮ КРАФТА
# ============================================================

class CraftView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=180)
        self.user_id = user_id
        self._add_buttons()

    def _add_buttons(self):
        for key, recipe in CRAFT_RECIPES.items():
            button = discord.ui.Button(
                label=recipe["name"],
                style=discord.ButtonStyle.green,
                custom_id=f"craft_{key}",
            )
            button.callback = self._make_callback(key)
            self.add_item(button)

    def _make_callback(self, key: str):
        async def callback(interaction: discord.Interaction):
            recipe = CRAFT_RECIPES[key]
            item_type = "chest" if key in ("Common", "Mega", "Ultra") else "fertilizer"

            have = await get_item_quantity(self.user_id, item_type, key)
            if have < recipe["need"]:
                await interaction.response.send_message(
                    f"❌ Нужно **{recipe['need']}x {key}**, у вас **{have}**.",
                    ephemeral=True,
                )
                return

            ok = await remove_item(self.user_id, item_type, key, recipe["need"])
            if not ok:
                await interaction.response.send_message("❌ Не удалось списать предметы.", ephemeral=True)
                return

            give_type = "chest" if recipe["give"] in ("Common", "Mega", "Ultra", "Super") else "fertilizer"
            await add_item(self.user_id, give_type, recipe["give"], 1)

            embed = discord.Embed(
                title="🔨 Крафт успешен!",
                description=f"{recipe['name']}\n\nПолучено: **{recipe['give']}** ×1",
                color=discord.Color.green(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Это меню не для вас!", ephemeral=True)
            return False
        return True


def build_craft_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🔨 Крафт",
        description="Обменяй 3 предмета на 1 более редкий.",
        color=discord.Color.orange(),
    )
    for recipe in CRAFT_RECIPES.values():
        embed.add_field(name=recipe["name"], value="\u200b", inline=False)
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
async def chest_drop(interaction: discord.Interaction, count: int,
                     chest_type: app_commands.Choice[str], member: discord.Member = None):
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
async def give_chest(interaction: discord.Interaction, count: int,
                     chest_type: app_commands.Choice[str], member: discord.Member):
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
async def remove_chest(interaction: discord.Interaction, count: int,
                       chest_type: app_commands.Choice[str], member: discord.Member):
    debug_roles(interaction)
    if not has_admin_role(interaction):
        return await deny(interaction)
    ok = await remove_item(member.id, "chest", chest_type.value, count)
    if ok:
        await interaction.response.send_message(f"✅ У {member.mention} удалено **{count}x {chest_type.value}**!")
    else:
        await interaction.response.send_message(f"❌ У {member.mention} недостаточно **{chest_type.value}** сундуков!")


@tree.command(name="tree-grow-up", description="Увеличить уровень дерева")
@app_commands.describe(amount="Насколько увеличить", member="Кому (по умолчанию — вы)")
async def tree_grow_up(interaction: discord.Interaction, amount: int, member: discord.Member = None):
    debug_roles(interaction)
    if not has_admin_role(interaction):
        return await deny(interaction)
    target = member or interaction.user
    await update_user(target.id, tree_level=amount)
    await interaction.response.send_message(f"✅ Дерево {target.mention} выросло на **{amount}** уровней!")


@tree.command(name="set-money", description="Установить баланс пользователя")
@app_commands.describe(amount="Новый баланс", member="Кому")
async def set_money(interaction: discord.Interaction, amount: int, member: discord.Member):
    if not has_admin_role(interaction):
        return await deny(interaction)
    await set_user_money(member.id, amount)
    await interaction.response.send_message(f"✅ Баланс {member.mention} = **{amount}** монет")


@tree.command(name="set-tree", description="Установить уровень дерева")
@app_commands.describe(level="Новый уровень", member="Кому")
async def set_tree(interaction: discord.Interaction, level: int, member: discord.Member):
    if not has_admin_role(interaction):
        return await deny(interaction)
    await set_user_tree(member.id, level)
    await interaction.response.send_message(f"✅ Дерево {member.mention} = **{level}**")


@tree.command(name="set-luck", description="Установить бонус удачи магазина")
@app_commands.describe(value="Новый бонус удачи (0-5)", member="Кому")
async def set_luck(interaction: discord.Interaction, value: float, member: discord.Member):
    if not has_admin_role(interaction):
        return await deny(interaction)
    await set_user_luck(member.id, value)
    await interaction.response.send_message(f"✅ Бонус удачи {member.mention} = **+{value:.2f}**")


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
    embed = discord.Embed(title="💰 Баланс", color=discord.Color.gold())
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

    luck = calc_luck(data["tree_level"], data.get("luck_bonus", 0.0))

    embed = discord.Embed(title="🎒 Инвентарь", color=discord.Color.purple())
    embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
    embed.add_field(
        name="📊 Общее",
        value=(
            f"💰 Монеты: **{data['money']}**\n"
            f"🌳 Уровень дерева: **{data['tree_level']}**\n"
            f"🍀 Удача: **×{luck:.2f}** (дерево + магазин)"
        ),
        inline=False,
    )
    chest_text = "\n".join(f"📦 {name}: **{qty}**" for name, qty in chest_map.items()) or "Пусто"
    embed.add_field(name="🎁 Сундуки", value=chest_text, inline=False)
    fert_text = "\n".join(f"{_fert_emoji(name)} {name}: **{qty}**" for name, qty in fert_map.items()) or "Пусто"
    embed.add_field(name="🌱 Удобрения", value=fert_text, inline=False)
    await interaction.response.send_message(embed=embed)


@tree.command(name="store", description="Открыть магазин")
async def store(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_store_main_embed(), view=StoreView(interaction.user.id))


@tree.command(name="craft", description="Крафт — обменять 3 предмета на 1 редкий")
async def craft(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_craft_embed(), view=CraftView(interaction.user.id))


@tree.command(name="daily", description="Получить ежедневную награду")
async def daily(interaction: discord.Interaction):
    user_id = interaction.user.id
    data = await get_user(user_id)

    last = data.get("last_daily")
    now = datetime.now(timezone.utc)

    if last is not None:
        # если last_daily приходит как строка — конвертируем
        if isinstance(last, str):
            last = datetime.fromisoformat(last)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)

        delta = now - last
        if delta < timedelta(hours=DAILY_COOLDOWN_HOURS):
            remaining = timedelta(hours=DAILY_COOLDOWN_HOURS) - delta
            hours = int(remaining.total_seconds() // 3600)
            minutes = int((remaining.total_seconds() % 3600) // 60)
            await interaction.response.send_message(
                f"⏳ Ежедневная награда уже получена! Следующая через **{hours}ч {minutes}м**.",
                ephemeral=True,
            )
            return

    await update_user(user_id, money=DAILY_REWARD)
    await set_last_daily(user_id, now)

    embed = discord.Embed(
        title="🎁 Ежедневная награда!",
        description=f"Вы получили **{DAILY_REWARD}** монет!\nВозвращайтесь через 24 часа.",
        color=discord.Color.green(),
    )
    await interaction.response.send_message(embed=embed)


@tree.command(name="top", description="Топ игроков по балансу")
async def top(interaction: discord.Interaction):
    players = await get_top_players(10)
    if not players:
        await interaction.response.send_message("Пока нет игроков с балансом.")
        return

    lines = []
    for i, p in enumerate(players, 1):
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"**{i}.**")
        lines.append(f"{medal} <@{p['user_id']}> — 💰 **{p['money']}** (🌳 {p['tree_level']})")

    embed = discord.Embed(
        title="🏆 Топ игроков",
        description="\n".join(lines),
        color=discord.Color.gold(),
    )
    await interaction.response.send_message(embed=embed)


# ============================================================
# СОБЫТИЯ
# ============================================================

@bot.event
async def on_ready():
    try:
        await init_db()
    except Exception as e:
        print(f"❌ КРИТИЧЕСКАЯ ОШИБКА init_db: {e}")
        await bot.close()
        return

    try:
        await tree.sync()
    except Exception as e:
        print(f"⚠️ Ошибка sync команд: {e}")

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
