"""
Settings — THE ONE PLACE you change to swap data feeds.

PROVIDER options:
  "mock"     -> synthetic data, works anywhere (default for first run/testing)
  "yfinance" -> free/delayed real data (phase 1, quick but unofficial/flaky)
  "alpaca"   -> real US-equities data, free IEX feed (phase 1 recommended)
  "mt5"      -> your XM feed via MetaTrader 5 on Windows (phase 2)

Override with the env var DASHBOARD_PROVIDER, e.g.:
  DASHBOARD_PROVIDER=alpaca uvicorn app.main:app
"""
import os
from pathlib import Path


def _load_dotenv():
    """Load KEY=VALUE lines from a .env at the project root, if present.
    Does NOT override variables already set in the real environment, so an
    explicit $env: value still wins. No external dependency."""
    for cand in (Path(__file__).resolve().parent.parent / ".env", Path.cwd() / ".env"):
        if cand.exists():
            # utf-8-sig strips a Notepad-added BOM; also defensively strip it per-key
            for line in cand.read_text(encoding="utf-8-sig").splitlines():
                line = line.lstrip("\ufeff").strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip().lstrip("\ufeff")
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
            break


_load_dotenv()

PROVIDER = os.getenv("DASHBOARD_PROVIDER", "mock")

# Seconds a fetched snapshot is cached before refetching (matches 10s UI refresh).
CACHE_TTL = int(os.getenv("DASHBOARD_CACHE_TTL", "10"))
# News is heavier and changes slower — cache longer.
NEWS_TTL = int(os.getenv("DASHBOARD_NEWS_TTL", "120"))

# Alpaca (phase 1). Free keys at alpaca.markets (paper keys work for data).
ALPACA_KEY_ID = os.getenv("ALPACA_KEY_ID")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

# MT5 credentials (phase 2). Leave blank if your terminal is already logged in.
MT5_LOGIN = os.getenv("MT5_LOGIN")
MT5_PASSWORD = os.getenv("MT5_PASSWORD")
MT5_SERVER = os.getenv("MT5_SERVER")
MT5_PATH = os.getenv("MT5_PATH")


def make_provider():
    if PROVIDER == "yfinance":
        from .providers.yfinance_provider import YFinanceProvider
        return YFinanceProvider()
    if PROVIDER == "alpaca":
        from .providers.alpaca_provider import AlpacaProvider
        return AlpacaProvider(key_id=ALPACA_KEY_ID, secret=ALPACA_SECRET_KEY)
    if PROVIDER == "mt5":
        from .providers.mt5_provider import MT5Provider
        login = int(MT5_LOGIN) if MT5_LOGIN else None
        return MT5Provider(login=login, password=MT5_PASSWORD,
                           server=MT5_SERVER, path=MT5_PATH)
    from .providers.mock_provider import MockProvider
    return MockProvider()
