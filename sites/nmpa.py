"""
国家药监局 (NMPA) 采集策略

采集范围：药品、医疗器械、化妆品三个 tab 下各子板块（各取最新 1 篇）
- 药品: 监管工作、公告通告、法规文件、政策解读
- 医疗器械: 监管工作、公告通知、法规文件、政策解读、飞行检查、召回
- 化妆品: 监管工作、法规文件、政策解读、公告通知、抽检通告、飞行检查

实测反爬要点：
- NMPA 要求 Sec-Fetch-Dest/Mode/Site/User 等 Fetch Metadata 请求头，缺少则返回 412
- 通过 engine.http_get() 传入 NMPA_HEADERS（Sec-Fetch-* + Upgrade-Insecure-Requests）
- engine.http_get 自动合并 DEFAULT_HEADERS（Accept/Accept-Language）+ 自定义 headers
- 文章 URL 规律：路径中含 10+ 位数字 + .html
- 正文选择器: #ivs_content → .Custom_UnionStyle → .TRS_Editor（按优先级）
- 政策解读类文章多为图解海报，正文无可提取文字
"""

import logging
import re

from app.base_strategy import BaseSiteStrategy
from app.engine import http_get, regex_find_date
from app.models import Article

logger = logging.getLogger(__name__)

BASE = "https://www.nmpa.gov.cn"

ARTICLE_URL_PATTERN = re.compile(r".*\d{10,}.*\.html?$")

NMPA_HEADERS = {
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


class NMPAStrategy(BaseSiteStrategy):
    site_name = "国家药监局"
    site_url = f"{BASE}/hzhp/index.html"

    CATEGORIES = {
        # 药品
        "药品-监管工作": f"{BASE}/yaopin/ypjgdt/index.html",
        "药品-公告通告": f"{BASE}/yaopin/ypggtg/index.html",
        "药品-法规文件": f"{BASE}/yaopin/ypfgwj/index.html",
        "药品-政策解读": f"{BASE}/yaopin/ypzhcjd/index.html",
        # 医疗器械
        "医疗器械-监管工作": f"{BASE}/ylqx/ylqxjgdt/index.html",
        "医疗器械-公告通知": f"{BASE}/ylqx/ylqxggtg/index.html",
        "医疗器械-法规文件": f"{BASE}/ylqx/ylqxfgwj/index.html",
        "医疗器械-政策解读": f"{BASE}/ylqx/ylqxzhcjd/index.html",
        "医疗器械-飞行检查": f"{BASE}/xxgk/fxjzh/ylqxfxjch/index.html",
        "医疗器械-召回": f"{BASE}/xxgk/chpzhh/ylqxzhh/index.html",
        # 化妆品
        "化妆品-监管工作": f"{BASE}/hzhp/hzhpjgdt/index.html",
        "化妆品-法规文件": f"{BASE}/hzhp/hzhpfgwj/index.html",
        "化妆品-政策解读": f"{BASE}/hzhp/hzhpzcjd/index.html",
        "化妆品-公告通知": f"{BASE}/hzhp/hzhpjmtg/index.html",
        "化妆品-抽检通告": f"{BASE}/hzhp/hzhpcjgg/index.html",
        "化妆品-飞行检查": f"{BASE}/xxgk/fxjzh/hzhpfxjch/index.html",
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
            page = http_get(list_url, headers=NMPA_HEADERS, retry_delay=5)
            if not page:
                logger.warning(f"  板块列表页请求失败: {cat_name}")
                continue

            url, title = self._find_latest_article_link(page, list_url)
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
        page = http_get(url, headers=NMPA_HEADERS, retry_delay=5)
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

    @staticmethod
    def _find_latest_article_link(page, base_url: str) -> tuple[str, str]:
        for link in page.css("a[href]"):
            href = link.attrib.get("href", "")
            if not ARTICLE_URL_PATTERN.match(href):
                continue
            title = link.css("::text").get() or link.attrib.get("title", "")
            title = title.strip()
            if not title:
                continue
            full_url = _resolve_url(href, base_url)
            return full_url, title
        return "", ""

    @staticmethod
    def _extract_title(page) -> str:
        for sel in ["h1", ".article-title", ".title"]:
            els = page.css(sel)
            if els:
                text = els[0].css("::text").get("") or ""
                if text.strip():
                    return text.strip()
        return ""

    @staticmethod
    def _extract_date(page) -> str:
        meta = page.css('meta[name="PubDate"]')
        if meta:
            content = meta[0].attrib.get("content", "")
            if content.strip():
                return content.strip()
        for sel in [".date", ".article-date", ".pubtime", "span[class*='date']", "span[class*='time']"]:
            els = page.css(sel)
            if els:
                text = els[0].css("::text").get("") or ""
                if text.strip():
                    return text.strip()
        return regex_find_date(page)

    @staticmethod
    def _extract_content(page) -> str:
        for sel in [
            "#ivs_content",
            ".Custom_UnionStyle",
            ".article-content",
            ".TRS_Editor",
            ".text-content",
            ".content",
        ]:
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
        paras = page.css("p::text")
        texts = [p.get().strip() for p in paras if len(p.get().strip()) > 5]
        return "\n".join(texts[:50])


def _resolve_url(href: str, base_url: str) -> str:
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return BASE + href
    parts = base_url.rsplit("/", 1)[0].split("/")
    for seg in href.split("/"):
        if seg == "..":
            parts.pop()
        elif seg and seg != ".":
            parts.append(seg)
    return "/".join(parts)
