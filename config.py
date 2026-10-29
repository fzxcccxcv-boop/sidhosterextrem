import os

# ==========================================
# TELEGRAM BOT & ADMIN CONFIGURATION
# ==========================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8874296819:AAH249JwMBWsIMiIFnNP7XcclbcGLmJpD60")
OWNER_ID = int(os.getenv("OWNER_ID", "8684812026"))  # Replace with your Telegram User ID
ADMIN_IDS = [OWNER_ID]

# ==========================================
# PATHS & WORKSPACE SETTINGS
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_DIR = os.path.join(BASE_DIR, "workspaces")
DB_FILE_PATH = os.path.join(BASE_DIR, "database.json")

os.makedirs(WORKSPACE_DIR, exist_ok=True)

# ==========================================
# HOSTING, UPLOAD & ZIP ARCHIVE QUOTAS
# ==========================================
MAX_FILES_PER_USER = 10
MAX_CONCURRENT_PROCESSES_PER_USER = 3
MAX_FILE_SIZE_MB = 25
MAX_ZIP_UNCOMPRESSED_MB = 100   # Protects against Zip Bomb attacks
MAX_ZIP_FILES_COUNT = 250       # Maximum number of files inside a .zip project
LOG_TAIL_LINES = 35
PIP_TIMEOUT_SECONDS = 120

# Ordered priority list for automatic entry-point detection in .zip and GitHub projects
ENTRY_POINT_CANDIDATES = [
    "main.py", "bot.py", "app.py", "run.py", "start.py", "index.py", "server.py"
]

# ==========================================
# SECURITY SCANNER SIGNATURES
# ==========================================
RESTRICTED_IMPORTS = {
    "pty", "fcntl", "resource", "grp", "pwd", "spwd", "crypt", "mmap", "ctypes"
}

RESTRICTED_CALLS = {
    "os.system", "os.popen", "os.removedirs", "os.rmdir", "os.unlink",
    "shutil.rmtree", "subprocess.call", "subprocess.Popen", "subprocess.run",
    "subprocess.check_output", "eval", "exec", "compile"
}

MALICIOUS_PATTERNS = [
    r"rm\s+-rf",
    r"mkfs\.",
    r":\(\)\{\s*:\|:&\s*\};:",  # Fork bomb
    r"/dev/tcp/",
    r"/etc/passwd",
    r"/etc/shadow",
    r"BOT_TOKEN",               # Attempting to read the host bot's token
    r"database\.json",          # Attempting to access host DB
    r"xmrig|minerd|stratum\+tcp",
    r"nc\s+-e\s+/bin/(ba)?sh",
    r"chmod\s+777\s+/"
]

STDLIB_MODULES = {
    "abc", "argparse", "asyncio", "base64", "collections", "concurrent", "configparser",
    "contextlib", "copy", "csv", "ctypes", "datetime", "decimal", "difflib", "email",
    "enum", "errno", "functools", "gc", "getpass", "glob", "gzip", "hashlib", "heapq",
    "hmac", "html", "http", "importlib", "inspect", "io", "itertools", "json", "logging",
    "lzma", "math", "mimetypes", "multiprocessing", "numbers", "operator", "os", "pathlib",
    "pickle", "platform", "pprint", "queue", "random", "re", "secrets", "select", "shlex",
    "shutil", "signal", "socket", "sqlite3", "ssl", "stat", "statistics", "string",
    "struct", "subprocess", "sys", "tarfile", "tempfile", "textwrap", "threading", "time",
    "timeit", "tkinter", "traceback", "turtle", "types", "typing", "unicodedata",
    "unittest", "urllib", "uuid", "venv", "warnings", "wave", "weakref", "webbrowser",
    "xml", "zipfile", "zlib", "__future__"
}

IMPORT_TO_PYPI_MAP = {
    "telebot": "pyTelegramBotAPI",
    "telegram": "python-telegram-bot",
    "cv2": "opencv-python-headless",
    "PIL": "Pillow",
    "bs4": "beautifulsoup4",
    "sklearn": "scikit-learn",
    "yaml": "PyYAML",
    "dotenv": "python-dotenv",
    "jwt": "PyJWT",
    "fitz": "PyMuPDF"
}
