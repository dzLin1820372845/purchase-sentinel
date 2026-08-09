"""数据库集成测试 — 需要 PostgreSQL 运行"""
import pytest
import pytest_asyncio

import app.database as db_module
from app.database import (
    article_exists,
    insert_article,
    insert_failure_record,
    resolve_failures,
    update_article_ai_fields,
)
from app.models import Article


class TestPoolInitialization:
    """连接池初始化测试"""

    async def test_pool_initialized(self, db_pool):
        """init_pool() 后 pool 已打开"""
        assert db_module.pool is not None

    async def test_pool_connection_works(self, db_pool):
        """pool 可以获取连接并执行查询"""
        async with db_module.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
                result = await cur.fetchone()
                assert result == (1,)


class TestPgvectorExtension:
    """pgvector 扩展测试"""

    async def test_pgvector_extension(self, db_pool):
        """pgvector 扩展已安装"""
        async with db_module.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT extname FROM pg_extension WHERE extname = 'vector'"
                )
                result = await cur.fetchone()
                assert result is not None
                assert result[0] == "vector"


class TestArticlesTableSchema:
    """articles 表结构测试"""

    async def test_articles_table_schema(self, db_pool):
        """articles 表包含全部 16 列（id + 15 业务字段）"""
        expected_columns = {
            "id",
            "url_hash",
            "title",
            "url",
            "source",
            "source_type",
            "published_at",
            "collected_at",
            "content",
            "matched_keywords",
            "ai_summary",
            "ai_category",
            "ai_score",
            "dingtalk_sent",
            "dingtalk_sent_at",
            "error_msg",
        }
        async with db_module.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'articles'"
                )
                rows = await cur.fetchall()
                actual_columns = {row[0] for row in rows}
                assert expected_columns == actual_columns, (
                    f"Missing: {expected_columns - actual_columns}, "
                    f"Extra: {actual_columns - expected_columns}"
                )


class TestRequiredIndexes:
    """索引测试"""

    async def test_required_indexes(self, db_pool):
        """articles 表包含全部 6 个索引"""
        expected_indexes = {
            "idx_articles_collected_at",
            "idx_articles_source",
            "idx_articles_ai_score",
            "idx_articles_url_hash",
            "idx_articles_ai_category",
            "idx_articles_dingtalk_sent",
        }
        async with db_module.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT indexname FROM pg_indexes WHERE tablename = 'articles'"
                )
                rows = await cur.fetchall()
                actual_indexes = {row[0] for row in rows}
                for idx in expected_indexes:
                    assert idx in actual_indexes, f"Missing index: {idx}"


class TestKeywordsTable:
    """keywords 表测试"""

    async def test_keywords_table_exists(self, db_pool):
        """keywords 表存在且有正确的列"""
        expected_columns = {"id", "keyword", "category", "enabled", "created_at"}
        async with db_module.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'keywords'"
                )
                rows = await cur.fetchall()
                actual_columns = {row[0] for row in rows}
                assert expected_columns == actual_columns

    async def test_init_seed_keywords(self, db_pool):
        """keywords 表包含初始种子数据（处罚、召回、不合格、抽检）"""
        expected_keywords = {"处罚", "召回", "不合格", "抽检"}
        async with db_module.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT keyword FROM keywords")
                rows = await cur.fetchall()
                actual_keywords = {row[0] for row in rows}
                assert expected_keywords.issubset(actual_keywords), (
                    f"Missing keywords: {expected_keywords - actual_keywords}"
                )


class TestInsertArticle:
    """insert_article CRUD 测试"""

    async def test_insert_article(self, db_pool, clean_articles):
        """插入一篇测试文章，返回 article_id (int)"""
        article = Article(
            title="测试文章标题",
            url="https://example.com/test-article",
            source="测试来源",
            source_type="website",
            published_at="2024-01-15",
            content="文章正文内容",
            matched_keywords=[
                {"keyword": "处罚", "category": "综合"},
                {"keyword": "召回", "category": "综合"},
            ],
        )
        result = await insert_article(article)
        assert isinstance(result, int)
        assert result > 0

    async def test_insert_article_duplicate_returns_none(self, db_pool, clean_articles):
        """插入相同 url_hash 的文章，第二次返回 None"""
        article = Article(
            title="去重测试",
            url="https://example.com/dedup-test",
            source="测试",
        )
        result1 = await insert_article(article)
        result2 = await insert_article(article)
        assert isinstance(result1, int)
        assert result1 > 0
        assert result2 is None

    async def test_article_exists(self, db_pool, clean_articles):
        """插入后 article_exists() 返回 True"""
        article = Article(
            title="存在性测试",
            url="https://example.com/exists-test",
            source="测试",
        )
        assert await article_exists(article.url_hash) is False
        await insert_article(article)
        assert await article_exists(article.url_hash) is True


class TestUpdateArticleAiFields:
    """update_article_ai_fields 测试"""

    async def test_update_article_ai_fields(self, db_pool, clean_articles):
        """插入文章后更新 AI 字段，查询验证更新成功"""
        article = Article(
            title="AI更新测试",
            url="https://example.com/ai-update-test",
            source="测试",
        )
        await insert_article(article)

        await update_article_ai_fields(
            url_hash=article.url_hash,
            ai_summary="这是一篇AI生成的摘要",
            ai_category="药品",
            ai_score=4,
        )

        async with db_module.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT ai_summary, ai_category, ai_score "
                    "FROM articles WHERE url_hash = %s",
                    (article.url_hash,),
                )
                row = await cur.fetchone()
                assert row is not None
                assert row[0] == "这是一篇AI生成的摘要"
                assert row[1] == "药品"
                assert row[2] == 4


class TestMatchedKeywordsJsonb:
    """matched_keywords JSONB 字段测试"""

    async def test_matched_keywords_jsonb(self, db_pool, clean_articles):
        """插入带 list[dict] matched_keywords 的文章，读回验证正确存储"""
        article = Article(
            title="JSONB测试",
            url="https://example.com/jsonb-test",
            source="测试",
            matched_keywords=[
                {"keyword": "处罚", "category": "综合"},
                {"keyword": "召回", "category": "综合"},
            ],
        )
        await insert_article(article)

        async with db_module.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT matched_keywords FROM articles WHERE url_hash = %s",
                    (article.url_hash,),
                )
                row = await cur.fetchone()
                assert row is not None
                keywords = row[0]
                assert isinstance(keywords, list)
                assert keywords == [
                    {"keyword": "处罚", "category": "综合"},
                    {"keyword": "召回", "category": "综合"},
                ]


class TestProcessingFailures:
    """processing_failures 表测试"""

    async def test_processing_failures_table_exists(self, db_pool):
        """processing_failures 表存在且有正确的列"""
        expected_columns = {
            "id",
            "article_id",
            "stage",
            "error_msg",
            "retry_count",
            "resolved",
            "created_at",
            "resolved_at",
        }
        async with db_module.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'processing_failures'"
                )
                rows = await cur.fetchall()
                actual_columns = {row[0] for row in rows}
                assert expected_columns == actual_columns, (
                    f"Missing: {expected_columns - actual_columns}, "
                    f"Extra: {actual_columns - expected_columns}"
                )

    async def test_insert_failure_record(self, db_pool, clean_articles):
        """插入失败记录成功"""
        article = Article(
            title="失败测试",
            url="https://example.com/failure-test",
            source="测试",
        )
        article_id = await insert_article(article)
        assert article_id is not None

        record_id = await insert_failure_record(
            article_id, "dify", "Dify analysis failed"
        )
        assert isinstance(record_id, int)
        assert record_id > 0

    async def test_resolve_failures(self, db_pool, clean_articles):
        """resolve_failures 标记失败记录为已解决"""
        article = Article(
            title="解决测试",
            url="https://example.com/resolve-test",
            source="测试",
        )
        article_id = await insert_article(article)
        await insert_failure_record(article_id, "dify", "test error")

        await resolve_failures(article_id)

        async with db_module.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT resolved FROM processing_failures "
                    "WHERE article_id = %s",
                    (article_id,),
                )
                row = await cur.fetchone()
                assert row is not None
                assert row[0] is True


class TestDingtalkFunctions:
    """钉钉推送相关数据库函数测试"""

    async def test_get_unsent_dingtalk_articles_empty(self, db_pool, clean_articles):
        """无未推送文章时返回空列表"""
        from app.database import get_unsent_dingtalk_articles
        result = await get_unsent_dingtalk_articles()
        assert result == []

    async def test_get_unsent_returns_only_matched_with_ai(self, db_pool, clean_articles):
        """仅返回有 AI 分析结果的未推送文章"""
        from app.database import get_unsent_dingtalk_articles, insert_article
        # 文章 1: 有匹配+有 AI -> 应返回
        a1 = Article(title="命中", url="https://ex.com/1", source="s",
                     matched_keywords=[{"keyword": "处罚", "category": "综合"}],
                     ai_summary="摘要", ai_category="药品", ai_score=3)
        # 文章 2: 无匹配 -> 不应返回
        a2 = Article(title="未命中", url="https://ex.com/2", source="s")
        await insert_article(a1)
        await insert_article(a2)

        result = await get_unsent_dingtalk_articles()
        assert len(result) == 1
        assert result[0]["title"] == "命中"

    async def test_get_unsent_skips_already_sent(self, db_pool, clean_articles):
        """已推送的文章不在结果中"""
        from app.database import get_unsent_dingtalk_articles, insert_article
        a = Article(title="已推送", url="https://ex.com/sent", source="s",
                    matched_keywords=[{"keyword": "处罚", "category": "综合"}],
                    ai_summary="摘要", ai_score=3, dingtalk_sent=True)
        await insert_article(a)
        result = await get_unsent_dingtalk_articles()
        assert result == []

    async def test_mark_articles_dingtalk_sent(self, db_pool, clean_articles):
        """标记后 dingtalk_sent=True, dingtalk_sent_at 非空"""
        from app.database import insert_article, mark_articles_dingtalk_sent
        a = Article(title="标记测试", url="https://ex.com/mark", source="s")
        await insert_article(a)

        count = await mark_articles_dingtalk_sent([a.url_hash])
        assert count == 1

        async with db_module.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT dingtalk_sent, dingtalk_sent_at FROM articles WHERE url_hash = %s", (a.url_hash,))
                row = await cur.fetchone()
        assert row[0] is True
        assert row[1] is not None

    async def test_mark_articles_dingtalk_sent_empty(self, db_pool, clean_articles):
        """空列表返回 0"""
        from app.database import mark_articles_dingtalk_sent
        count = await mark_articles_dingtalk_sent([])
        assert count == 0
