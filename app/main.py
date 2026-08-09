"""FastAPI 应用入口 — 采购舆情检测系统"""
import asyncio
import logging
import sys
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from app import config as config_module
from app import database
from app.llm_client import LLMClient
from app.dingtalk_client import DingTalkClient
from app.engine import ScraperEngine, http_get
from app.keyword_matcher import KeywordMatcher
from app.pipeline import PipelineCoordinator

# 配置日志：控制台 + 文件持久化
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

_file_handler = logging.FileHandler(LOG_DIR / "app.log", encoding="utf-8")
_file_handler.setLevel(logging.INFO)
_file_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s %(name)s: %(message)s"
))

_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setLevel(logging.INFO)
_console_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s: %(message)s"
))

logging.basicConfig(
    level=logging.INFO,
    handlers=[_file_handler, _console_handler],
)

logger = logging.getLogger(__name__)

# 模块级共享状态
matcher = KeywordMatcher()
llm = LLMClient()
dingtalk = DingTalkClient()
scheduler = AsyncIOScheduler()
_collect_lock = asyncio.Lock()
_last_collect_result: dict | None = None


async def scheduled_collect():
    """定时采集任务 — 由 APScheduler 触发。全量采集完成后自动补推历史未推送文章。"""
    try:
        strategies = config_module.load_strategies()
        engine = ScraperEngine()
        articles = await asyncio.to_thread(engine.run_all, strategies)
        coordinator = PipelineCoordinator(matcher, llm, dingtalk)
        stats = await coordinator.process_articles(articles)
        # 全量采集完成后自动补推历史未推送文章
        unsent = await database.get_unsent_dingtalk_articles(limit=200)
        if unsent:
            push_result = await dingtalk.push_articles(unsent)
            if push_result.get("pushed", 0) > 0:
                hashes = [a["url_hash"] for a in unsent]
                await database.mark_articles_dingtalk_sent(hashes)
            stats["dingtalk_unsent_pushed"] = push_result.get("pushed", 0)
            logger.info(f"定时采集+补推完成: {stats}")
        else:
            logger.info(f"定时采集完成: {stats}")
    except Exception as e:
        logger.error(f"定时采集异常: {e}")


async def _background_collect():
    """后台采集任务 — 手动触发时使用，持有互斥锁。"""
    global _last_collect_result
    async with _collect_lock:
        try:
            strategies = config_module.load_strategies()
            engine = ScraperEngine()
            articles = await asyncio.to_thread(engine.run_all, strategies)
            coordinator = PipelineCoordinator(matcher, llm, dingtalk)
            stats = await coordinator.process_articles(articles)
            # 全量采集完成后自动补推历史未推送文章
            unsent = await database.get_unsent_dingtalk_articles(limit=200)
            if unsent:
                push_result = await dingtalk.push_articles(unsent)
                if push_result.get("pushed", 0) > 0:
                    hashes = [a["url_hash"] for a in unsent]
                    await database.mark_articles_dingtalk_sent(hashes)
                stats["dingtalk_unsent_pushed"] = push_result.get("pushed", 0)
            _last_collect_result = stats
            logger.info(f"手动采集完成: {stats}")
        except Exception as e:
            _last_collect_result = {"error": str(e)}
            logger.error(f"手动采集异常: {e}")


async def _background_collect_site(site_name: str):
    """后台单站点采集任务。"""
    global _last_collect_result
    async with _collect_lock:
        try:
            strategies = config_module.load_strategies()
            target = [s for s in strategies if s.site_name == site_name]
            if not target:
                _last_collect_result = {"error": f"未找到站点: {site_name}"}
                return
            engine = ScraperEngine()
            articles = await asyncio.to_thread(engine.run, target[0])
            coordinator = PipelineCoordinator(matcher, llm, dingtalk)
            stats = await coordinator.process_articles(articles)
            _last_collect_result = stats
            logger.info(f"单站采集完成 ({site_name}): {stats}")
        except Exception as e:
            _last_collect_result = {"error": str(e)}
            logger.error(f"单站采集异常 ({site_name}): {e}")


async def _site_scheduled_collect(site_name: str):
    """单站点定时采集任务 — 由 APScheduler per-site job 触发。"""
    try:
        strategies = config_module.load_strategies()
        target = [s for s in strategies if s.site_name == site_name]
        if not target:
            logger.warning(f"未找到站点，跳过: {site_name}")
            return
        engine = ScraperEngine()
        articles = await asyncio.to_thread(engine.run, target[0])
        coordinator = PipelineCoordinator(matcher, llm, dingtalk)
        stats = await coordinator.process_articles(articles)
        logger.info(f"定时采集完成 ({site_name}): {stats}")
    except Exception as e:
        logger.error(f"定时采集异常 ({site_name}): {e}")


def _register_site_job(site_name: str, cron_expr: str, enabled: bool):
    """为单个站点注册或更新 APScheduler job。"""
    job_id = f"site_{site_name.replace(' ', '_')}"
    if not enabled:
        existing = scheduler.get_job(job_id)
        if existing:
            scheduler.remove_job(job_id)
        return
    try:
        parts = cron_expr.split()
        if len(parts) != 5:
            raise ValueError(f"Invalid cron: {cron_expr}")
        trigger = CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
        )
    except Exception as e:
        logger.error(f"Cron 表达式解析失败 ({site_name}): {e}")
        return
    scheduler.add_job(
        _site_scheduled_collect,
        trigger=trigger,
        id=job_id,
        args=[site_name],
        replace_existing=True,
        misfire_grace_time=300,
        coalesce=True,
    )
    logger.info(f"站点 job 已注册: {site_name} ({cron_expr})")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理 — startup 和 shutdown。"""
    # Startup
    await database.init_pool()
    keywords = await database.fetch_keywords()
    matcher.build(keywords)
    logger.info(f"已加载 {len(keywords)} 个关键词")

    # 从数据库加载全局默认调度
    default_cfg = await database.get_default_schedule()
    default_cron = default_cfg["cron_expression"] if default_cfg else "0 9,14,18 * * *"
    default_enabled = default_cfg["enabled"] if default_cfg else True

    # 获取所有已配置的站点
    site_cfgs = await database.get_site_schedules()

    # 从 config.yaml 加载所有站点列表
    strategies = config_module.load_strategies()
    all_site_names = [s.site_name for s in strategies]

    # 为每个站点注册 job（已配置的用站点配置，未配置的用默认）
    for site_name in all_site_names:
        if site_name in site_cfgs:
            cfg = site_cfgs[site_name]
            _register_site_job(site_name, cfg["cron_expression"], cfg["enabled"])
        else:
            _register_site_job(site_name, default_cron, default_enabled)

    logger.info(f"站点 job 已注册: {len(all_site_names)} 个")

    scheduler.start()
    logger.info("调度器已启动")

    yield

    # Shutdown
    scheduler.shutdown(wait=False)
    logger.info("调度器已关闭")
    await llm.close()
    logger.info("LLM 客户端已关闭")
    await dingtalk.close()
    logger.info("DingTalk 客户端已关闭")
    await database.close_pool()


app = FastAPI(
    title="采购舆情检测系统",
    version="0.1.0",
    lifespan=lifespan,
)

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _get_next_run_time() -> str | None:
    """获取下次调度采集时间（ISO 字符串）。"""
    try:
        job = scheduler.get_job("daily_collect")
        if job and job.next_run_time:
            return job.next_run_time.isoformat()
    except Exception:
        pass
    return None


# --- API 路由 ---


@app.get("/")
async def dashboard():
    """渲染单页监控仪表盘。"""
    html = (TEMPLATES_DIR / "dashboard.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html)


@app.get("/schedule")
async def schedule_page():
    """调度配置已合并到仪表盘，重定向到首页。"""
    from starlette.responses import RedirectResponse
    return RedirectResponse(url="/")


@app.get("/api/health")
async def health_check():
    """健康检查端点。"""
    return {"status": "ok", "scheduler_running": scheduler.running}


@app.post("/api/collect")
async def manual_collect():
    """手动触发采集。正在运行时返回 409。"""
    if _collect_lock.locked():
        return JSONResponse(
            status_code=409,
            content={"status": "running", "message": "采集正在运行中"},
        )
    asyncio.create_task(_background_collect())
    return JSONResponse(
        status_code=202,
        content={"status": "started", "message": "已开始采集"},
    )


@app.post("/api/collect/{site_name}")
async def manual_collect_site(site_name: str):
    """手动触发单站点采集。正在运行时返回 409。"""
    if _collect_lock.locked():
        return JSONResponse(
            status_code=409,
            content={"status": "running", "message": "采集正在运行中"},
        )
    strategies = config_module.load_strategies()
    if not any(s.site_name == site_name for s in strategies):
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": f"未找到站点: {site_name}"},
        )
    asyncio.create_task(_background_collect_site(site_name))
    return JSONResponse(
        status_code=202,
        content={"status": "started", "message": f"已开始采集: {site_name}"},
    )


@app.get("/api/collect/status")
async def collect_status():
    """查询最近一次采集结果。"""
    return {"status": "idle", "last_result": _last_collect_result}


@app.get("/api/dingtalk/unsent-count")
async def dingtalk_unsent_count():
    """查询待推送钉钉的文章数量。"""
    count = await database.count_unsent_dingtalk_articles()
    return {"unsent_count": count}


@app.post("/api/dingtalk/push")
async def dingtalk_manual_push():
    """手动推送所有未推送的钉钉文章。采集进行中返回 409。"""
    if _collect_lock.locked():
        return JSONResponse(
            status_code=409,
            content={"status": "error", "message": "采集正在运行中，请稍后重试"},
        )
    try:
        unsent = await database.get_unsent_dingtalk_articles(limit=100)
        if not unsent:
            return {"status": "ok", "pushed": 0, "failed": 0, "message": "没有待推送的文章"}

        push_result = await dingtalk.push_articles(unsent)
        pushed = push_result.get("pushed", 0)
        failed = push_result.get("failed", 0)

        if pushed > 0:
            hashes = [a["url_hash"] for a in unsent[:pushed]]
            await database.mark_articles_dingtalk_sent(hashes)

        if failed > 0:
            await database.insert_failure_record(0, "dingtalk", f"Manual push failed: {failed} articles")

        logger.info(f"手动推送钉钉完成: pushed={pushed}, failed={failed}")
        return {"status": "ok", "pushed": pushed, "failed": failed}
    except Exception as e:
        logger.error(f"手动推送钉钉异常: {e}")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)},
        )


@app.get("/api/dashboard/stats")
async def dashboard_stats():
    """今日统计 + 系统状态。"""
    stats = await database.get_today_stats()
    unsent_count = await database.count_unsent_dingtalk_articles()
    return {
        "today_total": stats["total"],
        "today_matched": stats["matched"],
        "today_high_score": stats["high_score"],
        "today_pushed": stats["pushed"],
        "scheduler_running": scheduler.running,
        "collecting": _collect_lock.locked(),
        "last_result": _last_collect_result,
        "next_run": _get_next_run_time(),
        "dingtalk_unsent": unsent_count,
    }


@app.get("/api/dashboard/articles")
async def dashboard_articles(
    page: int = 1,
    category: str | None = None,
    min_score: int | None = None,
    search: str | None = None,
    dingtalk_sent: bool | None = None,
):
    """文章列表 — 分页 + 筛选 + 搜索。"""
    limit = 20
    offset = (page - 1) * limit
    articles, total = await database.get_recent_articles(
        limit=limit, offset=offset,
        category=category, min_score=min_score, search=search,
        dingtalk_sent=dingtalk_sent,
    )
    return {"articles": articles, "total": total, "page": page}


@app.get("/api/dashboard/sites")
async def dashboard_sites():
    """各站点今日采集状态 — 合并 config.yaml 已注册站点和数据库采集记录。"""
    db_status = await database.get_site_status()

    strategies = config_module.load_strategies()
    site_names = [s.site_name for s in strategies]

    # 数据库 source 格式为 "站点名-分类名"，需按站点名聚合
    aggregated: dict[str, dict] = {}
    for entry in db_status:
        # 找到匹配的站点名（最长前缀匹配）
        matched_name = None
        for name in site_names:
            if entry["source"] == name or entry["source"].startswith(name + "-"):
                matched_name = name
                break
        if not matched_name:
            matched_name = entry["source"]

        if matched_name not in aggregated:
            aggregated[matched_name] = {
                "source": matched_name,
                "today_count": 0,
                "last_collected": entry["last_collected"],
            }
        aggregated[matched_name]["today_count"] += entry["today_count"]
        if entry["last_collected"]:
            if not aggregated[matched_name]["last_collected"] or entry["last_collected"] > aggregated[matched_name]["last_collected"]:
                aggregated[matched_name]["last_collected"] = entry["last_collected"]

    # 合并 config 注册站点（确保未采集的站点也显示）
    seen = set(aggregated.keys())
    for name in site_names:
        if name not in seen:
            aggregated[name] = {
                "source": name,
                "today_count": 0,
                "last_collected": None,
            }

    return sorted(aggregated.values(), key=lambda x: x["source"])


@app.post("/api/werss/webhook")
async def werss_webhook(request: Request):
    """WeRSS Webhook — 接收微信公众号文章推送。始终返回 200。"""
    try:
        raw = await request.body()
        content_type = request.headers.get("content-type", "")
        logger.info(f"WeRSS webhook received: content_type={content_type}, body={raw[:500]}")

        # 解析 payload: JSON -> ast.literal_eval -> form-urlencoded
        payload = None
        text = raw.decode("utf-8", errors="replace").strip()

        if text:
            import json as _json
            import ast
            try:
                payload = _json.loads(text)
            except (_json.JSONDecodeError, ValueError):
                try:
                    payload = ast.literal_eval(text)
                except (ValueError, SyntaxError):
                    if "form" in content_type:
                        from urllib.parse import parse_qs
                        parsed = parse_qs(text)
                        payload = {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}

        if payload is None:
            logger.error(f"WeRSS webhook: unparseable body: {text[:500]}")
            return {"status": "error", "detail": "unparseable body"}

        # 支持多种推送格式: {"articles": [...]}, 单条 {}, 批量 [...]
        if isinstance(payload, dict) and "articles" in payload:
            items = payload["articles"]
        elif isinstance(payload, list):
            items = payload
        else:
            items = [payload]

        logger.info(f"WeRSS parsed payload: type={type(items).__name__}, count={len(items) if items else 0}")

        coordinator = PipelineCoordinator(matcher, llm, dingtalk)
        for item in items:
            logger.info(f"WeRSS raw item: {item}")

            import re as _re

            from app.models import Article

            # 兼容多种字段名
            url = item.get("url") or item.get("link") or ""
            source = item.get("mp_name") or item.get("feed_title") or item.get("author") or "微信公众号"
            source_category = item.get("mp_id") or None
            published_at = item.get("publish_time") or item.get("published") or item.get("pub_date")

            # 微信文章：始终从 URL 抓取正文和标题（忽略 WeRSS 推送的 description/summary/title）
            title = ""
            content = ""
            if url and "mp.weixin.qq.com" in url:
                page = http_get(url, retry=2, retry_delay=3)
                if page:
                    for sel in ["#js_content", "div.rich_media_content"]:
                        els = page.css(sel)
                        if els:
                            texts = [t.get().strip() for t in els[0].css("::text") if t.get().strip()]
                            if texts:
                                content = "\n".join(texts)
                                break
                    body_text = page.css("body")[0].get() if page.css("body") else ""
                    m = _re.search(r"var\s+msg_title\s*=\s*'(.*?)'", body_text)
                    if m and m.group(1).strip():
                        title = m.group(1).strip().replace("&amp;", "&")
                    logger.info(f"WeRSS fetched: url={url} title={title[:30] if title else '(empty)'} content_len={len(content)}")
                else:
                    logger.warning(f"WeRSS failed to fetch article page: {url}")

            if not title or not url:
                logger.warning(f"WeRSS item missing title/url, skipping. Item: {item}")
                continue

            article = Article(
                title=title,
                url=url,
                source=source,
                source_category=source_category,
                source_type="wechat",
                published_at=published_at,
                content=content,
            )
            await coordinator.process_articles([article])
            logger.info(f"WeRSS 文章已处理: {article.title[:30]}")
    except Exception as e:
        logger.error(f"WeRSS Webhook 处理异常: {e}")

    return {"status": "ok"}


class ScheduleUpdate(BaseModel):
    hours: list[int]
    enabled: bool = True


class SiteScheduleUpdate(BaseModel):
    cron_expression: str
    enabled: bool = True


class KeywordCreate(BaseModel):
    keyword: str
    category: str | None = None


@app.get("/api/keywords")
async def list_keywords(page: int = 1, page_size: int = 20):
    """分页列出关键词（含启用/禁用状态）。"""
    return await database.list_all_keywords(page, page_size)


@app.post("/api/keywords")
async def add_keyword(body: KeywordCreate):
    """添加关键词并重建 AC 自动机。"""
    new_id = await database.add_keyword(body.keyword, body.category)
    if new_id is None:
        return JSONResponse(
            status_code=409,
            content={"error": "关键词已存在"},
        )
    keywords = await database.fetch_keywords()
    matcher.rebuild(keywords)
    return {"status": "ok", "id": new_id}


@app.delete("/api/keywords/{keyword_id}")
async def delete_keyword(keyword_id: int):
    """删除关键词并重建 AC 自动机。"""
    deleted = await database.delete_keyword(keyword_id)
    if not deleted:
        return JSONResponse(
            status_code=404,
            content={"error": "关键词不存在"},
        )
    keywords = await database.fetch_keywords()
    matcher.rebuild(keywords)
    return {"status": "ok"}


@app.get("/api/schedule")
async def get_schedule():
    """获取当前调度配置。"""
    config = await database.get_schedule_config()
    if config is None:
        return {"hours": [9, 14, 18], "enabled": True}
    return {"hours": config["hours"], "enabled": config["enabled"]}


@app.put("/api/schedule")
async def update_schedule(body: ScheduleUpdate):
    """更新调度配置，立即生效。"""
    await database.update_schedule_config(body.hours, body.enabled)

    if body.enabled:
        new_trigger = CronTrigger(
            hour=",".join(str(h) for h in body.hours), minute=0
        )
        scheduler.reschedule_job("daily_collect", trigger=new_trigger)
        logger.info(f"调度已更新: {body.hours} 点")
    else:
        scheduler.pause_job("daily_collect")
        logger.info("调度已暂停")

    return {"hours": body.hours, "enabled": body.enabled}


# === per-site 调度配置 API ===

@app.get("/api/schedule/sites")
async def get_site_schedules_api():
    """获取所有站点的调度配置（含 __default__ 全局默认）。"""
    schedules = await database.get_site_schedules()
    default = await database.get_default_schedule()
    if default is None:
        default = {"cron_expression": "0 9,14,18 * * *", "enabled": True, "updated_at": None}
    return {"sites": schedules, "default": default}


@app.put("/api/schedule/{site_name}")
async def update_site_schedule(site_name: str, body: SiteScheduleUpdate):
    """更新指定站点的调度配置。site_name='__default__' 更新全局默认。"""
    result = await database.upsert_site_schedule(site_name, body.cron_expression, body.enabled)
    # 动态更新 APScheduler job
    _register_site_job(site_name, body.cron_expression, body.enabled)
    return result


@app.delete("/api/schedule/{site_name}")
async def delete_site_schedule_api(site_name: str):
    """删除站点调度配置（恢复到跟随全局默认）。__default__ 不可删除。"""
    if site_name == "__default__":
        return JSONResponse(status_code=400, content={"error": "无法删除全局默认配置"})
    deleted = await database.delete_site_schedule(site_name)
    if not deleted:
        return JSONResponse(status_code=404, content={"error": f"未找到站点: {site_name}"})
    # 恢复为跟随全局默认
    default_cfg = await database.get_default_schedule()
    default_cron = default_cfg["cron_expression"] if default_cfg else "0 9,14,18 * * *"
    default_enabled = default_cfg["enabled"] if default_cfg else True
    _register_site_job(site_name, default_cron, default_enabled)
    return {"site_name": site_name, "deleted": True}
