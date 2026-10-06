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
            # Проверим, что пул реально работает
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

    # Создаём таблицы
    async with _pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                money BIGINT NOT NULL DEFAULT 0,
                tree_level INT NOT NULL DEFAULT 0
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                user_id BIGINT NOT NULL,
                item_type VARCHAR(32) NOT NULL,
                item_name VARCHAR(64) NOT NULL,
                quantity INT NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, item_type, item_name)
            )
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
            "SELECT money, tree_level FROM users WHERE user_id = $1", user_id
        )
        if row is None:
            await conn.execute(
                "INSERT INTO users (user_id) VALUES ($1) ON CONFLICT DO NOTHING",
                user_id,
            )
            return {"money": 0, "tree_level": 0}
        return {"money": row["money"], "tree_level": row["tree_level"]}


async def update_user(user_id: int, money: int = None, tree_level: int = None):
    _ensure_pool()
    await get_user(user_id)  # убедимся что пользователь существует
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

async def drop_chest_reward(user_id: int, count: int) -> list:
    """Возвращает список наград с учётом удачи от уровня дерева."""
    _ensure_pool()

    user = await get_user(user_id)
    tree_level = user["tree_level"]

    # Бонус удачи: +2% к редким за каждый уровень дерева (макс. ×5)
    luck_bonus = min(1 + tree_level * 0.02, 5.0)

    # Базовые веса: money, Common, Mega, Ultra, Super, Fast, Fast x2, Fast x4
    base_weights = [40, 30, 15, 8, 2, 3, 1.5, 0.5]

    # Редкие получают буст удачи, обычные — ослабляются
    weights = [
        base_weights[0],                # 💰 деньги — без изменений
        base_weights[1] / luck_bonus,   # 🎁 Common — реже
        base_weights[2] * luck_bonus,   # 📦 Mega
        base_weights[3] * luck_bonus,   # 💎 Ultra
        base_weights[4] * luck_bonus,   # ⚡ Super
        base_weights[5] * luck_bonus,   # 🌱 Fast
        base_weights[6] * luck_bonus,   # 🌿 Fast x2
        base_weights[7] * luck_bonus,   # 🍀 Fast x4
    ]

    rewards = []
    for _ in range(count):
        drop = random.choices(
            ["money", "Common", "Mega", "Ultra", "Super",
             "Fast", "Fast x2", "Fast x4"],
            weights=weights,
            k=1,
        )[0]

        if drop == "money":
            amt = random.randint(10, 100)
            await update_user(user_id, money=amt)
            rewards.append({"kind": "money", "amount": amt})

        elif drop in ("Common", "Mega", "Ultra", "Super"):
            await add_item(user_id, "chest", drop, 1)
            rewards.append({"kind": "chest", "name": drop, "amount": 1})

        else:  # Fast, Fast x2, Fast x4
            await add_item(user_id, "fertilizer", drop, 1)
            rewards.append({"kind": "fertilizer", "name": drop, "amount": 1})

    return rewards
