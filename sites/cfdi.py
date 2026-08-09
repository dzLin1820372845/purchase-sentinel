"""
国家食品药品审核查验中心 (CFDI) 采集策略

采集范围：www.cfdi.org.cn 10 个板块（各取最新 1 篇）
- 最新消息、通知公告、工作动态
- 政策法规: 药品、医疗器械、化妆品
- 检查专栏: 药品检查、医疗器械检查、化妆品检查、境外检查

实测反爬要点：
- stealthy_headers=True 会设 Referer 为 google.com 导致 403，需显式设 Referer 为 CFDI 站内地址
- 需 Accept-Language 请求头（http_get DEFAULT_HEADERS 已包含）
- 列表页: div.page-lst-box ul li，链接 a.title，标题 p.content，日期 p.datatime
- 详情页: 标题 div.news-title，日期 div.news-pub-time（需去"发布时间："前缀），正文 div.news-con
"""

import logging

from app.base_strategy import BaseSiteStrategy
from app.engine import http_get, regex_find_date
from app.models import Article

logger = logging.getLogger(__name__)

BASE = "https://www.cfdi.org.cn/cfdi"

# stealthy_headers=True 会设 Referer 为 google.com，CFDI 拒绝此头返回 403
CFDI_HEADERS = {"Referer": f"{BASE}/"}


class CFDIStrategy(BaseSiteStrategy):
    site_name = "国家食品药品审核查验中心"
    site_url = "https://www.cfdi.org.cn/cfdi/"

    CATEGORIES = {
        "最新消息": f"{BASE}/index?module=A001&nty=C01",
        "通知公告": f"{BASE}/index?module=A001&nty=D06",
        "工作动态": f"{BASE}/index?module=A001&m1=10&m2=&nty=D16&tcode=D16B002",
        "政策法规-药品政策法规": f"{BASE}/index?module=A001&m1=11&m2=&nty=C03&tcode=C03B014",
        "政策法规-医疗器械政策法规": f"{BASE}/index?module=A001&m1=11&m2=&nty=C03&tcode=C03B015",
        "政策法规-化妆品政策法规": f"{BASE}/index?module=A001&m1=11&m2=&nty=C03&tcode=C03B016",
        "检查专栏-药品检查": f"{BASE}/index?module=A001&nty=A27",
        "检查专栏-医疗器械检查": f"{BASE}/index?module=A001&nty=A25",
        "检查专栏-化妆品检查": f"{BASE}/index?module=A001&nty=A26",
        "检查专栏-境外检查": f"{BASE}/index?module=A001&nty=A14",
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
            page = http_get(list_url, headers=CFDI_HEADERS, retry_delay=3)
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
        page = http_get(url, headers=CFDI_HEADERS, retry_delay=3)
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
        """从列表页 div.page-lst-box ul li 中找到第一个文章链接。"""
        items = page.css("div.page-lst-box ul li")
        for item in items:
            link = item.css("a.title")
            if not link:
                continue
            href = link[0].attrib.get("href", "")
            if not href:
                continue
            # 标题在 p.content
            title_el = link[0].css("p.content")
            if not title_el:
                # fallback: 取 a.title 的文本
                title = link[0].css("::text").get("") or ""
            else:
                title = title_el[0].css("::text").get("") or ""
            title = title.strip()
            if not title or len(title) < 4:
                continue
            return _resolve_url(href, base_url), title

        # fallback: scan all links matching CFDI article pattern
        for link in page.css("a[href]"):
            href = link.attrib.get("href", "")
            if not href.startswith("https://www.cfdi.org.cn"):
                continue
            if "/resource/news/" not in href:
                continue
            title = link.css("::text").get() or link.attrib.get("title", "")
            title = title.strip()
            if not title or len(title) < 4:
                continue
            return _resolve_url(href, base_url), title

        return "", ""

    @staticmethod
    def _extract_title(page) -> str:
        # 优先从 meta 标签取完整标题（div.news-title 含 <br> 会截断）
        meta = page.css('meta[name="ArticleTitle"]')
        if meta:
            content = meta[0].attrib.get("content", "")
            # meta content 中可能含字面量 <br>，去掉
            title = content.replace("<br>", "").replace("<br/>", "").strip()
            if title:
                return title
        # fallback: 拼接所有文本节点
        for sel in ["div.news-title", "h1", ".article-title"]:
            els = page.css(sel)
            if els:
                texts = [t.get().strip() for t in els[0].css("::text") if t.get().strip()]
                if texts:
                    return "".join(texts)
        return ""

    @staticmethod
    def _extract_date(page) -> str:
        els = page.css("div.news-pub-time")
        if els:
            # div 内含 <span>发布时间：</span> + 日期文本，需拼接所有文本节点
            texts = [t.get().strip() for t in els[0].css("::text") if t.get().strip()]
            text = "".join(texts)
            if text.startswith("发布时间："):
                text = text[len("发布时间："):]
            if text.strip():
                return text.strip()
        # fallback selectors
        for sel in [".article-date", ".pubtime", "span[class*='date']"]:
            els = page.css(sel)
            if els:
                texts = [t.get().strip() for t in els[0].css("::text") if t.get().strip()]
                if texts:
                    return "".join(texts)
        return regex_find_date(page)

    @staticmethod
    def _extract_content(page) -> str:
        for sel in ["div.news-con", ".content", "#ivs_content"]:
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
