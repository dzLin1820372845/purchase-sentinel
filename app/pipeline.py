"""处理管道协调器 — 去重 -> 关键词匹配 -> AI分析 -> 入库"""
import logging

from app.database import (
    article_exists,
    insert_article,
    insert_failure_record,
    mark_articles_dingtalk_sent,
)
from app.llm_client import LLMClient
from app.dingtalk_client import DingTalkClient
from app.keyword_matcher import KeywordMatcher
from app.models import Article

logger = logging.getLogger(__name__)


class PipelineCoordinator:
    """编排文章处理流程: 去重 -> 关键词匹配 -> AI分析 -> 入库.

    逐篇处理，每篇文章独立走完完整管道。
    一篇文章某步骤失败不影响其他文章（错误隔离）。
    LLM 失败时文章照常入库，默认值 score=0/category=None/summary=None。
    """

    def __init__(self, matcher: KeywordMatcher, llm: LLMClient, dingtalk: DingTalkClient):
        self.matcher = matcher
        self.llm = llm
        self.dingtalk = dingtalk

    async def process_articles(self, articles: list[Article]) -> dict:
        """处理所有文章，返回统计摘要。"""
        stats = {
            "total": len(articles),
            "duplicates": 0,
            "processed": 0,
            "failed": 0,
        }

        for article in articles:
            try:
                result = await self._process_one(article)
                if result == "duplicate":
                    stats["duplicates"] += 1
                else:
                    stats["processed"] += 1
            except Exception as e:
                stats["failed"] += 1
                logger.error(f"Pipeline error for '{article.title[:30]}': {e}")
                try:
                    await insert_failure_record(0, "storage", str(e))
                except Exception:
                    pass

        # Step 5: 批量推送本次关键词命中的文章（D-02, D-03）
        try:
            matched = [a for a in articles if a.matched_keywords]
            pushed = 0
            failed = 0

            if matched:
                push_data = [
                    {
                        "url_hash": a.url_hash,
                        "title": a.title,
                        "url": a.url,
                        "source": a.source,
                        "ai_summary": a.ai_summary,
                        "ai_category": a.ai_category,
                        "ai_score": a.ai_score,
                        "published_at": a.published_at,
                    }
                    for a in matched
                ]
                result = await self.dingtalk.push_articles(push_data)
                pushed = result.get("pushed", 0)
                failed = result.get("failed", 0)

                if pushed > 0:
                    hashes = [a.url_hash for a in matched]
                    await mark_articles_dingtalk_sent(hashes)

                if failed > 0:
                    await insert_failure_record(0, "dingtalk", f"Batch push failed: {failed} articles")

            stats["dingtalk_pushed"] = pushed
            stats["dingtalk_failed"] = failed
        except Exception as e:
            logger.error(f"DingTalk batch push error (non-fatal): {e}")
            stats["dingtalk_error"] = str(e)

        return stats

    async def _process_one(self, article: Article) -> str:
        """处理单篇文章。返回 'duplicate' 或 'done'。"""
        # Step 1: 去重检查
        if await article_exists(article.url_hash):
            logger.debug(f"Duplicate skipped: {article.title[:50]}")
            return "duplicate"

        # Step 1.5: 内容完整性检查
        if not article.title or not article.content:
            reason = f"Missing {'title' if not article.title else 'content'}"
            logger.warning(f"Skipping article with {reason}: {article.url}")
            article_id = await insert_article(article)
            if article_id:
                await insert_failure_record(article_id, "scrape", reason)
            return "done"

        # Step 2: 关键词匹配
        text = f"{article.title} {article.content or ''}"
        article.matched_keywords = self.matcher.match(text)

        # Step 3: AI 分析（仅匹配到关键词时）
        if article.matched_keywords:
            kw_str = ",".join(kw["keyword"] for kw in article.matched_keywords)
            result, error = await self.llm.analyze(
                article.title, article.content or "", kw_str
            )
            if result:
                article.ai_summary = result["summary"]
                article.ai_category = result["category"]
                article.ai_score = result["score"]
            else:
                article.error_msg = error
                logger.warning(f"LLM analysis failed for: {article.title[:50]}")

        # Step 4: 入库
        article_id = await insert_article(article)

        # LLM 失败时记录到 processing_failures
        if article.error_msg and article.matched_keywords and article_id:
            await insert_failure_record(
                article_id, "llm", article.error_msg
            )

        return "done"
