"""
食品资讯中心 (news.foodmate.net) 采集策略

采集范围：10 个资讯板块（各取最新 1 篇）
- 中国食品、权威发布、国际食品、国际预警
- 食品科技、外讯导读、产经企业、会展动态
- 食品专题、食品网刊

实测反爬要点：
- HTTP GET 直接可用，无需特殊请求头
- 列表选择器: div.catlist li.catlist_li，日期 span.f_r.px11，链接 a[title]
- 正文选择器: h1.title，div.info（日期），div#article
"""

import logging
import re

from app.base_strategy import BaseSiteStrategy
from app.engine import http_get
from app.models import Article

logger = logging.getLogger(__name__)

BASE = "https://news.foodmate.net"


class FoodmateStrategy(BaseSiteStrategy):
    site_name = "食品资讯中心"
    site_url = f"{BASE}/"

    CATEGORIES = {
        "中国食品": f"{BASE}/guonei/",
        "权威发布": f"{BASE}/quanwei/",
        "国际食品": f"{BASE}/guoji/",
        "国际预警": f"{BASE}/yujing/",
        "食品科技": f"{BASE}/keji/",
        "外讯导读": f"{BASE}/daodu/",
        "产经企业": f"{BASE}/qiye/",
        "会展动态": f"{BASE}/zhanhui/",
        "食品专题": f"{BASE}/special/",
        "食品网刊": f"{BASE}/maillist/",
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

            url, title = self._find_latest_link(page)
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
    def _find_latest_link(page) -> tuple[str, str]:
        for li in page.css("li.catlist_li"):
            link = li.css("a[title]")
            if not link:
                continue
            href = link[0].attrib.get("href", "")
            title = link[0].attrib.get("title", "").strip()
            if href and title and len(title) >= 4:
                return href, title
        for link in page.css("div.catlist a[href]"):
            href = link.attrib.get("href", "")
            if "/20" not in href or ".html" not in href:
                continue
            title = link.attrib.get("title", "") or link.css("::text").get("") or ""
            title = title.strip()
            if not title or len(title) < 4:
                continue
            return href, title
        for link in page.css("li a[title][href]"):
            href = link.attrib.get("href", "")
            title = link.attrib.get("title", "").strip()
            if href and ".html" in href and title and len(title) >= 4:
                return href, title
        return "", ""

    @staticmethod
    def _extract_title(page) -> str:
        for sel in ["h1.title", "h1", ".article-title"]:
            els = page.css(sel)
            if els:
                text = els[0].css("::text").get("") or ""
                if text.strip():
                    return text.strip()
        return ""

    @staticmethod
    def _extract_date(page) -> str:
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
