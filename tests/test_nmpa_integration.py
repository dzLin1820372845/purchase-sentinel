"""NMPA 策略与框架集成测试 — 验证 NMPAStrategy 与 BaseSiteStrategy、ScraperEngine、策略加载器的集成"""
from unittest.mock import MagicMock, patch

import pytest

from app.base_strategy import BaseSiteStrategy
from app.config import load_strategies
from app.engine import ScraperEngine
from sites.nmpa import NMPAStrategy


# ── 类结构和属性测试 ──────────────────────────────────────


def test_nmpa_is_subclass_of_base_strategy():
    """NMPAStrategy 是 BaseSiteStrategy 的子类"""
    assert issubclass(NMPAStrategy, BaseSiteStrategy)


def test_nmpa_has_required_class_attributes():
    """类属性 site_name、site_url、CATEGORIES 正确设置"""
    strategy = NMPAStrategy()
    assert strategy.site_name == "国家药监局"
    assert strategy.site_url.startswith("https://www.nmpa.gov.cn")
    assert hasattr(strategy, "CATEGORIES")
    assert isinstance(strategy.CATEGORIES, dict)
    assert len(strategy.CATEGORIES) > 0


# ── __init__ kwargs 测试 ──────────────────────────────────


def test_nmpa_init_with_categories_kwarg():
    """categories 参数过滤板块子集"""
    strategy = NMPAStrategy(categories=["化妆品监管工作", "化妆品法规工作"])
    assert len(strategy.CATEGORIES) == 2
    assert "化妆品监管工作" in strategy.CATEGORIES
    assert "化妆品法规工作" in strategy.CATEGORIES


def test_nmpa_init_with_name_kwarg_ignored():
    """name 参数不影响策略（NMPAStrategy.__init__ 只处理 categories）"""
    strategy = NMPAStrategy(name="自定义名称", categories=["化妆品监管工作"])
    # site_name 不被 name 覆盖
    assert strategy.site_name == "国家药监局"
    assert len(strategy.CATEGORIES) == 1


def test_nmpa_init_no_kwargs_uses_all_categories():
    """无参数时使用全部 6 个化妆品板块"""
    strategy = NMPAStrategy()
    assert len(strategy.CATEGORIES) == 6


# ── ScraperEngine 集成测试 ────────────────────────────────


def test_engine_can_run_nmpa_strategy():
    """ScraperEngine 可以调度 NMPA 策略（mock http_get）"""
    strategy = NMPAStrategy(categories=["化妆品监管工作"])
    engine = ScraperEngine(delay=0)

    # mock http_get 返回 None（请求失败）
    with patch("sites.nmpa.http_get") as mock_get:
        mock_get.return_value = None
        articles = engine.run(strategy)
        assert articles == []


# ── 策略加载器集成测试 ────────────────────────────────────


def test_load_strategies_loads_nmpa_from_config():
    """load_strategies() 从真实 config.yaml 加载 NMPA 策略"""
    strategies = load_strategies()
    assert len(strategies) >= 1
    assert isinstance(strategies[0], NMPAStrategy)
    assert strategies[0].site_name == "国家药监局"


def test_full_pipeline_mock():
    """完整链路 mock 测试：config.yaml -> 加载策略 -> 引擎调度 -> 返回文章"""
    strategies = load_strategies()
    engine = ScraperEngine(delay=0)

    with patch("sites.nmpa.http_get") as mock_get:
        # mock 列表页返回包含 1 个链接的页面
        mock_page = MagicMock()
        mock_page.status = 200
        mock_page.css.return_value = []  # 简化：不匹配任何链接
        mock_get.return_value = mock_page

        articles = engine.run_all(strategies)
        assert isinstance(articles, list)
