import os
import asyncio
import random
import asyncpg
from config import CHEST_TYPES, FERTILIZER_TYPES

_pool: asyncpg.Pool | None = None


# ============================================================
# ИНИЦИАЛИЗАЦИЯ
# ============================================================

async def init_db():
    """Подключение к PostgreSQL с retry + создание таблиц"""
    global _pool
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("❌ ОШИБКА: Не задана переменная DATABASE_URL")
        raise RuntimeError("DATABASE_URL не задана")

    # Пробуем подключиться 5 раз с задержкой 3 секунды
    for attempt in range(1, 6):
        try:
            _pool = await asyncpg.create_pool(
                db_url,
                min_size=2,
                max_size=10,
                command_timeout=30,
            )
            async with _pool.acquire() as conn:
                await conn.execute("SELECT 1")
            print(f"✅ PostgreSQL подключен (попытка {attempt})")
            break
        except Exception as e:
            print(f"⚠️ Попытка {attempt} не удалась: {e}")
            if attempt < 5:
                await asyncio.sleep(3)
            else:
                print("❌ Не удалось подключиться к PostgreSQL")
                raise

    # Создаём таблицы + миграции
    async with _pool.acquire() as conn:
        # users
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                money BIGINT NOT NULL DEFAULT 0,
                tree_level INT NOT NULL DEFAULT 0,
                luck_bonus FLOAT NOT NULL DEFAULT 0
            )
        """)
        # inventory
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                user_id BIGINT NOT NULL,
                item_type VARCHAR(32) NOT NULL,
                item_name VARCHAR(64) NOT NULL,
                quantity INT NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, item_type, item_name)
            )
        """)
        # миграции для старых таблиц (если колонки нет — добавляем)
        await conn.execute("""
            ALTER TABLE users ADD COLUMN IF NOT EXISTS luck_bonus FLOAT NOT NULL DEFAULT 0
        """)
    print("✅ Таблицы готовы")


async def close_db():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def _ensure_pool():
    """Проверка, что пул жив — иначе понятная ошибка вместо AttributeError"""
    if _pool is None:
        raise RuntimeError("База данных не подключена (пул = None). Проверь init_db().")


# ============================================================
# USERS
# ============================================================

async def get_user(user_id: int) -> dict:
    _ensure_pool()
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT money, tree_level, luck_bonus FROM users WHERE user_id = $1",
            user_id,
        )
        if row is None:
            await conn.execute(
                "INSERT INTO users (user_id) VALUES ($1) ON CONFLICT DO NOTHING",
                user_id,
            )
            return {"money": 0, "tree_level": 0, "luck_bonus": 0.0}
        return {
            "money": row["money"],
            "tree_level": row["tree_level"],
            "luck_bonus": row["luck_bonus"],
        }


async def update_user(
    user_id: int,
    money: int = None,
    tree_level: int = None,
    luck_bonus: float = None,
):
    _ensure_pool()
    await get_user(user_id)
    async with _pool.acquire() as conn:
        if money is not None:
            await conn.execute(
                "UPDATE users SET money = money + $1 WHERE user_id = $2",
                money, user_id,
            )
        if tree_level is not None:
            await conn.execute(
                "UPDATE users SET tree_level = tree_level + $1 WHERE user_id = $2",
                tree_level, user_id,
            )
        if luck_bonus is not None:
            await conn.execute(
                "UPDATE users SET luck_bonus = luck_bonus + $1 WHERE user_id = $2",
                luck_bonus, user_id,
            )


# ============================================================
# INVENTORY
# ============================================================

async def get_inventory(user_id: int) -> list:
    _ensure_pool()
    async with _pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT item_type, item_name, quantity FROM inventory
            WHERE user_id = $1 AND quantity > 0
            ORDER BY item_type, item_name
        """, user_id)
        return [
            {"type": r["item_type"], "name": r["item_name"], "quantity": r["quantity"]}
            for r in rows
        ]


async def add_item(user_id: int, item_type: str, item_name: str, quantity: int):
    if quantity <= 0:
        return
    _ensure_pool()
    await get_user(user_id)
    async with _pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO inventory (user_id, item_type, item_name, quantity)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id, item_type, item_name)
            DO UPDATE SET quantity = inventory.quantity + EXCLUDED.quantity
        """, user_id, item_type, item_name, quantity)


async def remove_item(user_id: int, item_type: str, item_name: str, quantity: int) -> bool:
    if quantity <= 0:
        return False
    _ensure_pool()
    async with _pool.acquire() as conn:
        result = await conn.execute("""
            UPDATE inventory
            SET quantity = quantity - $1
            WHERE user_id = $2 AND item_type = $3 AND item_name = $4
              AND quantity >= $1
        """, quantity, user_id, item_type, item_name)
        return result.endswith("1")


async def get_item_quantity(user_id: int, item_type: str, item_name: str) -> int:
    _ensure_pool()
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT quantity FROM inventory
            WHERE user_id = $1 AND item_type = $2 AND item_name = $3
        """, user_id, item_type, item_name)
        return row["quantity"] if row else 0


# ============================================================
# ИГРОВАЯ ЛОГИКА
# ============================================================

async def drop_chest_reward(user_id: int, chest_type: str, count: int) -> list:
    """Возвращает награды в зависимости от типа открытого сундука и удачи."""
    _ensure_pool()

    user = await get_user(user_id)
    tree_level = user["tree_level"]
    shop_luck = user["luck_bonus"]

    # Удача: от дерева + купленная, максимум ×10
    tree_luck = min(1 + tree_level * 0.02, 5.0)
    luck_bonus = min(tree_luck + shop_luck, 10.0)

    # Разные лут-столы для разных сундуков
    # (money_range, [(drop, weight), ...])
    loot_tables = {
        "Common": {
            "money": (5, 25),
            "drops": [
                ("Common",  40),
                ("Mega",    25),
                ("Fast",    20),
                ("Ultra",   10),
                ("Fast x2", 5),
            ],
        },
        "Mega": {
            "money": (20, 80),
            "drops": [
                ("Mega",    35),
                ("Ultra",   30),
                ("Fast",    10),
                ("Fast x2", 15),
                ("Super",   8),
                ("Fast x4", 2),
            ],
        },
        "Ultra": {
            "money": (80, 250),
            "drops": [
                ("Ultra",   40),
                ("Super",   25),
                ("Fast x2", 15),
                ("Fast x4", 12),
                ("Mega",    8),
            ],
        },
        "Super": {
            "money": (300, 800),
            "drops": [
                ("Super",   45),
                ("Ultra",   25),
                ("Fast x4", 20),
                ("Fast x2", 8),
                ("Mega",    2),
            ],
        },
    }

    table = loot_tables.get(chest_type, loot_tables["Common"])
    drops = table["drops"]
    money_min, money_max = table["money"]

    # Применяем удачу: редкие дропы (кроме Common и Mega) получают буст
    rare_items = {"Ultra", "Super", "Fast x2", "Fast x4"}
    weights = []
    for name, w in drops:
        if name in rare_items:
            weights.append(w * luck_bonus)
        else:
            weights.append(w / max(luck_bonus ** 0.5, 1.0))  # Common/Mega чуть реже

    rewards = []
    for _ in range(count):
        # 25% шанс что вообще деньги, иначе — предмет
        if random.random() < 0.25:
            amt = random.randint(money_min, money_max)
            await update_user(user_id, money=amt)
            rewards.append({"kind": "money", "amount": amt})
            continue

        drop = random.choices(
            [d[0] for d in drops],
            weights=weights,
            k=1,
        )[0]

        if drop in ("Common", "Mega", "Ultra", "Super"):
            await add_item(user_id, "chest", drop, 1)
            rewards.append({"kind": "chest", "name": drop, "amount": 1})
        else:
            await add_item(user_id, "fertilizer", drop, 1)
            rewards.append({"kind": "fertilizer", "name": drop, "amount": 1})

    # Super-сундук: гарантированный бонусный редкий дроп сверху
    if chest_type == "Super" and count >= 1:
        bonus_drop = random.choices(
            ["Ultra", "Super", "Fast x4"],
            weights=[50, 30, 20],
            k=1,
        )[0]
        if bonus_drop in ("Ultra", "Super"):
            await add_item(user_id, "chest", bonus_drop, 1)
            rewards.append({"kind": "chest", "name": bonus_drop, "amount": 1, "bonus": True})
        else:
            await add_item(user_id, "fertilizer", bonus_drop, 1)
            rewards.append({"kind": "fertilizer", "name": bonus_drop, "amount": 1, "bonus": True})

    return rewards

# ============================================================
# ТОП ИГРОКОВ И АДМИН-ФУНКЦИИ
# ============================================================

async def get_top_players(limit: int = 10) -> list:
    """Топ игроков по балансу."""
    _ensure_pool()
    async with _pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT user_id, money, tree_level FROM users
            ORDER BY money DESC
            LIMIT $1
        """, limit)
        return [dict(r) for r in rows]


async def set_user_money(user_id: int, amount: int):
    """Установить баланс (абсолютное значение)."""
    _ensure_pool()
    await get_user(user_id)
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET money = $1 WHERE user_id = $2",
            amount, user_id,
        )


async def set_user_tree(user_id: int, level: int):
    """Установить уровень дерева (абсолютное значение)."""
    _ensure_pool()
    await get_user(user_id)
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET tree_level = $1 WHERE user_id = $2",
            level, user_id,
        )


async def set_user_luck(user_id: int, value: float):
    """Установить бонус удачи магазина (абсолютное значение)."""
    _ensure_pool()
    await get_user(user_id)
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET luck_bonus = $1 WHERE user_id = $2",
            value, user_id,
        )


async def set_last_daily(user_id: int, when):
    """Сохранить время последней ежедневной награды."""
    _ensure_pool()
    await get_user(user_id)
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET last_daily = $1 WHERE user_id = $2",
            when, user_id,
        )
