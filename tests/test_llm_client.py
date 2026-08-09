"""LLMClient 异步 HTTP 客户端单元测试（mocked httpx）。

对照原 DifyClient 测试用例结构改写为 chat completion 响应形态：
- SUCCESS_PAYLOAD 不再有 {data:{status,outputs}} 包裹
- _make_chat_response 构造 choices[0].message.content 形态
- 覆盖成功 / 重试 / 两种失败 / score 转 int / 截断 / payload+headers /
  两种 JSON 健壮性 / close / 并发
"""
import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# 测试内强制确定性 env（在构造 LLMClient 前注入，避免读到真实 .env）
os.environ["LLM_API_KEY"] = "test-key"
os.environ["LLM_BASE_URL"] = "https://test-llm.example/v1"
os.environ["LLM_MODEL"] = "test-model"

from app.llm_client import LLMClient, SYSTEM_PROMPT, USER_TEMPLATE  # noqa: E402


def _make_chat_response(content_str: str, status_code: int = 200):
    """构造 chat completion 形态的 mock httpx.Response。"""
    body = {"choices": [{"message": {"content": content_str}}]}
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = body
    resp.raise_for_status = MagicMock()
    return resp


def _success_content(**overrides) -> str:
    """返回成功场景下的 message.content（合法 JSON 字符串）。"""
    payload = {"summary": "测试摘要", "category": "药品", "score": "4"}
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


class TestLLMClient:

    async def test_analyze_success(self):
        """成功响应返回 (dict, None) 且 score 已转 int。"""
        client = LLMClient()
        mock_resp = _make_chat_response(_success_content())

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=mock_resp)
            mock_get.return_value = mock_http

            result, error = await client.analyze("测试标题", "测试内容", "处罚")
            assert error is None
            assert result == {"summary": "测试摘要", "category": "药品", "score": 4}
        await client.close()

    async def test_analyze_retry_success_on_second(self):
        """第一次异常、第二次成功（2 次重试内恢复）。"""
        client = LLMClient()
        mock_resp_ok = _make_chat_response(_success_content())

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(
                side_effect=[httpx.HTTPError("boom"), mock_resp_ok]
            )
            mock_get.return_value = mock_http

            result, error = await client.analyze("测试标题", "测试内容", "处罚")
            assert error is None
            assert result == {"summary": "测试摘要", "category": "药品", "score": 4}
        await client.close()

    async def test_analyze_all_attempts_network_fail(self):
        """两次都网络异常 → (None, error)，error 以 LLM request failed 开头。"""
        client = LLMClient()
        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(side_effect=httpx.HTTPError("boom"))
            mock_get.return_value = mock_http

            result, error = await client.analyze("测试标题", "测试内容", "处罚")
            assert result is None
            assert error is not None
            assert error.startswith("LLM request failed")
            assert "boom" in error
        await client.close()

    async def test_analyze_all_attempts_parse_fail(self):
        """两次都解析失败 → (None, error)，error 以 LLM analysis failed 开头且含 raw=。"""
        client = LLMClient()
        mock_resp = _make_chat_response("not json at all {{{")

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=mock_resp)
            mock_get.return_value = mock_http

            result, error = await client.analyze("测试标题", "测试内容", "处罚")
            assert result is None
            assert error is not None
            assert error.startswith("LLM analysis failed")
            assert "raw=" in error
        await client.close()

    async def test_score_string_to_int(self):
        """score 字符串 '3' 转为 int 3。"""
        client = LLMClient()
        mock_resp = _make_chat_response(
            json.dumps({"summary": "s", "category": "c", "score": "3"},
                       ensure_ascii=False)
        )

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=mock_resp)
            mock_get.return_value = mock_http

            result, error = await client.analyze("t", "c", "k")
            assert error is None
            assert result["score"] == 3
        await client.close()

    async def test_score_non_numeric_fallback(self):
        """score='abc' 回落为 0。"""
        client = LLMClient()
        mock_resp = _make_chat_response(
            json.dumps({"summary": "s", "category": "c", "score": "abc"},
                       ensure_ascii=False)
        )

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=mock_resp)
            mock_get.return_value = mock_http

            result, error = await client.analyze("t", "c", "k")
            assert error is None
            assert result["score"] == 0
        await client.close()

    async def test_content_truncated_to_1500(self):
        """content 长 2000 → payload user content 内正文恰为 1500 字。"""
        client = LLMClient()
        long_content = "x" * 2000
        mock_resp = _make_chat_response(_success_content())

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=mock_resp)
            mock_get.return_value = mock_http

            await client.analyze("t", long_content, "k")

            payload = mock_http.post.call_args.kwargs["json"]
            user_content = payload["messages"][1]["content"]
            assert "x" * 1500 in user_content
            assert "x" * 1501 not in user_content
        await client.close()

    async def test_correct_payload_and_headers(self):
        """payload 结构与 POST 路径正确。"""
        client = LLMClient()
        mock_resp = _make_chat_response(_success_content())

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=mock_resp)
            mock_get.return_value = mock_http

            await client.analyze("标题", "内容", "关键词")

            call = mock_http.post.call_args
            assert call.args[0] == "/chat/completions"
            payload = call.kwargs["json"]
            assert payload["model"] == "test-model"
            assert payload["temperature"] == 0.7
            assert payload["response_format"] == {"type": "json_object"}
            assert len(payload["messages"]) == 2
            assert payload["messages"][0]["role"] == "system"
            assert payload["messages"][1]["role"] == "user"
            assert payload["messages"][0]["content"] == SYSTEM_PROMPT
            user_text = payload["messages"][1]["content"]
            assert "标题" in user_text
            assert "关键词" in user_text
            assert "内容" in user_text
        await client.close()

    async def test_authorization_header_injected(self):
        """_get_client 创建 httpx.AsyncClient 时注入 Authorization: Bearer。"""
        with patch("app.llm_client.httpx.AsyncClient") as mock_client_cls:
            mock_http = AsyncMock()
            mock_http.is_closed = False
            mock_http.post = AsyncMock(return_value=_make_chat_response(_success_content()))
            mock_client_cls.return_value = mock_http

            client = LLMClient()
            await client.analyze("t", "c", "k")

            construct = mock_client_cls.call_args
            assert construct.kwargs["base_url"] == "https://test-llm.example/v1"
            assert construct.kwargs["headers"]["Authorization"] == "Bearer test-key"
            # timeout 为 httpx.Timeout(30.0) 实例
            assert isinstance(construct.kwargs["timeout"], httpx.Timeout)
            assert construct.kwargs["timeout"].read == 30.0
        await client.close()

    async def test_json_robustness_markdown_codeblock(self):
        """响应带 ```json 代码块围栏时仍能解析。"""
        client = LLMClient()
        content = "```json\n" + json.dumps(
            {"summary": "s", "category": "c", "score": 2}, ensure_ascii=False
        ) + "\n```"
        mock_resp = _make_chat_response(content)

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=mock_resp)
            mock_get.return_value = mock_http

            result, error = await client.analyze("t", "c", "k")
            assert error is None
            assert result == {"summary": "s", "category": "c", "score": 2}
        await client.close()

    async def test_json_robustness_extra_text(self):
        """响应前后带额外文字时仍能靠正则兜底解析。"""
        client = LLMClient()
        inner = json.dumps(
            {"summary": "s", "category": "c", "score": 2}, ensure_ascii=False
        )
        content = f"好的，这是结果：\n{inner}\n以上。"
        mock_resp = _make_chat_response(content)

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=mock_resp)
            mock_get.return_value = mock_http

            result, error = await client.analyze("t", "c", "k")
            assert error is None
            assert result == {"summary": "s", "category": "c", "score": 2}
        await client.close()

    async def test_close_client(self):
        """close() 调用 aclose。"""
        client = LLMClient()
        mock_http = AsyncMock()
        mock_http.is_closed = False
        mock_http.aclose = AsyncMock()
        client._client = mock_http

        await client.close()
        mock_http.aclose.assert_awaited_once()

    async def test_concurrent_calls(self):
        """并发 3 次 analyze 全部返回非 None result（Semaphore(3) 不阻塞）。"""
        client = LLMClient()
        mock_resp = _make_chat_response(_success_content())

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=mock_resp)
            mock_get.return_value = mock_http

            results = await asyncio.gather(
                client.analyze("t1", "c1", "k1"),
                client.analyze("t2", "c2", "k2"),
                client.analyze("t3", "c3", "k3"),
            )
            assert len(results) == 3
            for result, error in results:
                assert error is None
                assert result is not None
                assert result["score"] == 4
        await client.close()
