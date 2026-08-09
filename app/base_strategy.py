"""采集策略基类 — 所有网站采集策略的抽象接口"""
from abc import ABC, abstractmethod

from app.models import Article


class BaseSiteStrategy(ABC):
    """网站采集策略抽象基类。

    子类必须定义：
    - site_name: str — 站点名称（如 "国家药监局"）
    - site_url: str — 站点首页 URL
    - fetch_latest() -> list[Article] — 获取最新文章列表
    - fetch_article(url: str) -> Article | None — 获取单篇文章详情
    """

    site_name: str = ""
    site_url: str = ""

    @abstractmethod
    def fetch_latest(self) -> list[Article]:
        """获取最新文章列表。返回 Article 列表（content 可为空，待 fetch_article 填充）。"""

    @abstractmethod
    def fetch_article(self, url: str) -> Article | None:
        """获取单篇文章详情。返回完整的 Article 或 None（获取失败时）。"""
