"""
进出口食品安全信息平台 (exim.foodmate.net) 采集策略

采集范围：信息动态页 7 个板块（各取最新 1 篇）
- 最新信息动态: /news/ 首区列表
- 美国/欧盟/澳大利亚/日本/韩国/香港: /news/area.php?areaid=XXX

实测反爬要点：
- HTTP GET 直接可用，无需特殊请求头
- 最新信息动态选择器: div.m_l > ul > li > a[title]
- 区域列表选择器: li.catlist_li，日期 span.f_r.px11，链接 a[title]
- 正文选择器: h1.title，div.info（时间），div#article
"""

import logging

from app.base_strategy import BaseSiteStrategy
from app.engine import http_get
from app.models import Article

logger = logging.getLogger(__name__)

BASE = "https://exim.foodmate.net"


class EximStrategy(BaseSiteStrategy):
    site_name = "进出口食品安全信息平台"
    site_url = f"{BASE}/"

    CATEGORIES = {
        "最新信息动态": f"{BASE}/news/",
        "美国": f"{BASE}/news/area.php?areaid=394",
        "欧盟": f"{BASE}/news/area.php?areaid=397",
        "澳大利亚": f"{BASE}/news/area.php?areaid=395",
        "日本": f"{BASE}/news/area.php?areaid=398",
        "韩国": f"{BASE}/news/area.php?areaid=399",
        "香港": f"{BASE}/news/area.php?areaid=33",
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

            url, title = self._find_latest_link(page, cat_name)
            if url and title:
                url = _resolve_url(url)
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
    def _find_latest_link(page, cat_name: str) -> tuple[str, str]:
        # Area pages use li.catlist_li format
        if cat_name != "最新信息动态":
            for li in page.css("li.catlist_li"):
                link = li.css("a[title]")
                if not link:
                    continue
                href = link[0].attrib.get("href", "")
                title = link[0].attrib.get("title", "").strip()
                if href and title and len(title) >= 4:
                    return href, title
        # Main news page: first <li><a[title]> in the top section
        for link in page.css("div.m_l li a[title]"):
            href = link.attrib.get("href", "")
            title = link.attrib.get("title", "").strip()
            if href and title and len(title) >= 4:
                return href, title
        return "", ""

    @staticmethod
    def _extract_title(page) -> str:
        for sel in ["h1.title", "h1", ".article-title"]:
            els = page.css(sel)
            if els:
                text = els[0].css("::text").get() or ""
                if text.strip():
                    return text.strip()
        return ""

    @staticmethod
    def _extract_date(page) -> str:
        import re
        info_els = page.css("div.info")
        if info_els:
            all_text = " ".join((t.get() or "") for t in info_els[0].css("::text"))
            m = re.search(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}", all_text)
            if m:
                return m.group()
        for sel in [".article-date", ".pubtime", "span[class*='date']"]:
            els = page.css(sel)
            if els:
                all_text = " ".join((t.get() or "") for t in els[0].css("::text"))
                m = re.search(r"\d{4}-\d{2}-\d{2}", all_text)
                if m:
                    return m.group()
        return ""

    @staticmethod
    def _extract_content(page) -> str:
        for sel in ["div#article", "div.content", "#ivs_content", ".TRS_Editor"]:
            els = page.css(sel)
            if els:
                texts = [t.get().strip() for t in els[0].css("::text") if t.get().strip()]
                if texts:
                    return "\n".join(texts)
        paras = page.css("p::text")
        return "\n".join(p.get().strip() for p in paras if len(p.get().strip()) > 5)


def _resolve_url(href: str) -> str:
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return BASE + href
    return href
