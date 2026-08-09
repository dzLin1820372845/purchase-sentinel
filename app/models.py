"""数据模型定义"""
from datetime import datetime
from hashlib import md5
import re
from typing import Optional

from pydantic import BaseModel, Field, computed_field


class Article(BaseModel):
    """文章数据模型 — 所有采集策略返回此类型"""

    title: str = Field(..., min_length=1, description="文章标题")
    url: str = Field(..., min_length=1, description="文章URL")
    source: str = Field(..., description="来源名称，如 '国家药监局-化妆品监管工作'")
    source_category: Optional[str] = Field(default=None, description="采集板块，如 '化妆品-监管工作'")
    source_type: str = Field(
        default="website",
        pattern=r"^(wechat|website)$",
        description="来源类型：wechat=微信公众号, website=政府网站",
    )
    published_at: Optional[str] = Field(
        default=None, description="原始发布时间（字符串格式）"
    )
    content: Optional[str] = Field(
        default=None, description="正文全文（LLM 分析时取前 1500 字）"
    )
    matched_keywords: list[dict[str, str]] = Field(
        default_factory=list, description="命中的合规关键词列表，格式: [{keyword, category}]"
    )
    ai_summary: Optional[str] = Field(default=None, description="AI生成的50-100字摘要")
    ai_category: Optional[str] = Field(
        default=None, description="AI分类：食品/化妆品/药品/医疗器械/综合"
    )
    ai_score: int = Field(default=0, ge=0, le=5, description="AI重要性评分 0-5")
    dingtalk_sent: bool = Field(default=False, description="是否已推送钉钉")
    dingtalk_sent_at: Optional[str] = Field(default=None, description="钉钉推送时间")
    error_msg: Optional[str] = Field(default=None, description="处理过程中的错误信息")

    @computed_field
    @property
    def url_hash(self) -> str:
        """URL的MD5哈希，用于去重。协议归一化：http/https 视为同一URL。"""
        normalized = re.sub(r"^https?://", "https://", self.url)
        return md5(normalized.encode()).hexdigest()

    model_config = {"populate_by_name": True}
