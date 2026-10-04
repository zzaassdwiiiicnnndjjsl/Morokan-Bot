import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_ROLE_ID = int(os.getenv("ADMIN_ROLE_ID", "0"))

CHEST_TYPES = ["Mega", "Ultra", "Super"]
FERTILIZER_TYPES = ["Fast", "Fast x2", "Fast x4"]
