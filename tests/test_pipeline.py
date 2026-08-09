"""PipelineCoordinator 管道协调器单元测试（mocked 依赖）"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models import Article


def _make_article(
    title="测试文章",
    url="https://example.com/test",
    source="测试来源",
    content="文章内容",
):
    return Article(title=title, url=url, source=source, content=content)


class TestPipelineCoordinator:
    """PipelineCoordinator 单元测试"""

    async def test_process_happy_path(self):
        """新文章 -> 关键词匹配 -> LLM 分析 -> 入库"""
        from app.pipeline import PipelineCoordinator

        matcher = MagicMock()
        matcher.match.return_value = [{"keyword": "处罚", "category": "综合"}]

        llm = AsyncMock()
        llm.analyze.return_value = (
            {
                "summary": "测试摘要",
                "category": "综合",
                "score": 3,
            },
            None,
        )

        dingtalk = AsyncMock()
        dingtalk.push_articles.return_value = {"pushed": 1, "failed": 0}

        with (
            pytest.MonkeyPatch.context() as mp,
        ):
            mock_exists = AsyncMock(return_value=False)
            mock_insert = AsyncMock(return_value=42)
            mock_failure = AsyncMock(return_value=1)
            mp.setattr("app.pipeline.article_exists", mock_exists)
            mp.setattr("app.pipeline.insert_article", mock_insert)
            mp.setattr("app.pipeline.insert_failure_record", mock_failure)
            mp.setattr("app.pipeline.mark_articles_dingtalk_sent", AsyncMock())

            coordinator = PipelineCoordinator(matcher, llm, dingtalk)
            article = _make_article()
            stats = await coordinator.process_articles([article])

        assert stats["total"] == 1
        assert stats["duplicates"] == 0
        assert stats["processed"] == 1
        assert stats["failed"] == 0
        assert stats["dingtalk_pushed"] == 1
        llm.analyze.assert_awaited_once_with("测试文章", "文章内容", "处罚")
        mock_insert.assert_awaited_once()

    async def test_process_duplicate_skips_llm(self):
        """重复 URL 跳过，不调用 LLM"""
        from app.pipeline import PipelineCoordinator

        matcher = MagicMock()
        llm = AsyncMock()
        dingtalk = AsyncMock()

        with pytest.MonkeyPatch.context() as mp:
            mock_exists = AsyncMock(return_value=True)
            mock_insert = AsyncMock()
            mp.setattr("app.pipeline.article_exists", mock_exists)
            mp.setattr("app.pipeline.insert_article", mock_insert)
            mp.setattr("app.pipeline.mark_articles_dingtalk_sent", AsyncMock())

            coordinator = PipelineCoordinator(matcher, llm, dingtalk)
            article = _make_article()
            stats = await coordinator.process_articles([article])

        assert stats["total"] == 1
        assert stats["duplicates"] == 1
        assert stats["processed"] == 0
        assert stats["failed"] == 0
        llm.analyze.assert_not_awaited()
        mock_insert.assert_not_awaited()

    async def test_process_llm_failure_stores_article(self):
        """LLM 失败时文章照常入库，记录失败"""
        from app.pipeline import PipelineCoordinator

        matcher = MagicMock()
        matcher.match.return_value = [{"keyword": "处罚", "category": "综合"}]

        llm = AsyncMock()
        llm.analyze.return_value = (None, "LLM analysis failed: raw=stub")

        dingtalk = AsyncMock()

        with pytest.MonkeyPatch.context() as mp:
            mock_exists = AsyncMock(return_value=False)
            mock_insert = AsyncMock(return_value=42)
            mock_failure = AsyncMock(return_value=1)
            mp.setattr("app.pipeline.article_exists", mock_exists)
            mp.setattr("app.pipeline.insert_article", mock_insert)
            mp.setattr("app.pipeline.insert_failure_record", mock_failure)
            mp.setattr("app.pipeline.mark_articles_dingtalk_sent", AsyncMock())

            coordinator = PipelineCoordinator(matcher, llm, dingtalk)
            article = _make_article()
            stats = await coordinator.process_articles([article])

        assert stats["total"] == 1
        assert stats["duplicates"] == 0
        assert stats["processed"] == 1
        assert stats["failed"] == 0
        mock_insert.assert_awaited_once()
        mock_failure.assert_awaited_once_with(42, "llm", "LLM analysis failed: raw=stub")

    async def test_process_mixed_batch(self):
        """混合批次：1篇新+命中，1篇重复，1篇新+无匹配"""
        from app.pipeline import PipelineCoordinator

        matcher = MagicMock()
        llm = AsyncMock()
        llm.analyze.return_value = ({"summary": "s", "category": "c", "score": 3}, None)

        dingtalk = AsyncMock()

        articles = [
            _make_article(title="新1", url="https://example.com/1"),
            _make_article(title="重复", url="https://example.com/2"),
            _make_article(title="新2", url="https://example.com/3"),
        ]

        with pytest.MonkeyPatch.context() as mp:
            exists_results = iter([False, True, False])
            mock_exists = AsyncMock(side_effect=lambda _: next(exists_results))
            mock_insert = AsyncMock(return_value=1)

            # 第一篇有匹配，第三篇没有
            match_results = iter(
                [[{"keyword": "处罚", "category": "综合"}], []]
            )
            matcher.match.side_effect = lambda _: next(match_results)

            mp.setattr("app.pipeline.article_exists", mock_exists)
            mp.setattr("app.pipeline.insert_article", mock_insert)
            mp.setattr(
                "app.pipeline.insert_failure_record", AsyncMock(return_value=1)
            )
            mp.setattr("app.pipeline.mark_articles_dingtalk_sent", AsyncMock())

            coordinator = PipelineCoordinator(matcher, llm, dingtalk)
            stats = await coordinator.process_articles(articles)

        assert stats["total"] == 3
        assert stats["duplicates"] == 1
        assert stats["processed"] == 2

    async def test_process_error_isolation(self):
        """一篇文章异常不影响其他文章"""
        from app.pipeline import PipelineCoordinator

        matcher = MagicMock()
        matcher.match.return_value = []
        llm = AsyncMock()
        dingtalk = AsyncMock()

        articles = [
            _make_article(title="异常文章", url="https://example.com/err"),
            _make_article(title="正常文章", url="https://example.com/ok"),
        ]

        with pytest.MonkeyPatch.context() as mp:
            mock_exists = AsyncMock(return_value=False)
            # 第一次 insert 抛异常，第二次正常
            mock_insert = AsyncMock(side_effect=[Exception("DB error"), 2])
            mp.setattr("app.pipeline.article_exists", mock_exists)
            mp.setattr("app.pipeline.insert_article", mock_insert)
            mp.setattr(
                "app.pipeline.insert_failure_record", AsyncMock(return_value=1)
            )
            mp.setattr("app.pipeline.mark_articles_dingtalk_sent", AsyncMock())

            coordinator = PipelineCoordinator(matcher, llm, dingtalk)
            stats = await coordinator.process_articles(articles)

        assert stats["failed"] == 1
        assert stats["processed"] == 1

    async def test_process_content_none(self):
        """content=None 不崩溃"""
        from app.pipeline import PipelineCoordinator

        matcher = MagicMock()
        matcher.match.return_value = []
        llm = AsyncMock()
        dingtalk = AsyncMock()

        with pytest.MonkeyPatch.context() as mp:
            mock_exists = AsyncMock(return_value=False)
            mock_insert = AsyncMock(return_value=1)
            mp.setattr("app.pipeline.article_exists", mock_exists)
            mp.setattr("app.pipeline.insert_article", mock_insert)
            mp.setattr(
                "app.pipeline.insert_failure_record", AsyncMock(return_value=1)
            )
            mp.setattr("app.pipeline.mark_articles_dingtalk_sent", AsyncMock())

            coordinator = PipelineCoordinator(matcher, llm, dingtalk)
            article = _make_article(content=None)
            stats = await coordinator.process_articles([article])

        assert stats["processed"] == 1

    async def test_process_no_keywords_skips_llm(self):
        """无匹配关键词时不调用 LLM"""
        from app.pipeline import PipelineCoordinator

        matcher = MagicMock()
        matcher.match.return_value = []
        llm = AsyncMock()
        dingtalk = AsyncMock()

        with pytest.MonkeyPatch.context() as mp:
            mock_exists = AsyncMock(return_value=False)
            mock_insert = AsyncMock(return_value=1)
            mp.setattr("app.pipeline.article_exists", mock_exists)
            mp.setattr("app.pipeline.insert_article", mock_insert)
            mp.setattr(
                "app.pipeline.insert_failure_record", AsyncMock(return_value=1)
            )
            mp.setattr("app.pipeline.mark_articles_dingtalk_sent", AsyncMock())

            coordinator = PipelineCoordinator(matcher, llm, dingtalk)
            article = _make_article()
            stats = await coordinator.process_articles([article])

        llm.analyze.assert_not_awaited()
        assert stats["processed"] == 1

    async def test_process_keyword_string_format(self):
        """多个关键词逗号分隔传给 LLM"""
        from app.pipeline import PipelineCoordinator

        matcher = MagicMock()
        matcher.match.return_value = [
            {"keyword": "处罚", "category": "综合"},
            {"keyword": "召回", "category": "综合"},
        ]

        llm = AsyncMock()
        llm.analyze.return_value = ({"summary": "s", "category": "c", "score": 3}, None)

        dingtalk = AsyncMock()
        dingtalk.push_articles.return_value = {"pushed": 0, "failed": 0}

        with pytest.MonkeyPatch.context() as mp:
            mock_exists = AsyncMock(return_value=False)
            mock_insert = AsyncMock(return_value=1)
            mp.setattr("app.pipeline.article_exists", mock_exists)
            mp.setattr("app.pipeline.insert_article", mock_insert)
            mp.setattr(
                "app.pipeline.insert_failure_record", AsyncMock(return_value=1)
            )
            mp.setattr("app.pipeline.mark_articles_dingtalk_sent", AsyncMock())

            coordinator = PipelineCoordinator(matcher, llm, dingtalk)
            article = _make_article()
            await coordinator.process_articles([article])

        llm.analyze.assert_awaited_once()
        call_args = llm.analyze.call_args
        assert call_args[0][2] == "处罚,召回"

    async def test_process_low_score_still_stored(self):
        """score=1 的文章仍然入库 (PROC-05)"""
        from app.pipeline import PipelineCoordinator

        matcher = MagicMock()
        matcher.match.return_value = [{"keyword": "处罚", "category": "综合"}]

        llm = AsyncMock()
        llm.analyze.return_value = ({"summary": "低分摘要", "category": "综合", "score": 1}, None)

        dingtalk = AsyncMock()
        dingtalk.push_articles.return_value = {"pushed": 1, "failed": 0}

        with pytest.MonkeyPatch.context() as mp:
            mock_exists = AsyncMock(return_value=False)
            mock_insert = AsyncMock(return_value=1)
            mp.setattr("app.pipeline.article_exists", mock_exists)
            mp.setattr("app.pipeline.insert_article", mock_insert)
            mp.setattr(
                "app.pipeline.insert_failure_record", AsyncMock(return_value=1)
            )
            mp.setattr("app.pipeline.mark_articles_dingtalk_sent", AsyncMock())

            coordinator = PipelineCoordinator(matcher, llm, dingtalk)
            article = _make_article()
            stats = await coordinator.process_articles([article])

        mock_insert.assert_awaited_once()
        assert stats["processed"] == 1


class TestDingtalkIntegration:
    """钉钉推送集成测试"""

    async def test_dingtalk_push_after_processing(self):
        """处理完匹配文章后调用 dingtalk.push_articles()"""
        from app.pipeline import PipelineCoordinator

        matcher = MagicMock()
        matcher.match.return_value = [{"keyword": "处罚", "category": "综合"}]

        llm = AsyncMock()
        llm.analyze.return_value = ({"summary": "s", "category": "c", "score": 3}, None)

        dingtalk = AsyncMock()
        dingtalk.push_articles.return_value = {"pushed": 1, "failed": 0}

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("app.pipeline.article_exists", AsyncMock(return_value=False))
            mp.setattr("app.pipeline.insert_article", AsyncMock(return_value=1))
            mp.setattr("app.pipeline.insert_failure_record", AsyncMock(return_value=1))
            mp.setattr("app.pipeline.mark_articles_dingtalk_sent", AsyncMock())

            coordinator = PipelineCoordinator(matcher, llm, dingtalk)
            article = _make_article()
            stats = await coordinator.process_articles([article])

        assert stats["dingtalk_pushed"] == 1
        dingtalk.push_articles.assert_awaited()

    async def test_dingtalk_only_pushes_matched(self):
        """仅推送匹配到关键词的文章"""
        from app.pipeline import PipelineCoordinator

        matcher = MagicMock()
        matcher.match.return_value = []  # 无匹配

        llm = AsyncMock()
        dingtalk = AsyncMock()
        dingtalk.push_articles.return_value = {"pushed": 0, "failed": 0}

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("app.pipeline.article_exists", AsyncMock(return_value=False))
            mp.setattr("app.pipeline.insert_article", AsyncMock(return_value=1))
            mp.setattr("app.pipeline.insert_failure_record", AsyncMock(return_value=1))
            mp.setattr("app.pipeline.mark_articles_dingtalk_sent", AsyncMock())

            coordinator = PipelineCoordinator(matcher, llm, dingtalk)
            stats = await coordinator.process_articles([_make_article()])

        assert stats.get("dingtalk_pushed", 0) == 0

    async def test_dingtalk_failure_isolation(self):
        """推送异常不影响主流程 stats"""
        from app.pipeline import PipelineCoordinator

        matcher = MagicMock()
        matcher.match.return_value = [{"keyword": "处罚", "category": "综合"}]

        llm = AsyncMock()
        llm.analyze.return_value = ({"summary": "s", "category": "c", "score": 3}, None)

        dingtalk = AsyncMock()
        dingtalk.push_articles.side_effect = Exception("DingTalk API down")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("app.pipeline.article_exists", AsyncMock(return_value=False))
            mp.setattr("app.pipeline.insert_article", AsyncMock(return_value=1))
            mp.setattr("app.pipeline.insert_failure_record", AsyncMock(return_value=1))
            mp.setattr("app.pipeline.mark_articles_dingtalk_sent", AsyncMock())

            coordinator = PipelineCoordinator(matcher, llm, dingtalk)
            stats = await coordinator.process_articles([_make_article()])

        assert stats["processed"] == 1  # 主流程正常
        assert "dingtalk_error" in stats  # 错误被捕获

    async def test_catchup_push(self):
        """匹配文章处理后推送到钉钉（push_articles 返回 pushed 计数）"""
        from app.pipeline import PipelineCoordinator

        matcher = MagicMock()
        matcher.match.return_value = [{"keyword": "处罚", "category": "综合"}]

        llm = AsyncMock()
        llm.analyze.return_value = ({"summary": "s", "category": "c", "score": 3}, None)

        dingtalk = AsyncMock()
        dingtalk.push_articles.return_value = {"pushed": 2, "failed": 0}

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("app.pipeline.article_exists", AsyncMock(return_value=False))
            mp.setattr("app.pipeline.insert_article", AsyncMock(return_value=1))
            mp.setattr("app.pipeline.insert_failure_record", AsyncMock(return_value=1))
            mp.setattr("app.pipeline.mark_articles_dingtalk_sent", AsyncMock())

            coordinator = PipelineCoordinator(matcher, llm, dingtalk)
            stats = await coordinator.process_articles([_make_article()])

        assert stats["dingtalk_pushed"] == 2
        # push_articles 至少被调用一次
        assert dingtalk.push_articles.await_count >= 1
