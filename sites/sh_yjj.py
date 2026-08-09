"""
上海市药品监督管理局 采集策略

采集范围：信息公开页 (https://yjj.sh.gov.cn/xxgk/)
- 最新公开信息: 综合、药品、化妆品、医疗器械（各取最新 1 篇）
- 监管信息: 许可信息公告、监督抽检信息（各取最新 1 篇）
  注: 行政处罚公告在 sjcx.yjj.sh.gov.cn 外部子站，暂不采集

实测反爬要点：
- HTTP GET 直接可用，无需特殊请求头
- 正文选择器: #ivs_content，标题: #ivs_title，日期: small.PBtime
"""

import logging

from app.base_strategy import BaseSiteStrategy
from app.engine import http_get, regex_find_date
from app.models import Article

logger = logging.getLogger(__name__)

BASE = "https://yjj.sh.gov.cn"


class ShanghaiYJJStrategy(BaseSiteStrategy):
    site_name = "上海药监局"
    site_url = f"{BASE}/xxgk/"

    CATEGORIES = {
        "最新公开信息-综合": f"{BASE}/zh/index.html",
        "最新公开信息-药品": f"{BASE}/zx-yp/index.html",
        "最新公开信息-化妆品": f"{BASE}/zx-hzp/index.html",
        "最新公开信息-医疗器械": f"{BASE}/zx-ylqx/index.html",
        "监管信息-许可信息公告": f"{BASE}/xkxxgg/index.html",
        "监管信息-监督抽检信息": f"{BASE}/jdcjxx/index.html",
    }

    def __init__(self, **kwargs):
        if "categories" in kwargs:
            names = kwargs["categories"]
            self.CATEGORIES = {
                k: v for k, v in self.CATEGORIES.items() if k in names
            }

    def fetch_latest(self) -> list[Article]:
        articles = []
        for cat_name, list_url in self.CATEGORIES.items():
            logger.info(f"  板块: {cat_name}")
            page = http_get(list_url, retry_delay=3)
            if not page:
                logger.warning(f"  板块列表页请求失败: {cat_name}")
                continue

            url, title = self._find_latest_link(page, list_url)
            if url and title:
                articles.append(Article(
                    title=title,
                    url=url,
                    source=f"{self.site_name}-{cat_name}",
                    source_category=cat_name,
                ))
                logger.info(f"    ✓ {title[:40]}")
            else:
                logger.warning(f"    板块未找到文章链接: {cat_name}")
        return articles

    def fetch_article(self, url: str) -> Article | None:
        page = http_get(url, retry_delay=3)
        if not page:
            return None
        return Article(
            title=self._extract_title(page) or "（无标题）",
            url=url,
            source=self.site_name,
            published_at=self._extract_date(page),
            content=self._extract_content(page),
        )

    @staticmethod
    def _find_latest_link(page, base_url: str) -> tuple[str, str]:
        for link in page.css("a[href]"):
            href = link.attrib.get("href", "")
            if "/20" not in href or ".html" not in href:
                continue
            title = link.css("::text").get() or link.attrib.get("title", "")
            title = title.strip()
            if not title or len(title) < 4:
                continue
            return _resolve_url(href, base_url), title
        return "", ""

    @staticmethod
    def _extract_title(page) -> str:
        for sel in ["#ivs_title", "h1", ".article-title"]:
            els = page.css(sel)
            if els:
                text = els[0].css("::text").get("") or ""
                if text.strip():
                    return text.strip()
        return ""

    @staticmethod
    def _extract_date(page) -> str:
        for sel in ["small.PBtime", ".article-date", ".pubtime", "span[class*='date']"]:
            els = page.css(sel)
            if els:
                text = els[0].css("::text").get("") or ""
                if text.strip():
                    return text.strip()
        return regex_find_date(page)

    @staticmethod
    def _extract_content(page) -> str:
        for sel in ["#ivs_content", ".Custom_UnionStyle", ".TRS_Editor", ".content"]:
            els = page.css(sel)
            if els:
                texts = [t.get().strip() for t in els[0].css("::text") if t.get().strip()]
                if texts:
                    return "\n".join(texts)
        paras = page.css("p::text")
        return "\n".join(p.get().strip() for p in paras if len(p.get().strip()) > 5)


def _resolve_url(href: str, base_url: str) -> str:
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return BASE + href
    from urllib.parse import urljoin
    return urljoin(base_url, href)
