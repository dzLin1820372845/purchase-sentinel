"""BaseSiteStrategy ABC 约束测试"""
import pytest

from app.base_strategy import BaseSiteStrategy
from app.models import Article


# ── 测试用子类 ──────────────────────────────────────────


class _OnlyFetchArticle(BaseSiteStrategy):
    """只实现 fetch_article，缺少 fetch_latest"""

    site_name = "不完整策略"

    def fetch_article(self, url: str) -> Article | None:
        return None


class _OnlyFetchLatest(BaseSiteStrategy):
    """只实现 fetch_latest，缺少 fetch_article"""

    site_name = "不完整策略"

    def fetch_latest(self) -> list[Article]:
        return []


class _FullStrategy(BaseSiteStrategy):
    """完整实现两个抽象方法"""

    site_name = "测试站点"
    site_url = "https://test.com"

    def fetch_latest(self) -> list[Article]:
        return []

    def fetch_article(self, url: str) -> Article | None:
        return None


# ── 测试 ────────────────────────────────────────────────


class TestBaseStrategyABC:
    """验证 ABC 约束行为"""

    def test_cannot_instantiate_abc(self):
        """直接实例化 BaseSiteStrategy() 必须抛出 TypeError"""
        with pytest.raises(TypeError):
            BaseSiteStrategy()

    def test_subclass_without_fetch_latest(self):
        """只实现 fetch_article 的子类不能实例化"""
        with pytest.raises(TypeError):
            _OnlyFetchArticle()  # type: ignore[abstract]

    def test_subclass_without_fetch_article(self):
        """只实现 fetch_latest 的子类不能实例化"""
        with pytest.raises(TypeError):
            _OnlyFetchLatest()  # type: ignore[abstract]

    def test_concrete_subclass_works(self):
        """同时实现两个抽象方法的完整子类可以正常实例化"""
        s = _FullStrategy()
        assert s.site_name == "测试站点"
        assert s.site_url == "https://test.com"
        # 类属性可读写
        s.site_name = "改名"
        assert s.site_name == "改名"

    def test_default_class_attributes(self):
        """未覆盖的 site_name 和 site_url 默认为空字符串"""
        # 使用动态创建的子类来验证默认值
        class _Minimal(BaseSiteStrategy):
            def fetch_latest(self) -> list[Article]:
                return []

            def fetch_article(self, url: str) -> Article | None:
                return None

        s = _Minimal()
        assert s.site_name == ""
        assert s.site_url == ""

    def test_subclass_with_custom_attributes(self):
        """子类定义 site_name 和 site_url 后属性值正确"""
        s = _FullStrategy()
        assert s.site_name == "测试站点"
        assert s.site_url == "https://test.com"
