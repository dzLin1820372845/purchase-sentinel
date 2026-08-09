"""LLM 异步客户端：通过 httpx 直接调用 chat/completions 兼容接口。

替换原 DifyClient 的 Dify 工作流调用。输入契约 (title, content, keywords)
与输出字段 {summary, category, score} 保持不变；analyze 改为返回
(result, error) 元组，以便 pipeline 把失败详情写入 processing_failures。

沿用 DifyClient 已验证策略：httpx.AsyncClient 懒加载、Semaphore(3)、
2 次重试、content 截断前 1500 字、30s 超时。
"""
import asyncio
import json
import logging
import os
import re

import httpx

logger = logging.getLogger(__name__)


# System prompt（逐字迁移自 Dify 工作流 YAML 的 system 节点，
# 已去除 summary 描述末尾悬空的「，并提取」编辑残缺片段）。
SYSTEM_PROMPT = '''你是一名采购合规分析师，负责分析大健康行业（食品、药品、化妆品、医疗器械、宠物）的监管动态文章。

请对以下文章进行分析，严格按JSON格式输出，不要输出其他内容。

## 分析任务
**文章标题**
**命中关键词**
**文章内容**


## 输出格式

请严格输出以下JSON，不要有任何额外文字：
{
  "summary": "中文摘要，聚焦监管动态、政策变化或违规处罚等核心信息",
  "category": "食品 或 化妆品 或 药品 或 医疗器械 或 综合（只能选一个）",
  "score": "1到5的整数，评分标准如下：
    5分：重大处罚/召回/禁令，直接影响采购合规
    4分：新法规/政策出台，需要关注和跟进
    3分：行业动态/标准更新，有参考价值
    2分：一般行业新闻，价值有限
    1分：与采购合规关联度低"
}'''


# User prompt 模板（{title}/{keywords}/{content} 由 analyze 填充）。
USER_TEMPLATE = '''**文章标题：** {title}

**命中关键词：** {keywords}

**文章内容：** {content}'''


def _safe_int(value) -> int:
    """score 容错：非数字 / 缺失 / None 一律回落到 0。"""
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0


class LLMClient:
    """Async chat/completions client.

    POST {LLM_BASE_URL}/chat/completions
    Header: Authorization: Bearer <LLM_API_KEY>
    Timeout 30s, retry 2 times, Semaphore(3)
    Content truncated to first 1500 chars
    analyze() 返回 (result, error) 元组。
    """

    def __init__(self):
        self.base_url = os.getenv("LLM_BASE_URL", "https://www.lordfine.top/v1")
        self.api_key = os.getenv("LLM_API_KEY", "")
        self.model = os.getenv("LLM_MODEL", "deepseek-v4-pro")
        self._semaphore = asyncio.Semaphore(3)
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """懒加载 httpx.AsyncClient（Bearer header + 30s timeout）。"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(30.0),
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        return self._client

    def _parse_json(self, raw: str) -> tuple[dict | None, str | None]:
        """三级兜底把 LLM 返回的 content 解析为 JSON dict。

        1. 直接 json.loads
        2. 剥离 markdown 代码块围栏（```json / ```）后 json.loads
        3. 正则 re.search(r"\\{.*\\}", raw, re.DOTALL) 提取首个 JSON 对象后 json.loads
        全部失败返回 (None, "parse_failed")。
        """
        if not raw:
            return None, "parse_failed"

        # Level 1: 直接解析
        try:
            result = json.loads(raw.strip())
            if isinstance(result, dict):
                return result, None
        except (json.JSONDecodeError, ValueError):
            pass

        # Level 2: 剥离 markdown 代码块围栏
        stripped = re.sub(r"```(?:json)?", "", raw).strip()
        try:
            result = json.loads(stripped)
            if isinstance(result, dict):
                return result, None
        except (json.JSONDecodeError, ValueError):
            pass

        # Level 3: 正则提取首个 {...} 对象
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                result = json.loads(m.group(0))
                if isinstance(result, dict):
                    return result, None
            except (json.JSONDecodeError, ValueError):
                pass

        return None, "parse_failed"

    async def analyze(
        self, title: str, content: str, keywords: str
    ) -> tuple[dict | None, str | None]:
        """调用 chat/completions 生成摘要/分类/评分。

        成功返回 ({"summary", "category", "score": int}, None)；
        全部重试失败返回 (None, error)，error 形如
        "LLM request failed: <异常摘要>" 或
        "LLM analysis failed: raw=<原始 content 截断前 500 字>"。
        """
        async with self._semaphore:
            user_content = USER_TEMPLATE.format(
                title=title,
                keywords=keywords,
                content=(content or "")[:1500],
            )
            payload = {
                "model": self.model,
                "temperature": 0.7,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
            }

            last_raw: str | None = None
            last_error: str | None = None

            for attempt in range(2):
                try:
                    client = await self._get_client()
                    resp = await client.post("/chat/completions", json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                    raw = data["choices"][0]["message"]["content"]
                    outputs, perr = self._parse_json(raw)
                    if perr is None:
                        return (
                            {
                                "summary": outputs.get("summary"),
                                "category": outputs.get("category"),
                                "score": _safe_int(outputs.get("score", 0)),
                            },
                            None,
                        )
                    # 解析失败 → 计入重试（与网络异常同等处理）
                    last_raw = raw
                    last_error = None
                    logger.warning(
                        f"LLM JSON parse failed (attempt {attempt+1}/2): {perr}, raw={raw[:200]}"
                    )
                except Exception as e:
                    last_error = str(e)
                    last_raw = None
                    logger.error(f"LLM API error (attempt {attempt+1}/2): {e}")

            # 全部 attempt 失败：按最后一次失败性质组装 error
            if last_raw is not None:
                error = f"LLM analysis failed: raw={last_raw[:500]}"
            else:
                error = f"LLM request failed: {last_error}"
            return (None, error)

    async def close(self) -> None:
        """释放 httpx client。"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
