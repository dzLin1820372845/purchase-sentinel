"""示例采集策略 — 展示 BaseSiteStrategy 的标准实现模式。

将此文件作为模板，替换 site_name / site_url / CATEGORIES 和解析逻辑即可。
实际项目中策略可通过 AI 自动生成 —— 提供网站 URL，AI 分析页面结构后生成策略文件。
"""

import logging
import re

from app.base_strategy import BaseSiteStrategy
from app.engine import http_get, regex_find_date
from app.models import Article

logger = logging.getLogger(__name__)

BASE = "https://example.com"

# 文章详情页 URL 匹配模式（按需修改）
ARTICLE_URL_PATTERN = re.compile(r".*/article/\d+\.html$")


class ExampleStrategy(BaseSiteStrategy):
    """示例策略 — 继承 BaseSiteStrategy，实现 fetch_latest 和 fetch_article。

    引擎统一处理：HTTP 请求封装（TLS 指纹、自动重试、频率控制）、
    错误隔离（单篇失败不中断）、日志记录。
    策略文件只需关心：列表页怎么解析、详情页怎么提取。
    """

    site_name = "示例站点"
    site_url = BASE

    # 板块名 → 列表页 URL（按需定义）
    CATEGORIES = {
        "最新资讯": f"{BASE}/news/",
    }

    def __init__(self, **kwargs):
        """接收 config.yaml 传入的 categories 参数，筛选启用的板块。"""
        if "categories" in kwargs:
            names = kwargs["categories"]
            self.CATEGORIES = {
                k: v for k, v in self.CATEGORIES.items() if k in names
            }

    def fetch_latest(self) -> list[Article]:
        """遍历各板块列表页，提取最新文章链接。

        返回 Article 列表（content 可为空，由 fetch_article 填充）。
        """
        articles = []

        for cat_name, list_url in self.CATEGORIES.items():
            logger.info(f"  板块: {cat_name}")
            page = http_get(list_url)
            if not page:
                logger.warning(f"  板块列表页请求失败: {cat_name}")
                continue

            for link in page.css("a[href]"):
                href = link.attrib.get("href", "")
                if not ARTICLE_URL_PATTERN.match(href):
                    continue
                title = link.css("::text").get() or link.attrib.get("title", "")
                title = title.strip()
                if not title:
                    continue

                full_url = self._resolve_url(href, list_url)
                articles.append(Article(
                    title=title,
                    url=full_url,
                    source=f"{self.site_name}-{cat_name}",
                    source_category=cat_name,
                ))
                break  # 每个板块只取最新 1 篇

        return articles

    def fetch_article(self, url: str) -> Article | None:
        """请求文章详情页，提取标题、日期、正文。

        返回完整的 Article 或 None（获取失败时）。
        """
        page = http_get(url)
        if not page:
            return None

        title = self._extract_title(page)
        date = self._extract_date(page)
        content = self._extract_content(page)

        return Article(
            title=title or "（无标题）",
            url=url,
            source=self.site_name,
            published_at=date,
            content=content,
        )

    # ── 以下为辅助方法，展示常见的提取模式 ──────────────────

    @staticmethod
    def _extract_title(page) -> str:
        """从详情页提取标题，多选择器兜底。"""
        for sel in ["h1", ".article-title", ".title"]:
            els = page.css(sel)
            if els:
                text = els[0].css("::text").get("") or ""
                if text.strip():
                    return text.strip()
        return ""

    @staticmethod
    def _extract_date(page) -> str | None:
        """从详情页提取日期，先试 meta 标签再试 CSS 选择器，最后正则兜底。"""
        meta = page.css('meta[name="PubDate"]')
        if meta:
            content = meta[0].attrib.get("content", "")
            if content.strip():
                return content.strip()
        for sel in [".date", ".pubtime", "span[class*='date']"]:
            els = page.css(sel)
            if els:
                text = els[0].css("::text").get("") or ""
                if text.strip():
                    return text.strip()
        return regex_find_date(page)

    @staticmethod
    def _extract_content(page) -> str:
        """从详情页提取正文，多选择器兜底。"""
        for sel in ["#content", ".article-content", ".content", ".main-text"]:
            els = page.css(sel)
            if els:
                texts = [
                    t.get().strip()
                    for t in els[0].css("::text")
                    if t.get().strip()
                ]
                content = "\n".join(texts)
                if content.strip():
                    return content
        # 最后兜底：提取所有段落文本
        paras = page.css("p::text")
        texts = [p.get().strip() for p in paras if len(p.get().strip()) > 5]
        return "\n".join(texts[:50])

    @staticmethod
    def _resolve_url(href: str, base_url: str) -> str:
        """将相对 URL 解析为绝对 URL。"""
        if href.startswith("http"):
            return href
        if href.startswith("/"):
            return BASE + href
        # 处理相对路径（../）
        parts = base_url.rsplit("/", 1)[0].split("/")
        for seg in href.split("/"):
            if seg == "..":
                parts.pop()
            elif seg and seg != ".":
                parts.append(seg)
        return "/".join(parts)
