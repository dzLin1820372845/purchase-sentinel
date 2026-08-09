"""PostgreSQL 异步数据库连接模块

使用 psycopg v3 + psycopg-pool AsyncConnectionPool。
所有数据库操作均为异步，不阻塞 FastAPI 事件循环。
"""
import logging
import os

import psycopg
from psycopg_pool import AsyncConnectionPool
from psycopg.types.json import Jsonb

from app.models import Article

logger = logging.getLogger(__name__)

# 从环境变量读取连接信息
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://sentiment:sentiment_dev@localhost:5432/sentiment",
)

# 全局连接池实例 — open=False 延迟到 init_pool() 中显式打开
pool: AsyncConnectionPool | None = None


async def init_pool() -> None:
    """初始化数据库连接池 — 在 FastAPI lifespan startup 中调用。

    Pool 参数说明（NAS 资源受限环境）：
    - min_size=2: 保持最少 2 个空闲连接，避免频繁创建
    - max_size=5: 最多 5 个并发连接（采集 + dashboard + webhook）
    - open=False: 延迟初始化模式，必须手动 await pool.open()
    """
    global pool
    pool = AsyncConnectionPool(
        conninfo=DATABASE_URL,
        min_size=2,
        max_size=5,
        open=False,
    )
    await pool.open()
    logger.info("数据库连接池已初始化 (min=2, max=5)")


async def close_pool() -> None:
    """关闭数据库连接池 — 在 FastAPI lifespan shutdown 中调用。"""
    global pool
    if pool:
        await pool.close()
        pool = None
        logger.info("数据库连接池已关闭")


async def get_connection():
    """获取数据库连接的异步上下文管理器。

    用法:
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT * FROM articles")
    """
    async with pool.connection() as conn:
        yield conn


async def insert_article(article: Article) -> int | None:
    """插入文章到数据库。如果 url_hash 冲突则跳过（ON CONFLICT DO NOTHING）。

    Returns:
        int: 插入成功，返回新文章的 id
        None: URL 已存在，跳过
    """
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO articles (
                    url_hash, title, url, source, source_category, source_type,
                    published_at, collected_at, content,
                    matched_keywords, ai_summary, ai_category, ai_score,
                    dingtalk_sent, error_msg
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, NOW(), %s,
                    %s, %s, %s, %s,
                    %s, %s
                )
                ON CONFLICT (url_hash) DO NOTHING
                RETURNING id
                """,
                (
                    article.url_hash,
                    article.title,
                    article.url,
                    article.source,
                    article.source_category,
                    article.source_type,
                    article.published_at,
                    article.content,
                    Jsonb(article.matched_keywords),
                    article.ai_summary,
                    article.ai_category,
                    article.ai_score,
                    article.dingtalk_sent,
                    article.error_msg,
                ),
            )
            row = await cur.fetchone()
        await conn.commit()
        return row[0] if row else None


async def article_exists(url_hash: str) -> bool:
    """检查文章是否已存在（基于 url_hash 去重）。"""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM articles WHERE url_hash = %s LIMIT 1",
                (url_hash,),
            )
            return await cur.fetchone() is not None


async def update_article_ai_fields(
    url_hash: str,
    ai_summary: str | None,
    ai_category: str | None,
    ai_score: int,
) -> None:
    """更新文章的 AI 分析结果字段。"""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE articles
                SET ai_summary = %s, ai_category = %s, ai_score = %s
                WHERE url_hash = %s
                """,
                (ai_summary, ai_category, ai_score, url_hash),
            )
        await conn.commit()


async def insert_failure_record(
    article_id: int, stage: str, error_msg: str | None = None
) -> int:
    """记录处理失败信息到 processing_failures 表。

    Args:
        article_id: 失败文章的 ID
        stage: 失败阶段 ('dedup', 'keyword_match', 'dify', 'storage')
        error_msg: 错误信息

    Returns:
        插入的失败记录 ID
    """
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO processing_failures (article_id, stage, error_msg)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (article_id, stage, error_msg),
            )
            record_id = (await cur.fetchone())[0]
        await conn.commit()
        return record_id


async def resolve_failures(article_id: int) -> None:
    """将指定文章的未解决失败记录标记为已解决。

    NOTE: 此函数用于 Phase 4 重试机制。当前 PipelineCoordinator 不调用，
    但在实现重试处理时使用。

    Args:
        article_id: 文章 ID
    """
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE processing_failures
                SET resolved = TRUE, resolved_at = NOW()
                WHERE article_id = %s AND resolved = FALSE
                """,
                (article_id,),
            )
        await conn.commit()


async def get_unsent_dingtalk_articles(limit: int = 100) -> list[dict]:
    """查询需要补推的钉钉文章：dingtalk_sent=false + 有 AI 分析结果。"""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT url_hash, title, url, source, ai_summary, ai_category, ai_score,
                       published_at
                FROM articles
                WHERE dingtalk_sent = FALSE
                  AND matched_keywords != '[]'::jsonb
                  AND ai_summary IS NOT NULL
                ORDER BY collected_at ASC
                LIMIT %s
                """,
                (limit,),
            )
            rows = await cur.fetchall()
    return [
        {
            "url_hash": r[0], "title": r[1], "url": r[2], "source": r[3],
            "ai_summary": r[4], "ai_category": r[5], "ai_score": r[6],
            "published_at": r[7].strftime("%Y-%m-%d %H:%M") if r[7] else None,
        }
        for r in rows
    ]


async def count_unsent_dingtalk_articles() -> int:
    """统计待推送钉钉的文章数量：dingtalk_sent=false + 有 AI 分析结果。"""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT COUNT(*)
                FROM articles
                WHERE dingtalk_sent = FALSE
                  AND matched_keywords != '[]'::jsonb
                  AND ai_summary IS NOT NULL
            """)
            row = await cur.fetchone()
    return row[0]


async def mark_articles_dingtalk_sent(url_hashes: list[str]) -> int:
    """标记文章为已推送钉钉。返回更新的行数。"""
    if not url_hashes:
        return 0
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE articles
                SET dingtalk_sent = TRUE, dingtalk_sent_at = NOW()
                WHERE url_hash = ANY(%s)
                """,
                (url_hashes,),
            )
            count = cur.rowcount
        await conn.commit()
    return count


async def fetch_keywords() -> list[dict[str, str]]:
    """从数据库加载所有启用的关键词。"""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT keyword, category FROM keywords WHERE enabled = TRUE"
            )
            rows = await cur.fetchall()
    return [{"keyword": row[0], "category": row[1]} for row in rows]


async def get_schedule_config() -> dict | None:
    """获取调度配置。表不存在时返回 None。"""
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT hours, enabled FROM schedule_config WHERE id = 1"
                )
                row = await cur.fetchone()
        if row:
            return {"hours": row[0], "enabled": row[1]}
        return None
    except Exception as e:
        logger.warning(f"获取调度配置失败: {e}")
        return None


async def update_schedule_config(hours: list[int], enabled: bool) -> None:
    """更新调度配置（upsert 模式）。"""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO schedule_config (id, hours, enabled, updated_at)
                VALUES (1, %s, %s, NOW())
                ON CONFLICT (id) DO UPDATE
                    SET hours = EXCLUDED.hours,
                        enabled = EXCLUDED.enabled,
                        updated_at = NOW()
                """,
                (Jsonb(hours), enabled),
            )
        await conn.commit()


async def get_today_stats() -> dict:
    """今日采集统计：总数、关键词命中数、高分文章数、已推送数。"""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE matched_keywords != '[]'::jsonb) AS matched,
                    COUNT(*) FILTER (WHERE ai_score >= 3) AS high_score,
                    COUNT(*) FILTER (WHERE dingtalk_sent = TRUE) AS pushed
                FROM articles
                WHERE collected_at >= CURRENT_DATE
            """)
            row = await cur.fetchone()
    return {"total": row[0], "matched": row[1], "high_score": row[2], "pushed": row[3]}


async def get_recent_articles(
    limit: int = 20,
    offset: int = 0,
    category: str | None = None,
    min_score: int | None = None,
    search: str | None = None,
    dingtalk_sent: bool | None = None,
) -> tuple[list[dict], int]:
    """查询最近文章 — 支持分页 + 分类/评分/搜索筛选。"""
    conditions: list[str] = []
    params: list = []

    if category:
        conditions.append("ai_category = %s")
        params.append(category)
    if min_score is not None:
        conditions.append("ai_score >= %s")
        params.append(min_score)
    if search:
        conditions.append("title ILIKE %s")
        params.append(f"%{search}%")
    if dingtalk_sent is not None:
        conditions.append("dingtalk_sent = %s")
        params.append(dingtalk_sent)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT COUNT(*) FROM articles {where}",
                params,
            )
            total = (await cur.fetchone())[0]

            await cur.execute(
                f"""
                SELECT id, title, url, source, source_category, ai_category, ai_score, ai_summary, collected_at
                FROM articles {where}
                ORDER BY collected_at DESC
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            rows = await cur.fetchall()

    articles = [
        {
            "id": r[0],
            "title": r[1],
            "url": r[2],
            "source": r[3],
            "source_category": r[4],
            "category": r[5],
            "score": r[6],
            "summary": r[7],
            "collected_at": r[8].isoformat() if r[8] else None,
        }
        for r in rows
    ]
    return articles, total


async def get_site_status() -> list[dict]:
    """各站点今日采集状态。"""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT source,
                       COUNT(*) AS today_count,
                       MAX(collected_at) AS last_collected
                FROM articles
                WHERE collected_at >= CURRENT_DATE
                GROUP BY source
                ORDER BY source
            """)
            rows = await cur.fetchall()
    return [
        {
            "source": r[0],
            "today_count": r[1],
            "last_collected": r[2].isoformat() if r[2] else None,
        }
        for r in rows
    ]


async def list_all_keywords(page: int = 1, page_size: int = 20) -> dict:
    """分页返回关键词（含启用/禁用状态）。"""
    offset = (page - 1) * page_size
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT COUNT(*) FROM keywords")
            total = (await cur.fetchone())[0]
            await cur.execute(
                "SELECT id, keyword, category, enabled, created_at FROM keywords ORDER BY id LIMIT %s OFFSET %s",
                (page_size, offset),
            )
            rows = await cur.fetchall()
    return {
        "keywords": [
            {
                "id": r[0],
                "keyword": r[1],
                "category": r[2],
                "enabled": r[3],
                "created_at": r[4].isoformat() if r[4] else None,
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
    }


async def add_keyword(keyword: str, category: str | None = None) -> int | None:
    """添加关键词（重复则跳过）。返回新 ID 或 None。"""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO keywords (keyword, category)
                VALUES (%s, %s)
                ON CONFLICT (keyword) DO NOTHING
                RETURNING id
                """,
                (keyword, category),
            )
            row = await cur.fetchone()
        await conn.commit()
    return row[0] if row else None


async def delete_keyword(keyword_id: int) -> bool:
    """删除关键词。返回是否成功删除。"""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM keywords WHERE id = %s",
                (keyword_id,),
            )
            deleted = cur.rowcount > 0
        await conn.commit()
    return deleted


# === 站点调度配置 CRUD ===

async def get_site_schedules() -> dict[str, dict]:
    """获取所有站点调度配置，返回 {site_name: {cron_expression, enabled, updated_at}} 字典。
    不含 __default__ 行。
    """
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT site_name, cron_expression, enabled, updated_at
                FROM site_schedules
                WHERE site_name != '__default__'
                ORDER BY site_name
                """
            )
            rows = await cur.fetchall()
            return {
                r[0]: {
                    "cron_expression": r[1],
                    "enabled": r[2],
                    "updated_at": r[3].isoformat() if r[3] else None,
                }
                for r in rows
            }


async def get_default_schedule() -> dict | None:
    """获取全局默认调度配置（__default__ 行）。"""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT cron_expression, enabled, updated_at
                FROM site_schedules
                WHERE site_name = '__default__'
                """
            )
            row = await cur.fetchone()
            if not row:
                return None
            return {
                "cron_expression": row[0],
                "enabled": row[1],
                "updated_at": row[2].isoformat() if row[2] else None,
            }


async def upsert_site_schedule(
    site_name: str,
    cron_expression: str,
    enabled: bool,
) -> dict:
    """插入或更新站点调度配置。site_name='__default__' 时更新全局默认。"""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO site_schedules (site_name, cron_expression, enabled, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (site_name) DO UPDATE
                    SET cron_expression = EXCLUDED.cron_expression,
                        enabled = EXCLUDED.enabled,
                        updated_at = NOW()
                RETURNING site_name, cron_expression, enabled, updated_at
                """,
                (site_name, cron_expression, enabled),
            )
            row = await cur.fetchone()
            await conn.commit()
            return {
                "site_name": row[0],
                "cron_expression": row[1],
                "enabled": row[2],
                "updated_at": row[3].isoformat() if row[3] else None,
            }


async def delete_site_schedule(site_name: str) -> bool:
    """删除站点调度配置（恢复到跟随 __default__）。__default__ 本身不可删除。"""
    if site_name == "__default__":
        return False
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM site_schedules WHERE site_name = %s",
                (site_name,),
            )
            deleted = cur.rowcount > 0
        await conn.commit()
    return deleted
