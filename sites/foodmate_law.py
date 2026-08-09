"""
食品法规中心 (law.foodmate.net) 采集策略

采集范围：4 个板块 8 个子类（各取最新 1 篇）
- 政策法规: 推荐政策法规、国家法规、国外法规、地方法规
- 法规草案: 国家法规草案、推荐法规草案、地方法规草案
- 法规解读: 最新 1 篇
- 法规动态: 最新 1 篇

实测反爬要点：
- HTTP GET 直接可用，无需特殊请求头
- 推荐列表选择器: div.mod_infolistd LI，日期 span.posttimeaa，链接 A[title]
- 分类列表选择器: a[alt][href*="show-"]，日期 span.lb_ft
- 正文选择器: h1.title，div.info（发布日期），div#article
"""

import logging
import re

from app.base_strategy import BaseSiteStrategy
from app.engine import http_get
from app.models import Article

logger = logging.getLogger(__name__)

BASE = "https://law.foodmate.net"


class FoodmateLawStrategy(BaseSiteStrategy):
    site_name = "食品法规中心"
    site_url = f"{BASE}/"

    CATEGORIES = {
        "政策法规-推荐政策法规": f"{BASE}/rule/",
        "政策法规-国家法规": f"{BASE}/guojia/list_1.html",
        "政策法规-国外法规": f"{BASE}/guowai/list_1.html",
        "政策法规-地方法规": f"{BASE}/difang/list_1.html",
        "法规草案-推荐法规草案": f"{BASE}/draft/",
        "法规草案-国家法规草案": f"{BASE}/draft/list-1874.html",
        "法规草案-地方法规草案": f"{BASE}/draft/list-1877.html",
        "法规解读": f"{BASE}/jiedu/",
        "法规动态": f"{BASE}/dongtai/",
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
        # Recommended lists on /rule/ and /draft/ use LI > SPAN.posttimeaa + A
        if "推荐" in cat_name:
            for li in page.css("div.mod_infolistd LI"):
                link = li.css("A[title]")
                if not link:
                    continue
                href = link[0].attrib.get("href", "")
                title = link[0].attrib.get("title", "").strip()
                if href and title and len(title) >= 4:
                    return href, title
        # Category list pages: a[alt][href*="show-"] format
        for a in page.css('a[alt][href*="show-"]'):
            href = a.attrib.get("href", "")
            title = a.attrib.get("alt", "").strip()
            if href and title and len(title) >= 4:
                return href, title
        # Fallback: any link to show- page
        for a in page.css('a[href*="show-"]'):
            href = a.attrib.get("href", "")
            if not href:
                continue
            title = a.attrib.get("title", "") or a.attrib.get("alt", "") or ""
            title = title.strip()
            if not title:
                bold = a.css("b::text")
                if bold:
                    title = bold[0].get().strip()
            if title and len(title) >= 4:
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
        info_els = page.css("div.info")
        if info_els:
            all_text = " ".join((t.get() or "") for t in info_els[0].css("::text"))
            m = re.search(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(:\d{2})?", all_text)
            if m:
                return m.group()
        for sel in [".article-date", ".pubtime", "span.lb_ft"]:
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
