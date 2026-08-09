"""DingTalk AI Bitable 异步客户端

推送文章至钉钉 AI 多维表格，复用 LLMClient 模式（httpx.AsyncClient + .env 配置）。

功能：
- AccessToken 内存缓存 + 300s buffer 自动刷新
- 批量推送（每批 20 条）
- 推送失败重试 1 次
- clientToken (UUID v4) 幂等性
- 未配置时静默跳过
"""
import logging
import os
import time
import uuid
from datetime import datetime, timezone, timedelta

import httpx

logger = logging.getLogger(__name__)

BATCH_SIZE = 20
TOKEN_REFRESH_BUFFER = 300  # token 过期前 300s 刷新

# 字段映射：Article 字段 -> 钉钉多维表格列名（D-07）
FIELD_MAPPING = {
    "title": "标题",
    "url": "链接",
    "source": "来源",
    "published_at": "文章发布时间",
    "ai_summary": "AI生成摘要",
    "ai_category": "AI生成分类",
    "ai_score": "AI评分",
}


class DingTalkClient:
    """Async DingTalk AI Bitable client.

    获取 AccessToken 并缓存到内存，过期前 300s 自动刷新。
    push_articles() 批量推送文章到钉钉多维表格。
    未配置时 push_articles() 静默跳过，返回 reason=not_configured。
    """

    def __init__(self):
        self.app_key = os.getenv("DINGTALK_APP_KEY", "")
        self.app_secret = os.getenv("DINGTALK_APP_SECRET", "")
        self.base_id = os.getenv("DINGTALK_BASE_ID", "")
        self.sheet_name = os.getenv("DINGTALK_SHEET_NAME", "")
        self.operator_id = os.getenv("DINGTALK_OPERATOR_ID", "")
        self._client: httpx.AsyncClient | None = None
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

    def _is_configured(self) -> bool:
        """检查必需配置是否存在（app_key + app_secret + base_id）"""
        return bool(self.app_key and self.app_secret and self.base_id)

    async def _get_client(self) -> httpx.AsyncClient:
        """懒加载 httpx.AsyncClient"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url="https://api.dingtalk.com",
                timeout=httpx.Timeout(30.0),
            )
        return self._client

    async def _ensure_token(self) -> str:
        """获取/刷新 AccessToken。缓存 token，过期前 300s 刷新。

        POST /v1.0/oauth2/accessToken
        Body: {"appKey": ..., "appSecret": ...}
        Response: {"expireIn": 7200, "accessToken": "..."}
        """
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token

        client = await self._get_client()
        resp = await client.post(
            "/v1.0/oauth2/accessToken",
            json={"appKey": self.app_key, "appSecret": self.app_secret},
        )
        resp.raise_for_status()
        data = resp.json()

        self._access_token = data["accessToken"]
        expire_in = data.get("expireIn", 7200)
        self._token_expires_at = time.time() + expire_in - TOKEN_REFRESH_BUFFER

        logger.debug(f"DingTalk token refreshed, expires in {expire_in}s")
        return self._access_token

    async def push_articles(self, articles: list[dict]) -> dict:
        """批量推送文章到钉钉 AI 多维表格。

        每批 20 条，每批重试 2 次，clientToken 幂等。
        未配置时静默跳过。

        Args:
            articles: 文章列表，每篇包含 title, url, source, ai_summary, ai_category, ai_score

        Returns:
            {"pushed": int, "failed": int} 或 {"pushed": 0, "failed": 0, "reason": "not_configured"}
        """
        if not self._is_configured():
            logger.info("DingTalk not configured, skipping push")
            return {"pushed": 0, "failed": 0, "reason": "not_configured"}

        if not articles:
            return {"pushed": 0, "failed": 0}

        # 确保获取 token
        await self._ensure_token()

        pushed = 0
        failed = 0

        # 分批推送
        for batch_start in range(0, len(articles), BATCH_SIZE):
            batch = articles[batch_start:batch_start + BATCH_SIZE]
            records = self._build_records(batch)
            client_token = str(uuid.uuid4())

            success = False
            for attempt in range(2):
                try:
                    client = await self._get_client()
                    resp = await client.post(
                        f"/v1.0/notable/bases/{self.base_id}/sheets/{self.sheet_name}/records",
                        json={"records": records},
                        params={
                            "operatorId": self.operator_id,
                            "clientToken": client_token,
                        },
                        headers={
                            "x-acs-dingtalk-access-token": self._access_token,
                        },
                    )
                    resp.raise_for_status()
                    pushed += len(batch)
                    success = True
                    logger.info(
                        f"DingTalk push batch succeeded: {len(batch)} articles "
                        f"(batch {batch_start // BATCH_SIZE + 1})"
                    )
                    break
                except Exception as e:
                    logger.error(
                        f"DingTalk push error (attempt {attempt + 1}/2, "
                        f"batch {batch_start // BATCH_SIZE + 1}): {e}"
                    )

            if not success:
                failed += len(batch)

        return {"pushed": pushed, "failed": failed}

    def _build_records(self, articles: list[dict]) -> list[dict]:
        """构建钉钉 records payload，使用中文键名映射（D-07）"""
        now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
        records = []
        for article in articles:
            fields = {}
            for src_key, dst_key in FIELD_MAPPING.items():
                value = article.get(src_key)
                if value is not None:
                    if src_key == "url":
                        fields[dst_key] = {"link": value, "text": value}
                    else:
                        fields[dst_key] = value
            fields["文章推送时间"] = now
            records.append({"fields": fields})
        return records

    async def close(self) -> None:
        """关闭 httpx client"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
