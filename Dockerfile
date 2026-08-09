# ============================================================
# 采购舆情检测系统 — 多阶段 Docker 构建
# Stage 1 (builder): 编译 C 扩展依赖 (pyahocorasick)
# Stage 2 (runtime): 最小化运行时镜像
# ============================================================

# --- Stage 1: builder ---
FROM python:3.11-slim AS builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# --- Stage 2: runtime ---
FROM python:3.11-slim

WORKDIR /app

# 从 builder 阶段拷贝已编译的依赖
COPY --from=builder /install /usr/local

# 拷贝应用代码
COPY app/ ./app/
COPY sites/ ./sites/
COPY run.py .
COPY config.yaml .

# 确保日志实时输出（Docker 环境必需）
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["python", "run.py"]
