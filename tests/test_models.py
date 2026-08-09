"""Article Pydantic 模型单元测试"""
import pytest
from pydantic import ValidationError

from app.models import Article


class TestArticleBasicCreation:
    """Article 基本构造测试"""

    def test_article_basic_creation(self):
        """Article(title, url, source) 构造成功"""
        article = Article(title="测试文章", url="https://example.com/1", source="测试来源")
        assert article.title == "测试文章"
        assert article.url == "https://example.com/1"
        assert article.source == "测试来源"

    def test_article_all_fields(self):
        """带所有可选字段的 Article 构造成功"""
        article = Article(
            title="测试文章",
            url="https://example.com/1",
            source="测试来源",
            source_type="wechat",
            published_at="2024-01-01",
            content="文章正文内容",
            matched_keywords=[
                {"keyword": "处罚", "category": "综合"},
                {"keyword": "召回", "category": "综合"},
            ],
            ai_summary="AI摘要",
            ai_category="药品",
            ai_score=4,
            dingtalk_sent=True,
            dingtalk_sent_at="2024-01-01T10:00:00",
            error_msg=None,
        )
        assert article.source_type == "wechat"
        assert article.matched_keywords == [
            {"keyword": "处罚", "category": "综合"},
            {"keyword": "召回", "category": "综合"},
        ]
        assert article.ai_score == 4

    def test_article_matched_keywords_old_format_raises(self):
        """旧格式 matched_keywords=['xxx'] 抛 ValidationError"""
        with pytest.raises(ValidationError):
            Article(
                title="a",
                url="https://example.com",
                source="s",
                matched_keywords=["处罚"],
            )


class TestArticleUrlHash:
    """url_hash 计算测试"""

    def test_article_url_hash_computed(self):
        """url_hash 自动计算，对同一 URL 结果一致"""
        article1 = Article(title="a", url="https://example.com/1", source="s")
        article2 = Article(title="b", url="https://example.com/1", source="s")
        assert article1.url_hash == article2.url_hash
        assert len(article1.url_hash) == 32  # MD5 hex 长度

    def test_article_url_hash_unique(self):
        """不同 URL 产生不同 url_hash"""
        article1 = Article(title="a", url="https://example.com/1", source="s")
        article2 = Article(title="b", url="https://example.com/2", source="s")
        assert article1.url_hash != article2.url_hash


class TestArticleSourceType:
    """source_type 验证测试"""

    def test_article_source_type_default(self):
        """默认 source_type='website'"""
        article = Article(title="a", url="https://example.com", source="s")
        assert article.source_type == "website"

    def test_article_source_type_wechat(self):
        """source_type='wechat' 有效"""
        article = Article(title="a", url="https://example.com", source="s", source_type="wechat")
        assert article.source_type == "wechat"

    def test_article_source_type_invalid(self):
        """source_type='invalid' 抛 ValidationError"""
        with pytest.raises(ValidationError):
            Article(title="a", url="https://example.com", source="s", source_type="invalid")


class TestArticleAiScore:
    """ai_score 范围测试"""

    def test_article_ai_score_range_valid(self):
        """ai_score=0~5 全部有效"""
        for score in range(6):
            article = Article(title="a", url="https://example.com", source="s", ai_score=score)
            assert article.ai_score == score

    def test_article_ai_score_range_invalid_high(self):
        """ai_score=6 抛 ValidationError"""
        with pytest.raises(ValidationError):
            Article(title="a", url="https://example.com", source="s", ai_score=6)

    def test_article_ai_score_range_invalid_negative(self):
        """ai_score=-1 抛 ValidationError"""
        with pytest.raises(ValidationError):
            Article(title="a", url="https://example.com", source="s", ai_score=-1)


class TestArticleFieldValidation:
    """字段验证测试"""

    def test_article_empty_url(self):
        """url='' 抛 ValidationError (min_length=1)"""
        with pytest.raises(ValidationError):
            Article(title="a", url="", source="s")

    def test_article_empty_title(self):
        """title='' 抛 ValidationError (min_length=1)"""
        with pytest.raises(ValidationError):
            Article(title="", url="https://example.com", source="s")

    def test_article_matched_keywords_default(self):
        """matched_keywords 默认为空列表"""
        article = Article(title="a", url="https://example.com", source="s")
        assert article.matched_keywords == []
