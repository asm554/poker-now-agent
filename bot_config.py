"""Non-secret defaults. Environment variables override values in .env."""
import os
from pathlib import Path


def load_env(path=None):
    path = Path(path) if path else Path(__file__).with_name('.env')
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key, value = key.strip(), value.strip()
        if value[:1] in ('"', "'") and value[-1:] == value[:1]:
            value = value[1:-1]
        os.environ.setdefault(key, value)


load_env()
CDP_ENDPOINT = os.getenv('CDP_ENDPOINT', 'http://127.0.0.1:18800')
PLAYER_NAME = os.getenv('POKER_PLAYER_NAME', 'André')
MODEL = os.getenv('ANTHROPIC_MODEL', '')
API_KEY = os.getenv('ANTHROPIC_API_KEY', '')
