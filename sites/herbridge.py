"""植提桥 (herbridge.com) 采集策略

采集范围：资讯列表页最新 1 篇
- 资讯: /news/list_news.html 列表首条 → /news/{slug}.html 详情

实测反爬要点：
- 服务端渲染 HTML，Scrapling Fetcher 直接可用
- 列表选择器: div.list-box，标题 div.subject h6 a，日期 span.date.fr
- 详情页选择器: 标题 div.content div.title，正文 div.content div.main
- 日期格式: YYYY-MM-DD
"""

import logging

from app.base_strategy import BaseSiteStrategy
from app.engine import http_get
from app.models import Article

logger = logging.getLogger(__name__)

BASE = "https://www.herbridge.com"

LIST_URL = f"{BASE}/news/list_news.html"


class HerbridgeStrategy(BaseSiteStrategy):
    site_name = "植提桥"
    site_url = BASE

    def __init__(self, **kwargs):
        pass

    def fetch_latest(self) -> list[Article]:
        page = http_get(LIST_URL, retry_delay=3)
        if not page:
            logger.warning("植提桥资讯列表页请求失败")
            return []

        boxes = page.css("div.list-box")
        if not boxes:
            logger.warning("植提桥列表页未找到文章")
            return []

        # 取第一条（最新）
        url, title, published = _parse_list_item(boxes[0])
        if not url or not title:
            logger.warning("植提桥首条文章解析失败")
            return []

        logger.info(f"  资讯: {title[:40]}")
        return [
            Article(
                title=title,
                url=url,
                source=f"{self.site_name}-资讯",
                source_category="资讯",
                published_at=published,
            )
        ]

    def fetch_article(self, url: str) -> Article | None:
        page = http_get(url, retry_delay=3)
        if not page:
            return None
        return Article(
            title=_extract_title(page) or "（无标题）",
            url=url,
            source=self.site_name,
            published_at=_extract_date(page),
            content=_extract_content(page),
        )


# ---- 列表页解析 ----


def _parse_list_item(box) -> tuple[str, str, str]:
    """从列表项 div.list-box 提取 url、title、date。"""
    # 标题和链接
    links = box.css("div.subject div.title h6 a")
    if not links:
        return "", "", ""
    href = links[0].attrib.get("href", "")
    title = (links[0].css("::text").get() or "").strip()
    if not href.startswith("http"):
        href = BASE + href
    # 日期：span.date 内含图标 span + 日期文本，遍历所有文本节点
    date = _extract_date_from_span(box, "span.date")
    return href, title, date


def _extract_date_from_span(parent, selector: str) -> str:
    """从含图标子元素的 span 中提取日期文本。"""
    import re

    date_re = re.compile(r"(20\d{2}-\d{2}-\d{2})")
    els = parent.css(selector) if isinstance(selector, str) else [parent]
    for el in els:
        for t in el.css("::text"):
            text = (t.get() or "").strip()
            m = date_re.search(text)
            if m:
                return m.group(1)
    return ""


# ---- 详情页解析 ----


def _extract_title(page) -> str:
    els = page.css("div.content div.title")
    if els:
        return (els[0].css("::text").get() or "").strip()
    return ""


def _extract_date(page) -> str:
    for item in page.css("div.content div.bar span.item"):
        icon = item.css("span.xinda-icon")
        if icon and "icon-time" in (icon[0].attrib.get("class", "")):
            return _extract_date_from_span(item, None)
    return ""


def _extract_content(page) -> str:
    els = page.css("div.content div.main")
    if els:
        texts = [t.get().strip() for t in els[0].css("::text") if t.get().strip()]
        if texts:
            return "\n".join(texts)
    return ""
