#!/usr/bin/env bash
# alert/deploy.sh — 部署 Stock Alert 系统至 Google Cloud Functions (Gen2) 与 Cloud Scheduler
# 复用 serverless 模式，运行在 GCP Always Free 额度内
set -euo pipefail

PROJECT_ID="${GCP_PROJECT:-stock-alert}"
REGION="${GCP_REGION:-asia-northeast1}"
TIMEZONE="Asia/Tokyo"

echo "============================================================"
echo "🚀 部署 Stock Alert 股价提醒系统"
echo "Project:  ${PROJECT_ID}"
echo "Region:   ${REGION}"
echo "Timezone: ${TIMEZONE}"
echo "============================================================"

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${DIR}"

# 1. 检查 gcloud 配置
if ! command -v gcloud &> /dev/null; then
    echo "❌ 错误: 未安装 gcloud CLI，请先安装并配置好认证。"
    exit 1
fi

gcloud config set project "${PROJECT_ID}"

# 2. 部署 Cloud Function: stock-monitor (开盘窗口每30分拉价监控)
echo "📦 正在部署 Cloud Function: stock-monitor..."
gcloud functions deploy stock-monitor \
    --gen2 \
    --runtime=python311 \
    --region="${REGION}" \
    --source=. \
    --entry-point=monitor \
    --trigger-http \
    --allow-unauthenticated \
    --memory=512MiB \
    --timeout=300s \
    --set-secrets="DISCORD_WEBHOOK=stock-discord-webhook:latest"

MONITOR_URL=$(gcloud functions describe stock-monitor --gen2 --region="${REGION}" --format='value(serviceConfig.uri)')
echo "✅ stock-monitor 部署成功: ${MONITOR_URL}"

# 3. 部署 Cloud Function: stock-close (收盘总结)
echo "📦 正在部署 Cloud Function: stock-close..."
gcloud functions deploy stock-close \
    --gen2 \
    --runtime=python311 \
    --region="${REGION}" \
    --source=. \
    --entry-point=close \
    --trigger-http \
    --allow-unauthenticated \
    --memory=512MiB \
    --timeout=300s \
    --set-secrets="DISCORD_WEBHOOK=stock-discord-webhook:latest"

CLOSE_URL=$(gcloud functions describe stock-close --gen2 --region="${REGION}" --format='value(serviceConfig.uri)')
echo "✅ stock-close 部署成功: ${CLOSE_URL}"

# 4. 部署 Cloud Function: stock-gen-targets (周更启发式目标价)
echo "📦 正在部署 Cloud Function: stock-gen-targets..."
gcloud functions deploy stock-gen-targets \
    --gen2 \
    --runtime=python311 \
    --region="${REGION}" \
    --source=. \
    --entry-point=gen_targets_http \
    --trigger-http \
    --allow-unauthenticated \
    --memory=512MiB \
    --timeout=300s

GEN_TARGETS_URL=$(gcloud functions describe stock-gen-targets --gen2 --region="${REGION}" --format='value(serviceConfig.uri)')
echo "✅ stock-gen-targets 部署成功: ${GEN_TARGETS_URL}"

# 5. 配置 Cloud Scheduler Jobs
echo "⏰ 配置 Cloud Scheduler 定时任务..."

# 5.1 窗口监控 Job: 周一至周五 21:00-23:59 每 30 分钟触发一次
if gcloud scheduler jobs describe monitor-window --location="${REGION}" &> /dev/null; then
    echo "更新 Scheduler Job: monitor-window"
    gcloud scheduler jobs update http monitor-window \
        --location="${REGION}" \
        --schedule="*/30 21-23 * * 1-5" \
        --time-zone="${TIMEZONE}" \
        --uri="${MONITOR_URL}" \
        --http-method=GET
else
    echo "创建 Scheduler Job: monitor-window"
    gcloud scheduler jobs create http monitor-window \
        --location="${REGION}" \
        --schedule="*/30 21-23 * * 1-5" \
        --time-zone="${TIMEZONE}" \
        --uri="${MONITOR_URL}" \
        --http-method=GET
fi

# 5.2 收盘总结 Job: 周二至周六 05:00 及 06:00 触发（覆盖冬夏令时）
if gcloud scheduler jobs describe monitor-close --location="${REGION}" &> /dev/null; then
    echo "更新 Scheduler Job: monitor-close"
    gcloud scheduler jobs update http monitor-close \
        --location="${REGION}" \
        --schedule="0 5,6 * * 2-6" \
        --time-zone="${TIMEZONE}" \
        --uri="${CLOSE_URL}" \
        --http-method=GET
else
    echo "创建 Scheduler Job: monitor-close"
    gcloud scheduler jobs create http monitor-close \
        --location="${REGION}" \
        --schedule="0 5,6 * * 2-6" \
        --time-zone="${TIMEZONE}" \
        --uri="${CLOSE_URL}" \
        --http-method=GET
fi

# 5.3 周更目标价 Job: 每周六 08:00 JST 触发
if gcloud scheduler jobs describe gen-targets --location="${REGION}" &> /dev/null; then
    echo "更新 Scheduler Job: gen-targets"
    gcloud scheduler jobs update http gen-targets \
        --location="${REGION}" \
        --schedule="0 8 * * 6" \
        --time-zone="${TIMEZONE}" \
        --uri="${GEN_TARGETS_URL}" \
        --http-method=GET
else
    echo "创建 Scheduler Job: gen-targets"
    gcloud scheduler jobs create http gen-targets \
        --location="${REGION}" \
        --schedule="0 8 * * 6" \
        --time-zone="${TIMEZONE}" \
        --uri="${GEN_TARGETS_URL}" \
        --http-method=GET
fi

echo "============================================================"
echo "🎉 所有函数与调度器已完成配置与部署！"
echo "============================================================"
