"""
国家卫生健康委员会 (NHC) 采集策略

采集范围：www.nhc.gov.cn 政策法规、财政管理、建议提案 6 个板块（各取最新 1 篇）
- 政策法规: 法律法规、规范性文件、政策解读
- 财政管理: 财政预决算
- 建议提案: 建议、提案

实测反爬要点：
- HTTP GET 直接可用，无需特殊请求头
- 列表页使用 ul.zxxx_list 结构
- 详情页标题: div.tit，日期: meta[name=PubDate]，正文: div.con / div#xw_box
"""

import logging

from app.base_strategy import BaseSiteStrategy
from app.engine import http_get, regex_find_date
from app.models import Article

logger = logging.getLogger(__name__)

BASE = "https://www.nhc.gov.cn"


class NHCStrategy(BaseSiteStrategy):
    site_name = "国家卫生健康委员会"
    site_url = BASE

    CATEGORIES = {
        "政策法规-法律法规": f"{BASE}/wjw/flfg/list.shtml",
        "政策法规-规范性文件": f"{BASE}/wjw/gfxwjj/list.shtml",
        "政策法规-政策解读": f"{BASE}/wjw/zcjd/list.shtml",
        "财政管理-财政预决算": f"{BASE}/wjw/czyjs/list.shtml",
        "建议提案-建议": f"{BASE}/wjw/jiany/list.shtml",
        "建议提案-提案": f"{BASE}/wjw/tia/list.shtml",
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
        """从列表页 ul.zxxx_list 中找到第一个文章链接。"""
        links = page.css("ul.zxxx_list li a")
        if not links:
            # fallback: scan all links matching NHC article pattern
            for link in page.css("a[href]"):
                href = link.attrib.get("href", "")
                if "/20" not in href or ".shtml" not in href:
                    continue
                title = link.attrib.get("title", "") or link.css("::text").get() or ""
                title = title.strip()
                if not title or len(title) < 4:
                    continue
                return _resolve_url(href, base_url), title
            return "", ""

        for link in links:
            href = link.attrib.get("href", "")
            if not href or "/20" not in href or ".shtml" not in href:
                continue
            title = link.attrib.get("title", "") or link.css("::text").get() or ""
            title = title.strip()
            if not title or len(title) < 4:
                continue
            return _resolve_url(href, base_url), title

        return "", ""

    @staticmethod
    def _extract_title(page) -> str:
        for sel in ["div.tit", "h1", ".article-title"]:
            els = page.css(sel)
            if els:
                text = els[0].css("::text").get("") or ""
                if text.strip():
                    return text.strip()
        return ""

    @staticmethod
    def _extract_date(page) -> str:
        els = page.css('meta[name="PubDate"]')
        if els:
            content = els[0].attrib.get("content", "")
            if content.strip():
                return content.strip()
        # fallback selectors
        for sel in [".article-date", ".pubtime", "span[class*='date']"]:
            els = page.css(sel)
            if els:
                text = els[0].css("::text").get("") or ""
                if text.strip():
                    return text.strip()
        return regex_find_date(page)

    @staticmethod
    def _extract_content(page) -> str:
        for sel in ["div.con", "div#xw_box", "#ivs_content", ".Custom_UnionStyle", ".TRS_Editor", ".content"]:
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
