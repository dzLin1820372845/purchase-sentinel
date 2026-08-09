"""DingTalkClient 异步 HTTP 客户端单元测试（mocked httpx）"""
import os
import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# 设置测试环境变量（必须在 import 之前）
os.environ.setdefault("DINGTALK_APP_KEY", "test-app-key")
os.environ.setdefault("DINGTALK_APP_SECRET", "test-app-secret")
os.environ.setdefault("DINGTALK_BASE_ID", "test-base-id")
os.environ.setdefault("DINGTALK_SHEET_NAME", "test-sheet")
os.environ.setdefault("DINGTALK_OPERATOR_ID", "test-operator-id")

from app.dingtalk_client import DingTalkClient


TOKEN_RESPONSE = {
    "expireIn": 7200,
    "accessToken": "test-access-token-abc123",
}

PUSH_SUCCESS_RESPONSE = {
    "value": ["record-id-1", "record-id-2"],
}


def _make_mock_response(data: dict, status_code: int = 200):
    """创建 mock httpx.Response"""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    return resp


class TestDingTalkClientIsConfigured:

    def test_is_configured_true(self):
        """5 个 env var 都设置时 _is_configured() 返回 True"""
        client = DingTalkClient()
        assert client._is_configured() is True

    def test_is_configured_false(self):
        """缺少任意必需 env var 时 _is_configured() 返回 False"""
        with patch.dict(os.environ, {}, clear=True):
            # 清除所有钉钉环境变量
            for key in ["DINGTALK_APP_KEY", "DINGTALK_APP_SECRET", "DINGTALK_BASE_ID",
                         "DINGTALK_SHEET_NAME", "DINGTALK_OPERATOR_ID"]:
                os.environ.pop(key, None)
            client = DingTalkClient()
            assert client._is_configured() is False


class TestDingTalkClientEnsureToken:

    async def test_ensure_token_first_call(self):
        """首次调用 _ensure_token() 发起 HTTP POST 到 /v1.0/oauth2/accessToken"""
        client = DingTalkClient()
        mock_resp = _make_mock_response(TOKEN_RESPONSE)

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=mock_resp)
            mock_get.return_value = mock_http

            token = await client._ensure_token()
            assert token == "test-access-token-abc123"
            mock_http.post.assert_called_once()
            call_args = mock_http.post.call_args
            assert call_args[0][0] == "/v1.0/oauth2/accessToken"
            body = call_args.kwargs.get("json") or call_args[1].get("json")
            assert body["appKey"] == "test-app-key"
            assert body["appSecret"] == "test-app-secret"
        await client.close()

    async def test_ensure_token_cached(self):
        """300s 内第二次调用 _ensure_token() 不发 HTTP 请求（使用缓存）"""
        client = DingTalkClient()
        mock_resp = _make_mock_response(TOKEN_RESPONSE)

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=mock_resp)
            mock_get.return_value = mock_http

            # 第一次调用 — 获取 token
            token1 = await client._ensure_token()
            # 第二次调用 — 应使用缓存
            token2 = await client._ensure_token()
            assert token1 == token2
            # 只调用了一次 HTTP POST
            assert mock_http.post.call_count == 1
        await client.close()

    async def test_ensure_token_refresh(self):
        """过期后调用 _ensure_token() 重新获取 token"""
        client = DingTalkClient()
        mock_resp_old = _make_mock_response({
            "expireIn": 100,  # 很短的 TTL
            "accessToken": "old-token",
        })
        mock_resp_new = _make_mock_response({
            "expireIn": 7200,
            "accessToken": "new-token",
        })

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(side_effect=[mock_resp_old, mock_resp_new])
            mock_get.return_value = mock_http

            # 第一次调用
            token1 = await client._ensure_token()
            assert token1 == "old-token"

            # 模拟过期：将 _token_expires_at 设置为过去
            client._token_expires_at = time.time() - 1

            # 第二次调用 — 应重新获取
            token2 = await client._ensure_token()
            assert token2 == "new-token"
            assert mock_http.post.call_count == 2
        await client.close()


class TestDingTalkClientPushArticles:

    async def test_push_articles_success(self):
        """推送 2 篇文章返回 {"pushed": 2, "failed": 0}"""
        client = DingTalkClient()

        articles = [
            {"title": "文章1", "url": "http://a.com/1", "source": "NMPA",
             "ai_summary": "摘要1", "ai_category": "药品", "ai_score": 4},
            {"title": "文章2", "url": "http://a.com/2", "source": "SAMR",
             "ai_summary": "摘要2", "ai_category": "食品", "ai_score": 3},
        ]

        token_resp = _make_mock_response(TOKEN_RESPONSE)
        push_resp = _make_mock_response(PUSH_SUCCESS_RESPONSE)

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(side_effect=[token_resp, push_resp])
            mock_get.return_value = mock_http

            result = await client.push_articles(articles)
            assert result["pushed"] == 2
            assert result["failed"] == 0
        await client.close()

    async def test_push_articles_field_mapping(self):
        """推送时 records 的 fields 包含中文键名"""
        client = DingTalkClient()

        articles = [
            {"title": "文章1", "url": "http://a.com/1", "source": "NMPA",
             "ai_summary": "摘要1", "ai_category": "药品", "ai_score": 4},
        ]

        token_resp = _make_mock_response(TOKEN_RESPONSE)
        push_resp = _make_mock_response(PUSH_SUCCESS_RESPONSE)

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(side_effect=[token_resp, push_resp])
            mock_get.return_value = mock_http

            await client.push_articles(articles)

            # 找到 push 调用（第二个 POST）
            push_call = mock_http.post.call_args_list[1]
            body = push_call.kwargs.get("json") or push_call[1].get("json")
            fields = body["records"][0]["fields"]
            assert "标题" in fields
            assert "链接" in fields
            assert "来源" in fields
            assert "AI生成摘要" in fields
            assert "AI生成分类" in fields
            assert "AI评分" in fields
            assert fields["标题"] == "文章1"
            assert fields["链接"] == {"link": "http://a.com/1", "text": "http://a.com/1"}
            assert fields["AI评分"] == 4
        await client.close()

    async def test_push_articles_uses_correct_header(self):
        """推送时使用 x-acs-dingtalk-access-token header（不是 Authorization Bearer）"""
        client = DingTalkClient()
        articles = [
            {"title": "文章1", "url": "http://a.com/1", "source": "NMPA",
             "ai_summary": "摘要", "ai_category": "药品", "ai_score": 3},
        ]

        token_resp = _make_mock_response(TOKEN_RESPONSE)
        push_resp = _make_mock_response(PUSH_SUCCESS_RESPONSE)

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(side_effect=[token_resp, push_resp])
            mock_get.return_value = mock_http

            await client.push_articles(articles)

            # push 调用使用正确的 header
            push_call = mock_http.post.call_args_list[1]
            headers = push_call.kwargs.get("headers") or push_call[1].get("headers")
            assert headers is not None
            assert "x-acs-dingtalk-access-token" in headers
            assert headers["x-acs-dingtalk-access-token"] == "test-access-token-abc123"
            # 不使用 Authorization Bearer
            assert "Authorization" not in headers
        await client.close()

    async def test_push_articles_client_token(self):
        """每个批次生成不同 clientToken (UUID v4)"""
        client = DingTalkClient()

        # 25 篇文章 = 2 批（20 + 5）
        articles = [
            {"title": f"文章{i}", "url": f"http://a.com/{i}", "source": "NMPA",
             "ai_summary": "摘要", "ai_category": "药品", "ai_score": 3}
            for i in range(25)
        ]

        token_resp = _make_mock_response(TOKEN_RESPONSE)
        push_resp1 = _make_mock_response({"value": [f"id-{i}" for i in range(20)]})
        push_resp2 = _make_mock_response({"value": [f"id-{i}" for i in range(5)]})

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(side_effect=[token_resp, push_resp1, push_resp2])
            mock_get.return_value = mock_http

            result = await client.push_articles(articles)
            assert result["pushed"] == 25
            assert result["failed"] == 0

            # 检查两个批次有不同的 clientToken
            call1 = mock_http.post.call_args_list[1]
            call2 = mock_http.post.call_args_list[2]
            params1 = call1.kwargs.get("params") or call1[1].get("params")
            params2 = call2.kwargs.get("params") or call2[1].get("params")
            assert params1["clientToken"] != params2["clientToken"]
            # 验证是有效的 UUID
            uuid.UUID(params1["clientToken"])
            uuid.UUID(params2["clientToken"])
        await client.close()

    async def test_push_articles_retry_on_failure(self):
        """第一次失败后重试，第二次成功则 pushed 计数正确"""
        client = DingTalkClient()
        articles = [
            {"title": "文章1", "url": "http://a.com/1", "source": "NMPA",
             "ai_summary": "摘要", "ai_category": "药品", "ai_score": 4},
        ]

        token_resp = _make_mock_response(TOKEN_RESPONSE)
        fail_resp = _make_mock_response({"error": "server error"}, status_code=500)
        success_resp = _make_mock_response(PUSH_SUCCESS_RESPONSE)

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(
                side_effect=[token_resp, fail_resp, success_resp]
            )
            mock_get.return_value = mock_http

            result = await client.push_articles(articles)
            assert result["pushed"] == 1
            assert result["failed"] == 0
        await client.close()

    async def test_push_articles_all_attempts_fail(self):
        """两次都失败，返回 {"pushed": 0, "failed": N}"""
        client = DingTalkClient()
        articles = [
            {"title": "文章1", "url": "http://a.com/1", "source": "NMPA",
             "ai_summary": "摘要", "ai_category": "药品", "ai_score": 4},
            {"title": "文章2", "url": "http://a.com/2", "source": "SAMR",
             "ai_summary": "摘要2", "ai_category": "食品", "ai_score": 3},
        ]

        token_resp = _make_mock_response(TOKEN_RESPONSE)
        fail_resp = _make_mock_response({"error": "error"}, status_code=500)
        fail_resp2 = _make_mock_response({"error": "error"}, status_code=500)

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(
                side_effect=[token_resp, fail_resp, fail_resp2]
            )
            mock_get.return_value = mock_http

            result = await client.push_articles(articles)
            assert result["pushed"] == 0
            assert result["failed"] == 2
        await client.close()

    async def test_skip_when_not_configured(self):
        """_is_configured() 为 False 时返回 {"pushed": 0, "failed": 0, "reason": "not_configured"}"""
        with patch.dict(os.environ, {}, clear=True):
            for key in ["DINGTALK_APP_KEY", "DINGTALK_APP_SECRET", "DINGTALK_BASE_ID",
                         "DINGTALK_SHEET_NAME", "DINGTALK_OPERATOR_ID"]:
                os.environ.pop(key, None)
            client = DingTalkClient()

            articles = [{"title": "test", "url": "http://a.com", "source": "NMPA"}]
            result = await client.push_articles(articles)
            assert result == {"pushed": 0, "failed": 0, "reason": "not_configured"}

    async def test_push_empty_list(self):
        """空文章列表返回 {"pushed": 0, "failed": 0}"""
        client = DingTalkClient()
        result = await client.push_articles([])
        assert result == {"pushed": 0, "failed": 0}


class TestDingTalkClientClose:

    async def test_close_client(self):
        """close() 正常关闭 httpx client"""
        client = DingTalkClient()
        mock_http = AsyncMock()
        mock_http.is_closed = False
        mock_http.aclose = AsyncMock()
        client._client = mock_http

        await client.close()
        mock_http.aclose.assert_awaited_once()
