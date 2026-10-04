import sqlite3
import random
from config import CHEST_TYPES, FERTILIZER_TYPES
from config import DB_NAME

def init_db():
    """Инициализация базы данных"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Таблица пользователей
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            money INTEGER DEFAULT 0,
            tree_level INTEGER DEFAULT 0
        )
    """)
    
    # Таблица инвентаря
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inventory (
            user_id INTEGER,
            item_type TEXT,
            item_name TEXT,
            quantity INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, item_type, item_name)
        )
    """)
    
    conn.commit()
    conn.close()

def get_user(user_id):
    """Получить данные пользователя"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("SELECT money, tree_level FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    
    if result is None:
        cursor.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()
        result = (0, 0)
    
    conn.close()
    return {"money": result[0], "tree_level": result[1]}

def update_user(user_id, money=None, tree_level=None):
    """Обновить данные пользователя"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    if money is not None:
        cursor.execute("UPDATE users SET money = money + ? WHERE user_id = ?", (money, user_id))
    if tree_level is not None:
        cursor.execute("UPDATE users SET tree_level = tree_level + ? WHERE user_id = ?", (tree_level, user_id))
    
    conn.commit()
    conn.close()

def get_inventory(user_id):
    """Получить инвентарь пользователя"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT item_type, item_name, quantity 
        FROM inventory 
        WHERE user_id = ? AND quantity > 0
    """, (user_id,))
    
    items = cursor.fetchall()
    conn.close()
    
    return [
        {"type": item[0], "name": item[1], "quantity": item[2]}
        for item in items
    ]

def add_item(user_id, item_type, item_name, quantity):
    """Добавить предмет в инвентарь"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO inventory (user_id, item_type, item_name, quantity)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, item_type, item_name) 
        DO UPDATE SET quantity = quantity + ?
    """, (user_id, item_type, item_name, quantity, quantity))
    
    conn.commit()
    conn.close()

def remove_item(user_id, item_type, item_name, quantity):
    """Удалить предмет из инвентаря"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE inventory 
        SET quantity = quantity - ?
        WHERE user_id = ? AND item_type = ? AND item_name = ? AND quantity >= ?
    """, (quantity, user_id, item_type, item_name, quantity))
    
    success = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return success

def drop_chest_reward(user_id, count):
    """Выдать награду из сундука"""
    rewards = []
    
    for _ in range(count):
        reward_type = random.choice(["money", "chest", "fertilizer"])
        
        if reward_type == "money":
            amount = random.randint(10, 100)
            update_user(user_id, money=amount)
            rewards.append(f"💰 {amount} монет")
        
        elif reward_type == "chest":
            chest_type = random.choice(CHEST_TYPES)
            add_item(user_id, "chest", chest_type, 1)
            rewards.append(f"📦 Сундук {chest_type}")
        
        elif reward_type == "fertilizer":
            fert_type = random.choice(FERTILIZER_TYPES)
            add_item(user_id, "fertilizer", fert_type, 1)
            rewards.append(f"🌱 Удобрение {fert_type}")
    
    return rewards
