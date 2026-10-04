import asyncpg
import random
from config import CHEST_TYPES, FERTILIZER_TYPES

_pool: asyncpg.Pool | None = None

async def init_db():
    """Подключение к PostgreSQL и создание таблиц"""
    global _pool
    # Railway сам подставит URL подключения в переменную DATABASE_URL
    import os
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("❌ ОШИБКА: Не задана переменная DATABASE_URL")
        return

    _pool = await asyncpg.create_pool(db_url, min_size=2, max_size=10)

    async with _pool.acquire() as conn:
        # Создаем таблицу пользователей
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                money BIGINT NOT NULL DEFAULT 0,
                tree_level INT NOT NULL DEFAULT 0
            )
        """)
        # Создаем таблицу инвентаря
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                user_id BIGINT NOT NULL,
                item_type VARCHAR(32) NOT NULL,
                item_name VARCHAR(64) NOT NULL,
                quantity INT NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, item_type, item_name)
            )
        """)
    print("✅ PostgreSQL подключен и таблицы готовы")

async def close_db():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None

# ============ USERS ============

async def get_user(user_id: int) -> dict:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT money, tree_level FROM users WHERE user_id = $1", user_id
        )
        if row is None:
            await conn.execute(
                "INSERT INTO users (user_id) VALUES ($1) ON CONFLICT DO NOTHING", user_id
            )
            return {"money": 0, "tree_level": 0}
        return {"money": row["money"], "tree_level": row["tree_level"]}

async def update_user(user_id: int, money: int = None, tree_level: int = None):
    await get_user(user_id) # Убедимся, что пользователь существует
    async with _pool.acquire() as conn:
        if money is not None:
            await conn.execute(
                "UPDATE users SET money = money + $1 WHERE user_id = $2",
                money, user_id
            )
        if tree_level is not None:
            await conn.execute(
                "UPDATE users SET tree_level = tree_level + $1 WHERE user_id = $2",
                tree_level, user_id
            )

# ============ INVENTORY ============

async def get_inventory(user_id: int) -> list:
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
    if quantity <= 0: return
    await get_user(user_id)
    async with _pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO inventory (user_id, item_type, item_name, quantity)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id, item_type, item_name)
            DO UPDATE SET quantity = inventory.quantity + EXCLUDED.quantity
        """, user_id, item_type, item_name, quantity)

async def remove_item(user_id: int, item_type: str, item_name: str, quantity: int) -> bool:
    if quantity <= 0: return False
    async with _pool.acquire() as conn:
        result = await conn.execute("""
            UPDATE inventory
            SET quantity = quantity - $1
            WHERE user_id = $2 AND item_type = $3 AND item_name = $4
            AND quantity >= $1
        """, quantity, user_id, item_type, item_name)
        return result.endswith("1")

async def get_item_quantity(user_id: int, item_type: str, item_name: str) -> int:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT quantity FROM inventory
            WHERE user_id = $1 AND item_type = $2 AND item_name = $3
        """, user_id, item_type, item_name)
        return row["quantity"] if row else 0

# ============ ИГРОВАЯ ЛОГИКА ============

async def drop_chest_reward(user_id: int, count: int) -> list:
    rewards = []
    for _ in range(count):
        r = random.choices(["money", "chest", "fertilizer"], weights=[60, 25, 15], k=1)[0]
        if r == "money":
            amt = random.randint(10, 100)
            await update_user(user_id, money=amt)
            rewards.append(f"💰 {amt} монет")
        elif r == "chest":
            ct = random.choice(CHEST_TYPES)
            await add_item(user_id, "chest", ct, 1)
            rewards.append(f"📦 Сундук {ct}")
        else:
            ft = random.choice(FERTILIZER_TYPES)
            await add_item(user_id, "fertilizer", ft, 1)
            rewards.append(f"🌱 Удобрение {ft}")
    return rewards
