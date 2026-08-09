"""http_get + ScraperEngine 测试"""
from unittest.mock import MagicMock, patch

import pytest

from app.base_strategy import BaseSiteStrategy
from app.engine import DEFAULT_HEADERS, ScraperEngine, http_get
from app.models import Article


# ── 辅助函数 ────────────────────────────────────────────


def make_mock_strategy(
    fetch_latest_return=None,
    fetch_article_return=None,
    fetch_latest_side_effect=None,
    fetch_article_side_effect=None,
    site_name="测试站点",
    site_url="https://test.com",
):
    """创建 mock 策略对象"""
    strategy = MagicMock(spec=BaseSiteStrategy)
    strategy.site_name = site_name
    strategy.site_url = site_url
    if fetch_latest_side_effect:
        strategy.fetch_latest.side_effect = fetch_latest_side_effect
    else:
        strategy.fetch_latest.return_value = fetch_latest_return or []
    if fetch_article_side_effect:
        strategy.fetch_article.side_effect = fetch_article_side_effect
    else:
        strategy.fetch_article.return_value = fetch_article_return
    return strategy


# ── http_get 测试 ───────────────────────────────────────


class TestHttpGet:
    """http_get() 公共请求函数测试"""

    @patch("app.engine.Fetcher")
    def test_http_get_200_returns_response(self, mock_fetcher):
        """200 响应直接返回 Scrapling Response 对象"""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_fetcher.get.return_value = mock_response

        result = http_get("https://example.com")

        assert result is mock_response

    @patch("app.engine.Fetcher")
    def test_http_get_non200_retries_and_returns_none(self, mock_fetcher):
        """非 200 状态码重试 retry 次后返回 None"""
        mock_response = MagicMock()
        mock_response.status = 412
        mock_fetcher.get.return_value = mock_response

        result = http_get("https://example.com", retry=2, retry_delay=0)

        assert result is None
        # retry=2 表示循环 range(2)，调用 2 次
        assert mock_fetcher.get.call_count == 2

    @patch("app.engine.Fetcher")
    def test_http_get_exception_retries_and_returns_none(self, mock_fetcher):
        """请求异常时重试后返回 None"""
        mock_fetcher.get.side_effect = Exception("连接超时")

        result = http_get("https://example.com", retry=2, retry_delay=0)

        assert result is None
        assert mock_fetcher.get.call_count == 2

    @patch("app.engine.Fetcher")
    def test_http_get_merges_headers(self, mock_fetcher):
        """自定义 headers 合并到 DEFAULT_HEADERS（自定义覆盖默认）"""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_fetcher.get.return_value = mock_response

        custom = {"Accept": "application/json", "X-Custom": "value"}
        http_get("https://example.com", headers=custom)

        call_kwargs = mock_fetcher.get.call_args
        passed_headers = call_kwargs.kwargs["headers"]

        # 自定义 Accept 覆盖了默认值
        assert passed_headers["Accept"] == "application/json"
        # 自定义头被保留
        assert passed_headers["X-Custom"] == "value"
        # 默认头中未被覆盖的保留
        assert "Accept-Language" in passed_headers

    @patch("app.engine.Fetcher")
    def test_http_get_retry_zero_no_retry(self, mock_fetcher):
        """retry=0 时只尝试一次不重试"""
        mock_response = MagicMock()
        mock_response.status = 412
        mock_fetcher.get.return_value = mock_response

        result = http_get("https://example.com", retry=0)

        assert result is None
        # retry=0 表示循环 range(0)，不调用
        assert mock_fetcher.get.call_count == 0

    @patch("app.engine.Fetcher")
    def test_http_get_passes_scrapling_params(self, mock_fetcher):
        """http_get 正确传递 stealthy_headers、impersonate、timeout 参数"""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_fetcher.get.return_value = mock_response

        http_get("https://example.com", impersonate="firefox", timeout=60)

        call_kwargs = mock_fetcher.get.call_args
        assert call_kwargs.kwargs["stealthy_headers"] is True
        assert call_kwargs.kwargs["impersonate"] == "firefox"
        assert call_kwargs.kwargs["timeout"] == 60
        assert call_kwargs.kwargs["retries"] == 1


# ── ScraperEngine 测试 ──────────────────────────────────


class TestScraperEngine:
    """ScraperEngine 调度器测试"""

    def test_engine_run_success(self):
        """正常流程：fetch_latest 返回文章，fetch_article 补充详情"""
        articles_from_list = [
            Article(title="文章1", url="https://example.com/1", source="测试"),
            Article(title="文章2", url="https://example.com/2", source="测试"),
        ]
        full_articles = [
            Article(title="文章1", url="https://example.com/1", source="测试", content="正文1"),
            Article(title="文章2", url="https://example.com/2", source="测试", content="正文2"),
        ]
        strategy = make_mock_strategy(
            fetch_latest_return=articles_from_list,
            fetch_article_side_effect=full_articles,
        )

        engine = ScraperEngine(delay=0)
        result = engine.run(strategy)

        assert len(result) == 2
        assert result[0].content == "正文1"
        assert result[1].content == "正文2"
        assert strategy.fetch_article.call_count == 2

    def test_engine_run_fetch_latest_exception(self):
        """fetch_latest 抛出异常时返回空列表"""
        strategy = make_mock_strategy(
            fetch_latest_side_effect=RuntimeError("网络错误"),
        )

        engine = ScraperEngine(delay=0)
        result = engine.run(strategy)

        assert result == []

    def test_engine_run_fetch_article_failure_keeps_original(self):
        """fetch_article 返回 None 时保留原始 Article"""
        articles_from_list = [
            Article(title="文章1", url="https://example.com/1", source="测试"),
        ]
        strategy = make_mock_strategy(
            fetch_latest_return=articles_from_list,
            fetch_article_return=None,
        )

        engine = ScraperEngine(delay=0)
        result = engine.run(strategy)

        assert len(result) == 1
        assert result[0].title == "文章1"
        assert result[0].content is None

    def test_engine_run_fetch_article_exception_keeps_original(self):
        """fetch_article 抛出异常时保留原始 Article"""
        articles_from_list = [
            Article(title="文章1", url="https://example.com/1", source="测试"),
        ]
        strategy = make_mock_strategy(
            fetch_latest_return=articles_from_list,
            fetch_article_side_effect=RuntimeError("详情获取失败"),
        )

        engine = ScraperEngine(delay=0)
        result = engine.run(strategy)

        assert len(result) == 1
        assert result[0].title == "文章1"

    def test_engine_run_content_already_present_skips_fetch(self):
        """content 已有内容时跳过 fetch_article 调用"""
        articles_with_content = [
            Article(title="完整文章", url="https://example.com/1", source="测试", content="已有正文"),
        ]
        strategy = make_mock_strategy(
            fetch_latest_return=articles_with_content,
        )

        engine = ScraperEngine(delay=0)
        result = engine.run(strategy)

        assert len(result) == 1
        assert result[0].content == "已有正文"
        strategy.fetch_article.assert_not_called()

    def test_engine_run_empty_site_name_returns_empty(self):
        """site_name 为空时返回空列表并跳过采集"""
        strategy = make_mock_strategy(site_name="")

        engine = ScraperEngine(delay=0)
        result = engine.run(strategy)

        assert result == []
        strategy.fetch_latest.assert_not_called()

    def test_engine_run_all_multiple_strategies(self):
        """run_all 执行多个策略并合并结果"""
        s1_articles = [
            Article(title="A1", url="https://a.com/1", source="A", content="内容"),
        ]
        s2_articles = [
            Article(title="B1", url="https://b.com/1", source="B", content="内容"),
            Article(title="B2", url="https://b.com/2", source="B", content="内容"),
        ]
        s1 = make_mock_strategy(fetch_latest_return=s1_articles, site_name="站点A")
        s2 = make_mock_strategy(fetch_latest_return=s2_articles, site_name="站点B")

        engine = ScraperEngine(delay=0)
        result = engine.run_all([s1, s2])

        assert len(result) == 3
        assert result[0].title == "A1"
        assert result[1].title == "B1"
        assert result[2].title == "B2"

    def test_engine_run_all_one_failure_continues(self):
        """一个策略失败不影响其他策略执行"""
        s1 = make_mock_strategy(
            fetch_latest_side_effect=RuntimeError("策略1崩溃"),
            site_name="失败策略",
        )
        s2_articles = [
            Article(title="正常文章", url="https://ok.com/1", source="OK", content="内容"),
        ]
        s2 = make_mock_strategy(fetch_latest_return=s2_articles, site_name="正常策略")

        engine = ScraperEngine(delay=0)
        result = engine.run_all([s1, s2])

        assert len(result) == 1
        assert result[0].title == "正常文章"
