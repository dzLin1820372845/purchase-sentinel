"""测试共享 fixtures

注意: Windows + Python 3.14 + psycopg v3 需要 SelectorEventLoop。
"""
import asyncio
import os
import sys

# 在 Windows 上，psycopg v3 异步不兼容 ProactorEventLoop。
# 必须在任何 async 代码执行前设置事件循环策略。
if sys.platform == "win32":
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# 确保测试使用本地 Docker PostgreSQL
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://sentiment:sentiment_dev@localhost:5432/sentiment",
)

import app.database as db_module  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402


@pytest_asyncio.fixture(scope="session")
async def db_pool():
    """Session-scoped 数据库连接池 — 需要数据库的测试显式使用。"""
    await db_module.init_pool()
    assert db_module.pool is not None
    yield db_module.pool
    await db_module.close_pool()


@pytest_asyncio.fixture
async def clean_articles(db_pool):
    """每个测试前后清理 articles 表和 processing_failures 表。"""
    # Setup: 测试前清理
    async with db_module.pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM processing_failures")
            await cur.execute("DELETE FROM articles")
        await conn.commit()
    yield
    # Teardown: 测试后清理
    async with db_module.pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM processing_failures")
            await cur.execute("DELETE FROM articles")
        await conn.commit()
