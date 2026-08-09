-- 采购舆情检测系统 - 数据库初始化脚本
-- Docker 首次启动时自动执行（docker-entrypoint-initdb.d）
-- 所有语句使用 IF NOT EXISTS 保护，确保幂等性

-- 启用 pgvector 扩展（用于未来 RAG 语义搜索）
CREATE EXTENSION IF NOT EXISTS vector;

-- 文章表：存储采集到的监管文章及其 AI 分析结果
CREATE TABLE IF NOT EXISTS articles (
    id              SERIAL PRIMARY KEY,                              -- 自增主键
    url_hash        VARCHAR(64) UNIQUE NOT NULL,                     -- URL 的 MD5 哈希，用于去重
    title           TEXT NOT NULL,                                    -- 文章标题
    url             TEXT NOT NULL,                                    -- 文章原始链接
    source          VARCHAR(200) NOT NULL,                           -- 来源名称，如"国家药监局-化妆品监管工作"
    source_type     VARCHAR(20) NOT NULL DEFAULT 'website'           -- 来源类型：website=政府网站, wechat=微信公众号
                    CHECK (source_type IN ('wechat', 'website')),
    source_category VARCHAR(100),                                    -- 采集板块，如"化妆品-监管工作"
    published_at    TIMESTAMPTZ,                                     -- 文章原始发布时间（可能为空）
    collected_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),              -- 系统采集时间
    content         TEXT,                                            -- 文章正文全文
    matched_keywords JSONB DEFAULT '[]'::jsonb,                     -- 命中的合规关键词列表，JSON 数组格式
    ai_summary      TEXT,                                            -- AI 生成的 50-100 字摘要
    ai_category     VARCHAR(50),                                     -- AI 分类：食品/化妆品/药品/医疗器械/综合
    ai_score        INTEGER DEFAULT 0 CHECK (ai_score >= 0 AND ai_score <= 5),  -- AI 重要性评分，0-5 分
    dingtalk_sent   BOOLEAN DEFAULT FALSE,                           -- 是否已推送钉钉
    dingtalk_sent_at TIMESTAMPTZ,                                    -- 钉钉推送时间
    error_msg       TEXT                                             -- 处理过程中的错误信息
);

-- 关键词表：合规关键词，支持通过仪表盘动态管理
CREATE TABLE IF NOT EXISTS keywords (
    id          SERIAL PRIMARY KEY,                                  -- 自增主键
    keyword     VARCHAR(200) UNIQUE NOT NULL,                        -- 关键词文本（唯一）
    category    VARCHAR(50),                                         -- 所属分类：食品/化妆品/药品/医疗器械/综合
    enabled     BOOLEAN DEFAULT TRUE,                                -- 是否启用（禁用后不参与匹配）
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()                   -- 创建时间
);

-- 索引：优化常用查询性能
CREATE INDEX IF NOT EXISTS idx_articles_collected_at ON articles(collected_at);    -- 按采集时间查询（仪表盘排序）
CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source);                -- 按来源筛选
CREATE INDEX IF NOT EXISTS idx_articles_source_category ON articles(source_category);  -- 按采集板块筛选
CREATE INDEX IF NOT EXISTS idx_articles_ai_score ON articles(ai_score);            -- 按评分筛选（高分文章查询）
CREATE INDEX IF NOT EXISTS idx_articles_url_hash ON articles(url_hash);            -- URL 去重查询
CREATE INDEX IF NOT EXISTS idx_articles_ai_category ON articles(ai_category);     -- 按分类筛选
CREATE INDEX IF NOT EXISTS idx_articles_dingtalk_sent ON articles(dingtalk_sent);  -- 未推送文章查询

-- 初始关键词种子数据（系统启动时的默认合规关键词）
INSERT INTO keywords (keyword, category) VALUES
    ('处罚', '综合'),
    ('召回', '综合'),
    ('不合格', '综合'),
    ('抽检', '综合'),
    -- 综合
    ('大健康行业', '综合'),
    ('法规', '综合'),
    ('动态', '综合'),
    ('处罚信息', '综合'),
    ('健字号用品', '综合'),
    ('企业标准日用品', '综合'),
    ('宠物食品', '综合'),
    ('宠物药品', '综合'),
    ('宠物用品', '综合'),
    -- 食品
    ('特殊膳食', '食品'),
    ('保健食品', '食品'),
    ('功能性食品', '食品'),
    ('片剂', '食品'),
    ('粉剂', '食品'),
    ('膏滋', '食品'),
    ('膏剂', '食品'),
    ('液体', '食品'),
    ('凝胶糖果', '食品'),
    ('压片糖果', '食品'),
    ('固体饮料', '食品'),
    ('运动营养', '食品'),
    -- 医疗器械
    ('一类医疗器械', '医疗器械'),
    ('二类医疗器械', '医疗器械'),
    -- 化妆品
    ('普通化妆品', '化妆品'),
    ('特殊化妆品', '化妆品'),
    ('儿童化妆品', '化妆品'),
    ('彩妆', '化妆品')
ON CONFLICT (keyword) DO NOTHING;

-- 处理失败记录表：记录管道处理中各环节的失败信息，用于排查和重试
CREATE TABLE IF NOT EXISTS processing_failures (
    id          SERIAL PRIMARY KEY,                                  -- 自增主键
    article_id  INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,  -- 关联的文章 ID
    stage       VARCHAR(50) NOT NULL,                                -- 失败阶段：keyword_match/dify/storage
    error_msg   TEXT,                                                -- 错误详细信息
    retry_count INTEGER DEFAULT 0,                                  -- 已重试次数
    resolved    BOOLEAN DEFAULT FALSE,                              -- 是否已解决
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),                 -- 失败发生时间
    resolved_at TIMESTAMPTZ                                         -- 解决时间
);

-- 失败记录索引
CREATE INDEX IF NOT EXISTS idx_failures_unresolved
    ON processing_failures(resolved) WHERE resolved = FALSE;       -- 未解决的失败记录（快速查找待处理项）
CREATE INDEX IF NOT EXISTS idx_failures_article_id
    ON processing_failures(article_id);                             -- 按文章查失败记录

-- 调度配置表：存储定时采集的时间配置，单行设计（固定 id=1）
CREATE TABLE IF NOT EXISTS schedule_config (
    id          SERIAL PRIMARY KEY,                                  -- 配置 ID（固定为 1）
    hours       JSONB NOT NULL DEFAULT '[9,14,18]'::jsonb,         -- 采集时间点列表，如 [9,14,18] 表示 9:00/14:00/18:00
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,                      -- 是否启用定时采集
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()                  -- 最后更新时间
);

-- 默认调度配置种子数据：每天 9:00、14:00、18:00 自动采集
INSERT INTO schedule_config (id, hours, enabled)
VALUES (1, '[9,14,18]'::jsonb, TRUE)
ON CONFLICT (id) DO NOTHING;

-- 站点调度配置表：每个站点一行，存储独立 cron 调度策略
CREATE TABLE IF NOT EXISTS site_schedules (
    id              SERIAL PRIMARY KEY,
    site_name       VARCHAR(200) UNIQUE NOT NULL,  -- 站点名称（如 "国家药监局"），__default__ 为全局默认
    cron_expression VARCHAR(200) NOT NULL,         -- 标准 cron 表达式，如 "0 9,14,18 * * *"
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,  -- 是否启用
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_site_schedules_site_name ON site_schedules(site_name);

-- __default__ 全局默认调度种子数据（对应原来 [9,14,18] 小时配置）
INSERT INTO site_schedules (site_name, cron_expression, enabled)
VALUES ('__default__', '0 9,14,18 * * *', TRUE)
ON CONFLICT (site_name) DO NOTHING;
