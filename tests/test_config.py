"""策略加载器测试 — 验证 config.yaml 解析和策略动态加载"""
import pytest
import yaml

from app.config import load_strategies
from app.base_strategy import BaseSiteStrategy


# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def config_file(tmp_path):
    """创建临时 config.yaml，包含一个启用站点和一个禁用站点"""
    config = {
        "sites": [
            {
                "name": "测试站点",
                "strategy": "sites.nmpa:NMPAStrategy",
                "enabled": True,
                "categories": ["化妆品监管工作"],
            },
            {
                "name": "禁用站点",
                "strategy": "sites.nmpa:NMPAStrategy",
                "enabled": False,
            },
        ]
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(config, allow_unicode=True), encoding="utf-8")
    return path


@pytest.fixture
def empty_config(tmp_path):
    """创建空的 config.yaml（sites 为空列表）"""
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump({"sites": []}, allow_unicode=True), encoding="utf-8")
    return path


@pytest.fixture
def no_sites_config(tmp_path):
    """创建无 sites 键的 config.yaml"""
    path = tmp_path / "config.yaml"
    path.write_text("{}", encoding="utf-8")
    return path


# ── 测试用例 ──────────────────────────────────────────────


def test_load_strategies_returns_enabled_only(config_file):
    """只返回 enabled=True 的策略"""
    strategies = load_strategies(config_file)
    assert len(strategies) == 1


def test_load_strategies_skips_disabled(config_file, caplog):
    """enabled=False 的站点被跳过，日志记录"""
    import logging

    with caplog.at_level(logging.INFO, logger="app.config"):
        strategies = load_strategies(config_file)
    assert len(strategies) == 1
    assert "跳过已禁用站点" in caplog.text


def test_load_strategies_instantiates_with_kwargs(config_file):
    """策略特定参数（如 categories）通过 kwargs 传递给构造函数"""
    strategies = load_strategies(config_file)
    assert len(strategies) == 1
    # 验证 categories 被正确过滤（只有 "化妆品监管工作"）
    assert len(strategies[0].CATEGORIES) == 1
    assert "化妆品监管工作" in strategies[0].CATEGORIES


def test_load_strategies_file_not_found():
    """配置文件不存在时抛出 FileNotFoundError"""
    with pytest.raises(FileNotFoundError, match="配置文件不存在"):
        load_strategies("/不存在的路径/config.yaml")


def test_load_strategies_invalid_path_format(tmp_path):
    """策略路径不含冒号时抛出 ValueError"""
    config = {
        "sites": [
            {
                "name": "错误站点",
                "strategy": "sites.nmpa",  # 缺少冒号分隔符
                "enabled": True,
            }
        ]
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(config, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ValueError, match="策略路径格式错误"):
        load_strategies(path)


def test_load_strategies_empty_config(empty_config):
    """空的 sites 列表返回空策略列表"""
    strategies = load_strategies(empty_config)
    assert strategies == []


def test_load_strategies_no_sites_key(no_sites_config):
    """配置文件无 sites 键时返回空列表"""
    strategies = load_strategies(no_sites_config)
    assert strategies == []


def test_load_strategies_returns_base_strategy_instances(config_file):
    """返回的对象都是 BaseSiteStrategy 子类实例"""
    strategies = load_strategies(config_file)
    for s in strategies:
        assert isinstance(s, BaseSiteStrategy)
