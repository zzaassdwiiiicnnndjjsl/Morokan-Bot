import random
from config import CHEST_TYPES, FERTILIZER_TYPES

_users: dict[int, dict] = {}
_inventory: dict[int, dict[tuple[str, str], int]] = {}


async def init_db():
    print("✅ Хранилище в памяти готово")


async def close_db():
    pass


async def get_user(user_id: int) -> dict:
    if user_id not in _users:
        _users[user_id] = {"money": 0, "tree_level": 0}
    return _users[user_id].copy()


async def update_user(user_id: int, money: int = None, tree_level: int = None):
    if user_id not in _users:
        _users[user_id] = {"money": 0, "tree_level": 0}
    if money is not None:
        _users[user_id]["money"] += money
    if tree_level is not None:
        _users[user_id]["tree_level"] += tree_level


async def get_inventory(user_id: int) -> list:
    items = _inventory.get(user_id, {})
    return [
        {"type": t, "name": n, "quantity": q}
        for (t, n), q in items.items()
        if q > 0
    ]


async def add_item(user_id: int, item_type: str, item_name: str, quantity: int):
    if quantity <= 0:
        return
    if user_id not in _inventory:
        _inventory[user_id] = {}
    key = (item_type, item_name)
    _inventory[user_id][key] = _inventory[user_id].get(key, 0) + quantity


async def remove_item(user_id: int, item_type: str, item_name: str, quantity: int) -> bool:
    if quantity <= 0:
        return False
    key = (item_type, item_name)
    current = _inventory.get(user_id, {}).get(key, 0)
    if current < quantity:
        return False
    _inventory[user_id][key] = current - quantity
    return True


async def get_item_quantity(user_id: int, item_type: str, item_name: str) -> int:
    return _inventory.get(user_id, {}).get((item_type, item_name), 0)


async def drop_chest_reward(user_id: int, count: int) -> list:
    rewards = []
    for _ in range(count):
        r = random.choices(
            ["money", "chest", "fertilizer"],
            weights=[60, 25, 15],
            k=1,
        )[0]
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
