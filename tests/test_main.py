"""FastAPI 端点测试 — 覆盖 SCHED-01~04, COLL-03

使用 mock 隔离外部依赖（数据库、LLM、关键词匹配器），不依赖真实服务。
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_external_deps():
    """Mock 所有外部依赖，避免连接真实数据库/外部服务。"""
    with (
        patch("app.main.database") as mock_db,
        patch("app.main.llm") as mock_llm,
        patch("app.main.matcher") as mock_matcher,
        patch("app.main.config_module") as mock_config,
    ):
        # --- database mock ---
        mock_db.init_pool = AsyncMock()
        mock_db.close_pool = AsyncMock()
        mock_db.fetch_keywords = AsyncMock(
            return_value=[{"keyword": "处罚", "category": "综合"}]
        )
        mock_db.get_schedule_config = AsyncMock(
            return_value={"hours": [9, 14, 18], "enabled": True}
        )
        mock_db.update_schedule_config = AsyncMock()

        # --- dashboard/keyword database mocks (Phase 5) ---
        mock_db.get_today_stats = AsyncMock(
            return_value={"total": 10, "matched": 5, "high_score": 3}
        )
        mock_db.get_recent_articles = AsyncMock(
            return_value=(
                [{"id": 1, "title": "测试文章", "url": "https://test.com",
                  "source": "测试来源", "category": "食品", "score": 4,
                  "collected_at": "2026-04-29T14:00:00"}],
                1,
            )
        )
        mock_db.get_site_status = AsyncMock(
            return_value=[
                {"source": "国家药监局", "today_count": 5, "last_collected": "2026-04-29T14:00:00"}
            ]
        )
        mock_db.list_all_keywords = AsyncMock(
            return_value=[
                {"id": 1, "keyword": "处罚", "category": "综合", "enabled": True, "created_at": "2026-04-28T10:00:00"}
            ]
        )
        mock_db.add_keyword = AsyncMock(return_value=2)
        mock_db.delete_keyword = AsyncMock(return_value=True)

        # --- llm client mock ---
        mock_llm.close = AsyncMock()

        # --- keyword matcher mock ---
        mock_matcher.build = MagicMock()
        mock_matcher.match = MagicMock(return_value=[])

        # --- config mock ---
        mock_config.load_strategies = MagicMock(return_value=[])

        yield {
            "db": mock_db,
            "llm": mock_llm,
            "matcher": mock_matcher,
            "config": mock_config,
        }


@pytest.fixture
def client(_mock_external_deps):
    """创建 TestClient 并触发 lifespan startup/shutdown。"""
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_module_state():
    """每个测试前后重置模块级可变状态。"""
    import app.main as main_mod

    main_mod._last_collect_result = None
    # 确保锁未被持有
    assert not main_mod._collect_lock.locked()
    yield
    # 清理：如果测试遗留了锁，释放它
    if main_mod._collect_lock.locked():
        main_mod._collect_lock.release()
    main_mod._last_collect_result = None


# ===========================================================================
# 1. Lifespan Tests
# ===========================================================================


class TestLifespan:
    """验证 FastAPI lifespan 的 startup 和 shutdown 行为。"""

    def test_startup_calls_init_pool(self, client, _mock_external_deps):
        """SCHED-01: startup 时调用 database.init_pool()。"""
        _mock_external_deps["db"].init_pool.assert_awaited_once()

    def test_startup_calls_fetch_keywords(self, client, _mock_external_deps):
        """SCHED-01: startup 时调用 database.fetch_keywords()。"""
        _mock_external_deps["db"].fetch_keywords.assert_awaited_once()

    def test_startup_calls_matcher_build(self, client, _mock_external_deps):
        """SCHED-01: startup 时用关键词列表构建 matcher。"""
        _mock_external_deps["matcher"].build.assert_called_once_with(
            [{"keyword": "处罚", "category": "综合"}]
        )

    def test_shutdown_closes_llm(self, _mock_external_deps):
        """Shutdown 时关闭 LLM 客户端。"""
        from app.main import app

        with TestClient(app):
            pass
        _mock_external_deps["llm"].close.assert_awaited()

    def test_shutdown_closes_pool(self, _mock_external_deps):
        """Shutdown 时关闭数据库连接池。"""
        from app.main import app

        with TestClient(app):
            pass
        _mock_external_deps["db"].close_pool.assert_awaited()


# ===========================================================================
# 2. Health Check — COLL-03
# ===========================================================================


class TestHealthCheck:
    def test_health_returns_ok(self, client):
        """COLL-03: GET /api/health 返回 status=ok。"""
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "scheduler_running" in data

    def test_health_scheduler_running(self, client):
        """COLL-03: scheduler_running 字段为布尔值。"""
        resp = client.get("/api/health")
        data = resp.json()
        assert isinstance(data["scheduler_running"], bool)


# ===========================================================================
# 3. Manual Collect — COLL-03
# ===========================================================================


class TestManualCollect:
    def test_collect_accepted(self, client):
        """COLL-03: POST /api/collect 返回 202 started。"""
        resp = client.post("/api/collect")
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "started"
        assert "message" in data

    def test_collect_conflict_when_locked(self, client):
        """COLL-03: 锁已持有时返回 409 running。"""
        import app.main as main_mod

        # 在另一个线程中获取锁，使 _collect_lock.locked() == True
        # TestClient 运行在独立线程，锁是 asyncio.Lock — 需要在同事件循环获取
        loop = asyncio.new_event_loop()
        loop.run_until_complete(main_mod._collect_lock.acquire())
        try:
            resp = client.post("/api/collect")
            assert resp.status_code == 409
            assert resp.json()["status"] == "running"
        finally:
            main_mod._collect_lock.release()
            loop.close()


# ===========================================================================
# 4. Collect Status
# ===========================================================================


class TestCollectStatus:
    def test_status_idle_no_result(self, client):
        """无采集记录时返回 idle + last_result=None。"""
        resp = client.get("/api/collect/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "idle"
        assert data["last_result"] is None

    def test_status_with_previous_result(self, client):
        """有采集结果时返回上次结果。"""
        import app.main as main_mod

        main_mod._last_collect_result = {"total": 5, "processed": 3}
        resp = client.get("/api/collect/status")
        data = resp.json()
        assert data["status"] == "idle"
        assert data["last_result"]["total"] == 5
        assert data["last_result"]["processed"] == 3

    def test_status_with_error_result(self, client):
        """采集出错后 last_result 包含 error 字段。"""
        import app.main as main_mod

        main_mod._last_collect_result = {"error": "connection timeout"}
        resp = client.get("/api/collect/status")
        data = resp.json()
        assert "error" in data["last_result"]


# ===========================================================================
# 5. WeRSS Webhook
# ===========================================================================


class TestWeRSSWebhook:
    def test_webhook_single_article(self, client):
        """单条 WeRSS 文章推送被正确处理。"""
        with patch("app.main.PipelineCoordinator") as MockPipeline:
            mock_inst = MagicMock()
            mock_inst.process_articles = AsyncMock(
                return_value={"total": 1, "processed": 1}
            )
            MockPipeline.return_value = mock_inst

            resp = client.post(
                "/api/werss/webhook",
                json={
                    "title": "测试文章标题",
                    "url": "https://mp.weixin.qq.com/s/test123",
                    "mp_name": "测试公众号",
                    "publish_time": "2026-04-29",
                    "description": "文章内容描述",
                },
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"

            # 验证 Article 对象字段
            call_args = mock_inst.process_articles.call_args[0][0]
            assert len(call_args) == 1
            article = call_args[0]
            assert article.source_type == "wechat"
            assert article.source == "测试公众号"
            assert article.title == "测试文章标题"

    def test_webhook_batch_articles(self, client):
        """批量 WeRSS 文章推送（列表格式）。"""
        with patch("app.main.PipelineCoordinator") as MockPipeline:
            mock_inst = MagicMock()
            mock_inst.process_articles = AsyncMock(
                return_value={"total": 1, "processed": 1}
            )
            MockPipeline.return_value = mock_inst

            resp = client.post(
                "/api/werss/webhook",
                json=[
                    {"title": "文章1", "url": "https://test.com/1"},
                    {"title": "文章2", "url": "https://test.com/2"},
                ],
            )
            assert resp.status_code == 200
            assert mock_inst.process_articles.await_count == 2

    def test_webhook_always_200_on_empty(self, client):
        """空 payload 也返回 200。"""
        resp = client.post("/api/werss/webhook", json={})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_webhook_default_source(self, client):
        """无 mp_name 时 source 默认为 '微信公众号'。"""
        with patch("app.main.PipelineCoordinator") as MockPipeline:
            mock_inst = MagicMock()
            mock_inst.process_articles = AsyncMock(
                return_value={"total": 0, "processed": 0}
            )
            MockPipeline.return_value = mock_inst

            resp = client.post(
                "/api/werss/webhook",
                json={"title": "无来源文章", "url": "https://test.com/3"},
            )
            assert resp.status_code == 200
            article = mock_inst.process_articles.call_args[0][0][0]
            assert article.source == "微信公众号"


# ===========================================================================
# 6. Schedule API — SCHED-01~04
# ===========================================================================


class TestGetSchedule:
    def test_get_schedule_returns_config(self, client):
        """SCHED-01: GET /api/schedule 返回当前调度配置。"""
        resp = client.get("/api/schedule")
        assert resp.status_code == 200
        data = resp.json()
        assert data["hours"] == [9, 14, 18]
        assert data["enabled"] is True

    def test_get_schedule_returns_default_when_none(self):
        """SCHED-01: config 为 None 时返回默认调度。"""
        with patch("app.main.database") as mock_db:
            mock_db.init_pool = AsyncMock()
            mock_db.close_pool = AsyncMock()
            mock_db.fetch_keywords = AsyncMock(return_value=[])
            mock_db.get_schedule_config = AsyncMock(return_value=None)

            from app.main import app

            with TestClient(app) as c:
                resp = c.get("/api/schedule")
            assert resp.status_code == 200
            data = resp.json()
            assert data["hours"] == [9, 14, 18]
            assert data["enabled"] is True


class TestUpdateSchedule:
    def test_update_schedule_enabled(self, client, _mock_external_deps):
        """SCHED-03: PUT /api/schedule 更新启用状态的调度。"""
        resp = client.put(
            "/api/schedule", json={"hours": [8, 12, 18], "enabled": True}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["hours"] == [8, 12, 18]
        assert data["enabled"] is True
        _mock_external_deps["db"].update_schedule_config.assert_awaited_once_with(
            [8, 12, 18], True
        )

    def test_update_schedule_disabled(self, client, _mock_external_deps):
        """SCHED-03: PUT /api/schedule 禁用调度时暂停任务。"""
        resp = client.put(
            "/api/schedule", json={"hours": [9, 14, 18], "enabled": False}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False
        _mock_external_deps["db"].update_schedule_config.assert_awaited_once_with(
            [9, 14, 18], False
        )

    def test_update_schedule_empty_hours_raises(self, client):
        """SCHED-03: 空的 hours 列表导致 CronTrigger 构建失败（已知缺陷）。

        注意: 当前 ScheduleUpdate 模型未校验 hours 非空，CronTrigger(hour="")
        会抛出 ValueError。此测试记录该行为，后续应在模型层添加 min_length=1。
        """
        with pytest.raises(ValueError, match="Unrecognized expression"):
            client.put("/api/schedule", json={"hours": [], "enabled": True})

    def test_update_schedule_reschedule_called(self, client, _mock_external_deps):
        """SCHED-04: 更新调度后 APScheduler reschedule_job 被调用。"""
        import app.main as main_mod

        with patch.object(main_mod.scheduler, "reschedule_job") as mock_reschedule:
            resp = client.put(
                "/api/schedule", json={"hours": [8, 16], "enabled": True}
            )
            assert resp.status_code == 200
            mock_reschedule.assert_called_once()
            # 验证第一个位置参数是 job id "daily_collect"
            call_args = mock_reschedule.call_args
            assert call_args[0][0] == "daily_collect"


# ===========================================================================
# 7. Scheduled Collect (async unit tests)
# ===========================================================================


@pytest.mark.asyncio
async def test_scheduled_collect_runs_pipeline(_mock_external_deps):
    """SCHED-02: 定时采集调用 pipeline 处理文章。"""
    with (
        patch("app.main.PipelineCoordinator") as MockPipeline,
        patch("app.main.ScraperEngine") as MockEngine,
        patch("app.main.asyncio.to_thread", new_callable=AsyncMock, return_value=[]) as mock_to_thread,
    ):
        mock_pipeline_inst = MagicMock()
        mock_pipeline_inst.process_articles = AsyncMock(
            return_value={"total": 0, "processed": 0, "duplicates": 0, "failed": 0}
        )
        MockPipeline.return_value = mock_pipeline_inst

        from app.main import scheduled_collect

        await scheduled_collect()

        mock_to_thread.assert_awaited_once()
        mock_pipeline_inst.process_articles.assert_awaited_once()


@pytest.mark.asyncio
async def test_scheduled_collect_handles_error(_mock_external_deps):
    """SCHED-02: 定时采集中异常不会向外传播。"""
    _mock_external_deps["config"].load_strategies.side_effect = RuntimeError(
        "test error"
    )
    from app.main import scheduled_collect

    # 不应抛出异常
    await scheduled_collect()


@pytest.mark.asyncio
async def test_background_collect_updates_last_result(_mock_external_deps):
    """COLL-03: 后台采集完成后更新 _last_collect_result。"""
    with (
        patch("app.main.PipelineCoordinator") as MockPipeline,
        patch("app.main.ScraperEngine"),
        patch("app.main.asyncio.to_thread", new_callable=AsyncMock, return_value=[]),
    ):
        mock_pipeline_inst = MagicMock()
        mock_pipeline_inst.process_articles = AsyncMock(
            return_value={"total": 3, "processed": 2, "duplicates": 1, "failed": 0}
        )
        MockPipeline.return_value = mock_pipeline_inst

        import app.main as main_mod

        await main_mod._background_collect()

        assert main_mod._last_collect_result == {
            "total": 3,
            "processed": 2,
            "duplicates": 1,
            "failed": 0,
        }


@pytest.mark.asyncio
async def test_background_collect_records_error(_mock_external_deps):
    """COLL-03: 后台采集异常时 _last_collect_result 记录 error。"""
    _mock_external_deps["config"].load_strategies.side_effect = RuntimeError(
        "db down"
    )

    import app.main as main_mod

    await main_mod._background_collect()

    assert main_mod._last_collect_result is not None
    assert "error" in main_mod._last_collect_result
    assert "db down" in main_mod._last_collect_result["error"]


# ===========================================================================
# 8. Dashboard Rendering — DASH-01
# ===========================================================================


class TestDashboard:
    def test_dashboard_returns_html(self, client):
        """DASH-01: GET / 返回 HTML 页面。"""
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "采购舆情监控系统" in resp.text

    def test_dashboard_contains_collect_button(self, client):
        """DASH-06: 仪表盘包含手动采集按钮。"""
        resp = client.get("/")
        assert "手动采集" in resp.text


# ===========================================================================
# 9. Dashboard Stats API — DASH-02, DASH-03
# ===========================================================================


class TestDashboardStats:
    def test_stats_returns_today_counts(self, client):
        """DASH-03: /api/dashboard/stats 返回今日统计。"""
        resp = client.get("/api/dashboard/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["today_total"] == 10
        assert data["today_matched"] == 5
        assert data["today_high_score"] == 3

    def test_stats_returns_system_status(self, client):
        """DASH-02: stats 包含 scheduler_running 和 collecting 状态。"""
        resp = client.get("/api/dashboard/stats")
        data = resp.json()
        assert isinstance(data["scheduler_running"], bool)
        assert isinstance(data["collecting"], bool)

    def test_stats_calls_get_today_stats(self, client, _mock_external_deps):
        """DASH-03: 调用 database.get_today_stats()。"""
        client.get("/api/dashboard/stats")
        _mock_external_deps["db"].get_today_stats.assert_awaited_once()


# ===========================================================================
# 10. Dashboard Articles API — DASH-04
# ===========================================================================


class TestDashboardArticles:
    def test_articles_returns_list(self, client):
        """DASH-04: /api/dashboard/articles 返回文章列表。"""
        resp = client.get("/api/dashboard/articles")
        assert resp.status_code == 200
        data = resp.json()
        assert "articles" in data
        assert "total" in data
        assert "page" in data

    def test_articles_default_page_1(self, client):
        """DASH-04: 默认 page=1。"""
        resp = client.get("/api/dashboard/articles")
        assert resp.json()["page"] == 1

    def test_articles_passes_pagination(self, client, _mock_external_deps):
        """DASH-04: page 参数传递给 database 函数。"""
        resp = client.get("/api/dashboard/articles?page=3")
        assert resp.status_code == 200
        call_args = _mock_external_deps["db"].get_recent_articles.call_args
        assert call_args[1]["offset"] == 40  # (3-1) * 20

    def test_articles_passes_filters(self, client, _mock_external_deps):
        """DASH-04: category/min_score/search 参数传递。"""
        client.get("/api/dashboard/articles?category=食品&min_score=3&search=测试")
        call_args = _mock_external_deps["db"].get_recent_articles.call_args
        assert call_args[1]["category"] == "食品"
        assert call_args[1]["min_score"] == 3
        assert call_args[1]["search"] == "测试"


# ===========================================================================
# 11. Dashboard Sites API — DASH-05
# ===========================================================================


class TestDashboardSites:
    def test_sites_returns_list(self, client):
        """DASH-05: /api/dashboard/sites 返回站点状态列表。"""
        resp = client.get("/api/dashboard/sites")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0
        assert data[0]["source"] == "国家药监局"
        assert data[0]["today_count"] == 5

    def test_sites_calls_get_site_status(self, client, _mock_external_deps):
        """DASH-05: 调用 database.get_site_status()。"""
        client.get("/api/dashboard/sites")
        _mock_external_deps["db"].get_site_status.assert_awaited_once()


# ===========================================================================
# 12. Keywords CRUD API — CONF-01, CONF-02
# ===========================================================================


class TestKeywords:
    def test_list_keywords(self, client):
        """CONF-01: GET /api/keywords 返回关键词列表。"""
        resp = client.get("/api/keywords")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert data[0]["keyword"] == "处罚"

    def test_add_keyword_success(self, client, _mock_external_deps):
        """CONF-01: POST /api/keywords 添加成功返回 200。"""
        resp = client.post(
            "/api/keywords",
            json={"keyword": "新关键词", "category": "食品"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["id"] == 2

    def test_add_keyword_triggers_rebuild(self, client, _mock_external_deps):
        """CONF-02: 添加关键词后触发 AC 自动机重建。"""
        client.post("/api/keywords", json={"keyword": "新关键词"})
        _mock_external_deps["matcher"].rebuild.assert_called()

    def test_add_duplicate_keyword_returns_409(self, client, _mock_external_deps):
        """CONF-01: 重复关键词返回 409。"""
        _mock_external_deps["db"].add_keyword = AsyncMock(return_value=None)
        resp = client.post(
            "/api/keywords", json={"keyword": "已存在关键词"}
        )
        assert resp.status_code == 409
        assert "已存在" in resp.json()["error"]

    def test_delete_keyword_success(self, client, _mock_external_deps):
        """CONF-01: DELETE /api/keywords/{id} 删除成功。"""
        resp = client.delete("/api/keywords/1")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_delete_keyword_triggers_rebuild(self, client, _mock_external_deps):
        """CONF-02: 删除关键词后触发 AC 自动机重建。"""
        client.delete("/api/keywords/1")
        _mock_external_deps["matcher"].rebuild.assert_called()

    def test_delete_nonexistent_keyword_returns_404(self, client, _mock_external_deps):
        """CONF-01: 删除不存在关键词返回 404。"""
        _mock_external_deps["db"].delete_keyword = AsyncMock(return_value=False)
        resp = client.delete("/api/keywords/999")
        assert resp.status_code == 404
        assert "不存在" in resp.json()["error"]
