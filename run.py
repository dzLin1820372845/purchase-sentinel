"""采购舆情检测系统 - 应用启动入口"""
import asyncio
import logging
import sys

import uvicorn
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def _make_selector_loop():
    import selectors

    return asyncio.SelectorEventLoop(selectors.SelectSelector())


if __name__ == "__main__":
    config = uvicorn.Config("app.main:app", host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)

    if sys.platform == "win32":
        # Windows 默认 ProactorEventLoop 不兼容 psycopg/psycopg_pool
        # Python >=3.12: 用 loop_factory 强制 SelectorEventLoop
        # Python <3.12: 用 set_event_loop_policy
        if sys.version_info >= (3, 12):
            asyncio.run(server.serve(), loop_factory=_make_selector_loop)
        else:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
            asyncio.run(server.serve())
    else:
        asyncio.run(server.serve())
