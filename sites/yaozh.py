"""药智网 (yaozh.com) 采集策略

采集范围：首页 4 个板块各取最新 1 篇
- 新闻: 首页"药智新闻"板块首条 → news.yaozh.com/archive/
- 会议: 首页"药智会议"板块首条 → news.yaozh.com/meeting/detail/
- 医械: 首页"药智情报"板块首条 → 多数为微信公众号链接
- 前沿: 首页"行研热点"板块首条 → 微信公众号或 archive 页

实测反爬要点：
- 首页 www.yaozh.com 服务端渲染，Scrapling Fetcher 直接可用
- 文章页 news.yaozh.com/archive/XXXXX.html Nuxt SSR，直接可抓
- 会议页 news.yaozh.com/meeting/detail/XXXX 服务端渲染
- 板块通过 li > a 的 onclick 属性区分（药智新闻/药智会议/药智情报/行研热点）
"""

import logging
import re

from app.base_strategy import BaseSiteStrategy
from app.engine import http_get
from app.models import Article

logger = logging.getLogger(__name__)

BASE = "https://www.yaozh.com"

# onclick 追踪标签 -> 板块名称
_SECTION_LABELS = {
    "药智新闻": "新闻",
    "药智会议": "会议",
    "药智情报": "医械",
    "行研热点": "前沿",
}


class YaozhStrategy(BaseSiteStrategy):
    site_name = "药智网"
    site_url = BASE

    CATEGORIES = {
        "新闻": "新闻",
        "会议": "会议",
        "医械": "医械",
        "前沿": "前沿",
    }

    def __init__(self, **kwargs):
        if "categories" in kwargs:
            names = kwargs["categories"]
            self.CATEGORIES = {
                k: v for k, v in self.CATEGORIES.items() if k in names
            }

    def fetch_latest(self) -> list[Article]:
        page = http_get(BASE, retry_delay=3)
        if not page:
            logger.warning("药智网首页请求失败")
            return []

        section_links = _find_section_links(page)
        articles = []
        for cat_name, (url, title) in section_links.items():
            if cat_name not in self.CATEGORIES:
                continue
            articles.append(
                Article(
                    title=title,
                    url=url,
                    source=f"{self.site_name}-{cat_name}",
                    source_category=cat_name,
                )
            )
            logger.info(f"  {cat_name}: {title[:40]}")
        return articles

    def fetch_article(self, url: str) -> Article | None:
        page = http_get(url, retry_delay=3)
        if not page:
            return None
        if "mp.weixin.qq.com" in url:
            return _parse_wechat(page, url)
        if "/meeting/detail/" in url:
            return _parse_meeting(page, url)
        return _parse_archive(page, url)


# ---- 首页板块解析 ----


def _find_section_links(page) -> dict[str, tuple[str, str]]:
    """从首页 li > a 的 onclick 标签中识别板块，取每板块首条。"""
    result: dict[str, tuple[str, str]] = {}
    for li in page.css("li"):
        anchors = li.css("a")
        if not anchors:
            continue
        a = anchors[0]
        onclick = a.attrib.get("onclick", "")
        href = a.attrib.get("href", "")
        title = (a.css("::text").get() or "").strip()
        if not href or not title or len(title) < 4:
            continue
        for label, cat_name in _SECTION_LABELS.items():
            if label in onclick and cat_name not in result:
                result[cat_name] = (href, title)
                break
    return result


# ---- 文章详情页 (archive) ----


_DATE_RE = re.compile(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})")


def _find_date(page, search_chars: int = 50000) -> str:
    """从页面文本中提取第一个日期，默认搜索前 50000 字符。"""
    body = page.css("body")
    if not body:
        return ""
    text = body[0].get() or ""
    m = _DATE_RE.search(text[:search_chars])
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return ""


def _parse_archive(page, url: str) -> Article:
    return Article(
        title=_extract_archive_title(page),
        url=url,
        source="药智网",
        published_at=_extract_archive_date(page),
        content=_extract_archive_content(page),
    )


def _extract_archive_date(page) -> str:
    # 从 article_info 区域的 div.fr 中提取日期
    for info in page.css("div.l_article_info"):
        for span in info.css("span"):
            text = (span.css("::text").get() or "").strip()
            if _DATE_RE.match(text):
                return text
    return _find_date(page)


def _extract_archive_title(page) -> str:
    for sel in ["h1.l_title span", "h1.l_title", "h1"]:
        els = page.css(sel)
        if els:
            text = (els[0].css("::text").get() or "").strip()
            if text:
                return text
    return ""


def _extract_archive_content(page) -> str:
    for sel in ["div.article_html_control", "div.article_html"]:
        els = page.css(sel)
        if els:
            texts = [
                t.get().strip() for t in els[0].css("::text") if t.get().strip()
            ]
            if texts:
                return "\n".join(texts)
    return ""


# ---- 会议详情页 ----


def _parse_meeting(page, url: str) -> Article:
    return Article(
        title=_extract_meeting_title(page),
        url=url,
        source="药智网",
        published_at=_extract_meeting_date(page),
        content=_extract_meeting_content(page),
    )


def _extract_meeting_title(page) -> str:
    for sel in ["h1.content_title span", "h1.content_title", "h1"]:
        els = page.css(sel)
        if els:
            text = (els[0].css("::text").get() or "").strip()
            if text:
                return text
    return ""


def _extract_meeting_date(page) -> str:
    for item in page.css("div.content_list"):
        labels = item.css("span.list_fa")
        if labels and "时间" in (labels[0].css("::text").get() or ""):
            ch = item.css("span.list_ch")
            if ch:
                return (ch[0].css("::text").get() or "").strip()
    return _find_date(page)


def _extract_meeting_content(page) -> str:
    for sel in ["div.fl.mainShow", "div.info_content"]:
        els = page.css(sel)
        if els:
            texts = [
                t.get().strip() for t in els[0].css("::text") if t.get().strip()
            ]
            if texts:
                return "\n".join(texts)
    return ""


# ---- 微信公众号文章 ----


def _extract_wechat_title(page) -> str:
    # 微信文章标题在 JS 变量 msg_title 中，h1 文本为空（JS 动态填充）
    body = page.css("body")
    if body:
        text = body[0].get() or ""
        m = re.search(r"var\s+msg_title\s*=\s*'(.*?)'", text)
        if m and m.group(1).strip():
            return m.group(1).strip().replace("&amp;", "&")
    # 备用：从 h1 提取
    for sel in ["h1.rich_media_title", "#activity-name", "h1"]:
        els = page.css(sel)
        if els:
            text = (els[0].css("::text").get() or "").strip()
            if text:
                return text
    return ""


def _parse_wechat(page, url: str) -> Article:
    content = ""
    for sel in ["#js_content", "div.rich_media_content"]:
        els = page.css(sel)
        if els:
            texts = [
                t.get().strip() for t in els[0].css("::text") if t.get().strip()
            ]
            if texts:
                content = "\n".join(texts)
                break
    return Article(
        title=_extract_wechat_title(page) or "（无标题）",
        url=url,
        source="药智网",
        published_at=_find_date(page),
        content=content,
    )
