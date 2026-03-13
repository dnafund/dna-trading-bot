"""Backward-compat stub -- canonical location: src.trading.bot"""
import asyncio

from src.trading.bot import *  # noqa: F401,F403
from src.trading.bot import (  # noqa: F401  explicit re-exports
    FuturesTradingBot,
    main,
)

if __name__ == "__main__":
    asyncio.run(main())
