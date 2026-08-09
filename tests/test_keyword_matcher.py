"""KeywordMatcher AC 自动机单元测试"""
import pytest

from app.keyword_matcher import KeywordMatcher


SAMPLE_KEYWORDS = [
    {"keyword": "处罚", "category": "综合"},
    {"keyword": "召回", "category": "综合"},
    {"keyword": "不合格", "category": "综合"},
    {"keyword": "抽检", "category": "综合"},
]


class TestKeywordMatcher:
    """KeywordMatcher 单元测试"""

    def test_match_keywords_in_text(self):
        """build() 后 match() 找到所有关键词，返回 dict 列表"""
        matcher = KeywordMatcher()
        matcher.build(SAMPLE_KEYWORDS)
        results = matcher.match("某公司因产品质量不合格被处罚并召回相关产品")
        assert len(results) == 3
        keywords = {r["keyword"] for r in results}
        assert keywords == {"不合格", "处罚", "召回"}
        for r in results:
            assert r["category"] == "综合"

    def test_match_deduplicates_overlapping(self):
        """'处罚处罚' 只返回 1 个 '处罚'"""
        matcher = KeywordMatcher()
        matcher.build(SAMPLE_KEYWORDS)
        results = matcher.match("处罚处罚")
        assert len(results) == 1
        assert results[0]["keyword"] == "处罚"

    def test_match_empty_string(self):
        """空字符串返回 []"""
        matcher = KeywordMatcher()
        matcher.build(SAMPLE_KEYWORDS)
        assert matcher.match("") == []

    def test_match_before_build(self):
        """未 build 时 match() 返回 [] 不报错"""
        matcher = KeywordMatcher()
        assert matcher.match("处罚") == []

    def test_match_no_keywords_in_text(self):
        """无关键词文本返回 []"""
        matcher = KeywordMatcher()
        matcher.build(SAMPLE_KEYWORDS)
        assert matcher.match("这是一篇普通的文章，没有任何敏感词") == []

    def test_match_different_categories(self):
        """不同分类关键词正确返回各自分类"""
        keywords = [
            {"keyword": "处罚", "category": "综合"},
            {"keyword": "疫苗", "category": "药品"},
        ]
        matcher = KeywordMatcher()
        matcher.build(keywords)
        results = matcher.match("某疫苗公司被处罚")
        assert len(results) == 2
        result_map = {r["keyword"]: r["category"] for r in results}
        assert result_map["处罚"] == "综合"
        assert result_map["疫苗"] == "药品"

    def test_rebuild_replaces_automaton(self):
        """rebuild 替换旧自动机"""
        matcher = KeywordMatcher()
        matcher.build([{"keyword": "处罚", "category": "综合"}])
        assert len(matcher.match("处罚")) == 1

        matcher.rebuild([{"keyword": "召回", "category": "综合"}])
        assert matcher.match("处罚") == []
        assert len(matcher.match("召回")) == 1

    def test_build_empty_keywords(self):
        """空关键词列表不崩溃"""
        matcher = KeywordMatcher()
        matcher.build([])
        assert matcher.match("任意文本") == []
