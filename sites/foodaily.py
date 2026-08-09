"""
每日食品网 (foodaily.com) 采集策略

采集范围：快讯页最新 1 篇
- 快讯: /newsflashes 列表首条

实测反爬要点：
- 需要浏览器 User-Agent，裸 curl 返回 403
- Scrapling Fetcher impersonate=chrome 直接可用
- 列表选择器: div.newsflash-item，标题 a.title，日期 div.date-card
  (span.month + span.day)，内容 div.message
- 详情页无绝对日期，仅"X小时前"；列表页 date-card 有月/日
"""

import logging

from app.base_strategy import BaseSiteStrategy
from app.engine import http_get
from app.models import Article

logger = logging.getLogger(__name__)

BASE = "https://www.foodaily.com"


class FoodailyStrategy(BaseSiteStrategy):
    site_name = "每日食品网"
    site_url = BASE

    CATEGORIES = {
        "快讯": f"{BASE}/newsflashes",
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

            url, title, published = self._find_latest(page)
            if url and title:
                articles.append(Article(
                    title=title,
                    url=url,
                    source=f"{self.site_name}-{cat_name}",
                    source_category=cat_name,
                    published_at=published,
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
    def _find_latest(page) -> tuple[str, str, str]:
        for item in page.css("div.newsflash-item"):
            title_el = item.css("a.title")
            if not title_el:
                continue
            href = title_el[0].attrib.get("href", "")
            title = (title_el[0].css("::text").get() or "").strip()
            if not href or not title or len(title) < 4:
                continue
            published = _extract_date_card(item)
            return href, title, published
        return "", "", ""

    @staticmethod
    def _extract_title(page) -> str:
        for sel in ["div.title", "h1", ".article-title"]:
            els = page.css(sel)
            if els:
                text = els[0].css("::text").get() or ""
                if text.strip():
                    return text.strip()
        return ""

    @staticmethod
    def _extract_date(page) -> str:
        date_card = page.css("div.date-card")
        if date_card:
            return _extract_date_card(date_card[0])
        return ""

    @staticmethod
    def _extract_content(page) -> str:
        for sel in ["div.message pre", "div.message", "div.content"]:
            els = page.css(sel)
            if els:
                texts = [t.get().strip() for t in els[0].css("::text") if t.get().strip()]
                if texts:
                    return "\n".join(texts)
        paras = page.css("p::text")
        return "\n".join(p.get().strip() for p in paras if len(p.get().strip()) > 5)


def _extract_date_card(el) -> str:
    import datetime, re
    month = el.css("span.month::text")
    day = el.css("span.day::text")
    if month and day:
        today = datetime.date.today()
        m = re.sub(r"[月日]", "", month[0].get().strip())
        d = re.sub(r"[月日]", "", day[0].get().strip())
        return f"{today.year}-{int(m):02d}-{int(d):02d}"
    return ""
