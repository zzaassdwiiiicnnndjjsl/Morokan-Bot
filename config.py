import os
from dotenv import load_dotenv

load_dotenv()

# Токен бота
TOKEN = os.getenv("DISCORD_TOKEN")

# ID роли для админ-команд (ваш ID из запроса)
ADMIN_ROLE_ID = 1556354466586959988

# Типы сундуков
CHEST_TYPES = ["Mega", "Ultra", "Super"]

# Типы удобрений
FERTILIZER_TYPES = ["Fast", "Fast x2", "Fast x4"]
