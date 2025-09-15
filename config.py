# # MySQL database configuration
# MYSQL_HOST = 'localhost'
# MYSQL_USER = 'root'
# MYSQL_PASSWORD = ''
# MYSQL_NAME = 'airline'
# SECRET_KEY = 'your_secret_key_here'

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
INSTANCE_DIR.mkdir(exist_ok=True)

# Default to a bundled SQLite db for free hosting (Render)
DEFAULT_SQLITE_URL = f"sqlite:///{(INSTANCE_DIR / 'skyreserve.db').as_posix()}"

# Primary connection string (override in production)
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_SQLITE_URL)

# Flask secret key
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")

# Legacy MySQL vars (used only if DATABASE_URL is missing or not usable)
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_NAME = os.getenv("MYSQL_NAME", "airline")