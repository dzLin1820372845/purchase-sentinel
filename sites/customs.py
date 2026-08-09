"""
海关总署 (Customs) 采集策略

采集范围：www.customs.gov.cn 新闻发布 7 个栏目 + 政务公开 3 个栏目（各取最新 1 篇）
- 新闻发布: 国务院要闻(gov.cn)、要闻聚焦、今日海关、媒体聚焦、重要会议、新闻发布会、各关新闻
- 政务公开: 海关法规、最新文件、政策解读

实测反爬要点：
- 海关总署使用瑞数 (RuiShu) 反爬，标准 HTTP 客户端返回 412 + 加密 JS 挑战
- 必须使用 Scrapling StealthyFetcher（无头浏览器模式）绕过瑞数反爬
- StealthyFetcher 默认设 Referer 为 google.com 导致 412，必须设 google_search=False
- 国务院要闻栏目链接到 www.gov.cn，跨域采集，_resolve_url 需正确处理
- 列表页结构: ul.news_list li，内含 a 标签（href + 文本为标题）+ span 文本为日期
- 详情页结构: meta[name="ArticleTitle"] / meta[name="PubDate"] + div.article_detail 内 p 标签
- StealthyFetcher.fetch() 启动浏览器较慢，但每栏目仅取 1 篇（共 10+10=20 次请求），可接受
"""

import logging

from app.base_strategy import BaseSiteStrategy
from app.engine import regex_find_date
from app.models import Article
from scrapling.fetchers import StealthyFetcher

logger = logging.getLogger(__name__)

BASE = "http://www.customs.gov.cn"


class CustomsStrategy(BaseSiteStrategy):
    """海关总署采集策略。

    使用 StealthyFetcher（无头浏览器）绕过瑞数反爬。
    采集 10 个栏目各取最新 1 篇文章。
    """

    site_name = "海关总署"
    site_url = BASE

    CATEGORIES = {
        "新闻发布-国务院要闻": "https://www.gov.cn/yaowen/liebiao/",
        "新闻发布-要闻聚焦": f"{BASE}/customs/xwfb34/ywjj/index.html",
        "新闻发布-今日海关": f"{BASE}/customs/xwfb34/302425/index.html",
        "新闻发布-媒体聚焦": f"{BASE}/customs/xwfb34/mtjj35/index.html",
        "新闻发布-重要会议": f"{BASE}/customs/xwfb34/3128008/index.html",
        "新闻发布-新闻发布会": f"{BASE}/customs/xwfb34/302330/index.html",
        "新闻发布-各关新闻": f"{BASE}/customs/xwfb34/302263/index.html",
        "政务公开-海关法规": f"{BASE}/customs/302249/302266/index.html",
        "政务公开-最新文件": f"{BASE}/customs/302249/2480148/index.html",
        "政务公开-政策解读": f"{BASE}/customs/302249/302272/index.html",
    }

    def __init__(self, **kwargs):
        if "categories" in kwargs:
            names = kwargs["categories"]
            self.CATEGORIES = {
                k: v for k, v in self.CATEGORIES.items() if k in names
            }

    def fetch_latest(self) -> list[Article]:
        """遍历 CATEGORIES，用 StealthyFetcher 请求列表页，取每个栏目最新 1 篇。"""
        articles = []
        for cat_name, list_url in self.CATEGORIES.items():
            logger.info(f"  板块: {cat_name}")
            try:
                page = StealthyFetcher.fetch(
                    list_url, headless=True,
                    google_search=False, network_idle=True, wait=3,
                )
            except Exception as e:
                logger.warning(f"  板块列表页请求失败: {cat_name} ({e})")
                continue

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
        """用 StealthyFetcher 请求详情页，提取标题、日期、正文。"""
        try:
            page = StealthyFetcher.fetch(
                url, headless=True,
                google_search=False, network_idle=True, wait=3,
            )
        except Exception as e:
            logger.warning(f"  详情页请求失败: {url} ({e})")
            return None

        if not page:
            return None

        return Article(
            title=self._extract_title(page) or "(无标题)",
            url=url,
            source=self.site_name,
            published_at=self._extract_date(page),
            content=self._extract_content(page),
        )

    @staticmethod
    def _find_latest_link(page, base_url: str) -> tuple[str, str]:
        """从列表页 ul.news_list li 中找到第一个文章链接。

        解析结构：
        - li 内 a 标签的 href 为文章链接、文本为标题
        - li 内 span 标签文本为发布日期 (YYYY-MM-DD)
        """
        items = page.css("ul.news_list li")
        if not items:
            # fallback: 扫描所有链接，匹配与 base_url 同域的文章链接
            for link in page.css("a[href]"):
                href = link.attrib.get("href", "")
                if not href:
                    continue
                # 跳过锚点、javascript 等非文章链接
                if href.startswith(("#", "javascript")):
                    continue
                title = link.attrib.get("title", "") or link.css("::text").get() or ""
                title = title.strip()
                if not title or len(title) < 4:
                    continue
                resolved = _resolve_url(href, base_url)
                return resolved, title
            return "", ""

        for item in items:
            links = item.css("a")
            if not links:
                continue
            href = links[0].attrib.get("href", "")
            if not href:
                continue
            title = links[0].css("::text").get() or links[0].attrib.get("title", "") or ""
            title = title.strip()
            if not title or len(title) < 4:
                continue
            return _resolve_url(href, base_url), title

        return "", ""

    @staticmethod
    def _extract_title(page) -> str:
        """提取文章标题：优先 meta[name="ArticleTitle"]，fallback div.article_detail 前的 h1/h2。"""
        meta = page.css('meta[name="ArticleTitle"]')
        if meta:
            content = meta[0].attrib.get("content", "")
            title = content.replace("<br>", "").replace("<br/>", "").strip()
            if title:
                return title
        # fallback: div.article_detail 区域前的标题标签
        for sel in ["h1", "h2", ".article-title"]:
            els = page.css(sel)
            if els:
                texts = [t.get().strip() for t in els[0].css("::text") if t.get().strip()]
                if texts:
                    return "".join(texts)
        return ""

    @staticmethod
    def _extract_date(page) -> str:
        """提取发布日期：优先 meta[name="PubDate"]，fallback .pubtime / .article-date。"""
        meta = page.css('meta[name="PubDate"]')
        if meta:
            content = meta[0].attrib.get("content", "")
            if content.strip():
                return content.strip()
        # fallback selectors
        for sel in [".pubtime", ".article-date", "span[class*='date']"]:
            els = page.css(sel)
            if els:
                texts = [t.get().strip() for t in els[0].css("::text") if t.get().strip()]
                if texts:
                    return "".join(texts)
        return regex_find_date(page)

    @staticmethod
    def _extract_content(page) -> str:
        """提取正文：div.article_detail 内 p 标签文本，fallback .content / #ivs_content。"""
        for sel in ["div.article_detail", ".content", "#ivs_content"]:
            els = page.css(sel)
            if els:
                texts = [t.get().strip() for t in els[0].css("::text") if t.get().strip()]
                if texts:
                    return "\n".join(texts)
        paras = page.css("p::text")
        return "\n".join(p.get().strip() for p in paras if len(p.get().strip()) > 5)


def _resolve_url(href: str, base_url: str) -> str:
    """将相对/绝对路径的 href 解析为完整 URL。"""
    if href.startswith("http"):
        return href
    from urllib.parse import urljoin
    return urljoin(base_url, href)
