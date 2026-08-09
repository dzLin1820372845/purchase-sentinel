"""采集引擎 — 公共 HTTP 请求和策略调度"""
import logging
import re
import time
from typing import TYPE_CHECKING

from scrapling.fetchers import Fetcher

from app.models import Article

if TYPE_CHECKING:
    from app.base_strategy import BaseSiteStrategy

logger = logging.getLogger(__name__)

# 默认请求头 — 所有策略共享（Accept 对齐 Chrome 真实值，避免被指纹检测）
DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,image/apng,*/*;q=0.8,"
              "application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def http_get(
    url: str,
    *,
    headers: dict | None = None,
    impersonate: str = "chrome",
    retry: int = 2,
    retry_delay: float = 5.0,
    timeout: int = 30,
) -> "scrapling.Response | None":
    """公共 GET 请求封装。

    职责：默认请求头合并、统一日志、应用层重试。
    Scrapling Fetcher.get() 设置 retries=1 禁用其内置重试，由本函数的应用层循环统一控制。

    Args:
        url: 请求 URL
        headers: 自定义请求头，合并到 DEFAULT_HEADERS（同名覆盖）
        impersonate: 浏览器指纹（默认 chrome）
        retry: 应用层最大重试次数（0=不重试，只试一次）
        retry_delay: 重试间隔秒数（默认 5.0，满足 NMPA >= 5秒要求）
        timeout: 请求超时秒数
    """
    merged = {**DEFAULT_HEADERS, **(headers or {})}

    for attempt in range(retry):
        if attempt > 0:
            logger.info(f"重试 {attempt}/{retry}，等待 {retry_delay}s -> {url}")
            time.sleep(retry_delay)

        try:
            page = Fetcher.get(
                url,
                stealthy_headers=True,
                impersonate=impersonate,
                headers=merged,
                timeout=timeout,
                retries=1,  # 禁用 Scrapling 内置重试，由本函数应用层循环统一控制
            )
            if page.status == 200:
                return page
            logger.warning(f"HTTP {page.status} <- {url}")
        except Exception as e:
            logger.error(f"请求异常: {e} <- {url}")

    return None


_DATE_PATTERN = re.compile(
    r'(20\d{2})[-年./](\d{1,2})[-月./](\d{1,2})日?'
)


def regex_find_date(page) -> str | None:
    """正则兜底：从页面文本中提取第一个日期。"""
    body = page.css("body")
    if not body:
        return None
    text = body[0].get() or ""
    m = _DATE_PATTERN.search(text[:3000])
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return None


class ScraperEngine:
    """采集引擎调度器 — 加载策略，执行抓取，返回标准化结果。

    职责：调度策略执行、频率控制、错误隔离。
    不关心具体解析逻辑（策略负责），不关心存储（下游管道负责）。
    """

    def __init__(self, delay: float = 3.0):
        """Args:
            delay: fetch_article 调用之间的间隔秒数（默认 3.0）
        """
        self.delay = delay

    def run(self, strategy: "BaseSiteStrategy") -> list[Article]:
        """执行单个策略的完整采集流程。"""
        logger.info(f"开始采集: {strategy.site_name}")

        if not strategy.site_name:
            logger.warning(f"策略 site_name 为空，跳过: {strategy.__class__.__name__}")
            return []

        articles = []

        # 第一步：获取最新文章列表
        try:
            latest = strategy.fetch_latest()
            logger.info(f"  发现 {len(latest)} 篇最新文章")
        except Exception as e:
            logger.error(f"  获取列表失败: {e}")
            return articles

        # 第二步：逐篇获取详情（列表中 content 为空的）
        for art in latest:
            if art.content:
                # 列表页已包含正文，无需额外请求
                articles.append(art)
                continue

            try:
                detail = strategy.fetch_article(art.url)
                if detail:
                    detail.source_category = art.source_category
                    articles.append(detail)
                else:
                    # 详情获取失败，保留列表页信息（不丢失文章）
                    articles.append(art)
            except Exception as e:
                logger.warning(f"  详情获取失败: {art.title[:30]} -> {e}")
                articles.append(art)

            time.sleep(self.delay)

        logger.info(f"采集完成: {strategy.site_name} -> {len(articles)} 篇")
        return articles

    def run_all(self, strategies: list["BaseSiteStrategy"]) -> list[Article]:
        """执行多个策略，收集所有文章。一个策略失败不影响其他策略。"""
        all_articles = []
        for strategy in strategies:
            try:
                articles = self.run(strategy)
                all_articles.extend(articles)
            except Exception as e:
                logger.error(f"策略执行异常: {strategy.__class__.__name__} -> {e}")
        return all_articles
