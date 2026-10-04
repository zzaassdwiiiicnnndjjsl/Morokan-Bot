import os
from dotenv import load_dotenv

load_dotenv()

# Токен бота
TOKEN = os.getenv("DISCORD_TOKEN")

# ID роли для админ-команд
ADMIN_ROLE_ID = int(os.getenv("ADMIN_ROLE_ID", "0"))

# Имя файла базы данных
DB_NAME = os.getenv("DB_NAME", "bot_data.db")

# Типы сундуков
CHEST_TYPES = ["Mega", "Ultra", "Super"]

# Типы удобрений
FERTILIZER_TYPES = ["Fast", "Fast x2", "Fast x4"]
