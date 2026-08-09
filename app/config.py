"""策略加载器 — 从 config.yaml 动态加载采集策略"""
import importlib
import logging
from pathlib import Path

import yaml

from app.base_strategy import BaseSiteStrategy

logger = logging.getLogger(__name__)

# 项目根目录下的 config.yaml（per D-07）
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


def load_strategies(config_path: str | Path | None = None) -> list[BaseSiteStrategy]:
    """从配置文件加载所有启用的采集策略。

    Args:
        config_path: 配置文件路径，默认为项目根目录的 config.yaml

    Returns:
        已实例化的策略对象列表

    Raises:
        FileNotFoundError: 配置文件不存在
        ValueError: 策略路径格式错误（缺少冒号分隔符）
        AttributeError: 策略类在模块中不存在
    """
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH

    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")

    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if not config or "sites" not in config:
        logger.warning("配置文件中无 sites 定义")
        return []

    strategies = []
    for site_config in config["sites"]:
        if not site_config.get("enabled", True):
            logger.info(f"跳过已禁用站点: {site_config.get('name', '未知')}")
            continue

        strategy_path = site_config.get("strategy", "")
        if ":" not in strategy_path:
            raise ValueError(
                f"策略路径格式错误，应为 '模块路径:类名'，实际为: {strategy_path}"
            )

        module_path, class_name = strategy_path.rsplit(":", 1)

        try:
            module = importlib.import_module(module_path)
        except ImportError as e:
            logger.error(f"策略模块导入失败: {module_path} -> {e}")
            raise

        strategy_cls = getattr(module, class_name, None)
        if strategy_cls is None:
            raise AttributeError(
                f"策略类 {class_name} 在模块 {module_path} 中不存在"
            )

        # 提取站点特定参数（排除框架字段）
        kwargs = {
            k: v for k, v in site_config.items()
            if k not in ("strategy", "enabled")
        }

        strategy = strategy_cls(**kwargs)
        strategies.append(strategy)
        logger.info(f"加载策略: {strategy.site_name} ({strategy_path})")

    return strategies
