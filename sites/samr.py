"""
国家市场监督管理局 - 特殊食品安全监督管理司 采集策略

采集范围：4 个板块（各取最新 1 篇）
- 司局动态: /tssps/sjdt/index.html (URL path /sjdt/art/)
- 图片新闻: 同页面 (URL path /sjdt/tpxw/)
- 工作动态: 同页面 (URL path /sjdt/gzdt/)
- 政策文件: /tssps/ 首页 zcwj 链接 (zcwj 列表页为 JS 渲染，无法直接爬取)

实测反爬要点：
- 裸 curl 返回 403，Scrapling impersonate=chrome 直接可用
- 列表选择器: li.gts_contentLeftList01 a[href]，日期 li.gts_contentLeftList01time
- 正文选择器: h1/h2 标题，发布时间 正文提取，#ivs_content / .Custom_UnionStyle
"""

import logging
import re

from app.base_strategy import BaseSiteStrategy
from app.engine import http_get, regex_find_date
from app.models import Article

logger = logging.getLogger(__name__)

BASE = "https://www.samr.gov.cn/tssps"


class SAMRStrategy(BaseSiteStrategy):
    site_name = "国家市场监督管理局"
    site_url = "https://www.samr.gov.cn/tssps/"

    CATEGORIES = {
        "司局动态-司局动态": f"{BASE}/sjdt/index.html",
        "司局动态-图片新闻": f"{BASE}/sjdt/index.html",
        "司局动态-工作动态": f"{BASE}/sjdt/index.html",
        "政策文件": f"{BASE}/",
    }

    def __init__(self, **kwargs):
        if "categories" in kwargs:
            names = kwargs["categories"]
            self.CATEGORIES = {
                k: v for k, v in self.CATEGORIES.items() if k in names
            }

    def fetch_latest(self) -> list[Article]:
        articles = []
        # Deduplicate pages already fetched
        _page_cache: dict[str, object] = {}

        for cat_name, list_url in self.CATEGORIES.items():
            logger.info(f"  板块: {cat_name}")

            if list_url not in _page_cache:
                page = http_get(list_url, retry_delay=3)
                if not page:
                    logger.warning(f"  板块列表页请求失败: {cat_name}")
                    continue
                _page_cache[list_url] = page
            else:
                page = _page_cache[list_url]

            url, title = self._find_latest_link(page, cat_name)
            if url and title:
                url = _resolve_url(url, BASE)
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
        if cat_name == "政策文件":
            return _find_zcwj_latest(page)
        # Map category to URL path fragment
        path_map = {
            "司局动态-司局动态": "/sjdt/art/",
            "司局动态-图片新闻": "/sjdt/tpxw/",
            "司局动态-工作动态": "/sjdt/gzdt/",
        }
        path_frag = path_map.get(cat_name, "")
        seen = set()
        for li in page.css("li.gts_contentLeftList01"):
            link = li.css("a[href]")
            if not link:
                continue
            href = link[0].attrib.get("href", "")
            if path_frag and path_frag not in href:
                continue
            if href in seen:
                continue
            title = (link[0].css("::text").get() or "").strip()
            title = " ".join(title.split())
            if href and title and len(title) >= 4:
                seen.add(href)
                return href, title
        return "", ""

    @staticmethod
    def _extract_title(page) -> str:
        for sel in ["h1", "h2", "#ivs_title", ".article-title"]:
            els = page.css(sel)
            if els:
                text = (els[0].css("::text").get() or "").strip()
                if text:
                    return text.strip()
        # Fallback: title is the text between breadcrumb and 发布时间
        body = page.css("body")
        if body:
            texts = [t.get().strip() for t in body[0].css("::text") if t.get().strip()]
            found_crumb = False
            for t in texts:
                if t in ("司局动态", "工作动态", "图片新闻", "政策文件"):
                    found_crumb = True
                    continue
                if found_crumb and len(t) > 4 and "发布时间" not in t and t not in (">", "首页"):
                    return t
        return ""

    @staticmethod
    def _extract_date(page) -> str:
        meta = page.css('meta[name="PubDate"]')
        if meta:
            content = meta[0].attrib.get("content", "").strip()
            if content:
                return content
        # Fallback: find 发布时间：YYYY-MM-DD in body text
        body = page.css("body")
        if body:
            all_text = " ".join((t.get() or "") for t in body[0].css("::text"))
            m = re.search(r"发布时间[：:]\s*(\d{4}-\d{2}-\d{2})", all_text)
            if m:
                return m.group(1)
        return regex_find_date(page)

    @staticmethod
    def _extract_content(page) -> str:
        for sel in ["#ivs_content", ".Custom_UnionStyle", ".TRS_Editor", "div.con", "div.content"]:
            els = page.css(sel)
            if els:
                texts = [t.get().strip() for t in els[0].css("::text") if t.get().strip()]
                if texts:
                    return "\n".join(texts)
        paras = page.css("p::text")
        return "\n".join(p.get().strip() for p in paras if len(p.get().strip()) > 5)


def _find_zcwj_latest(page) -> tuple[str, str]:
    for a in page.css("a[href]"):
        href = a.attrib.get("href", "")
        if "/zcwj/art/" not in href:
            continue
        title = (a.css("::text").get() or "").strip()
        title = " ".join(title.split())
        if title and len(title) >= 4:
            return href, title
    return "", ""


def _resolve_url(href: str, base: str) -> str:
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return "https://www.samr.gov.cn" + href
    from urllib.parse import urljoin
    return urljoin(base, href)
