#!/usr/bin/env bash
set -euo pipefail

# S3 存储桶连通性 / 中国大陆访问速度测试
# Bucket: ppt-deepagent (ap-southeast-2, 悉尼)
# 用法: bash docs/_chat/s3_speed_test.sh
# 测速后会清理测试对象与本地临时文件，可安全重复执行。

export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-AKIA6BTZCI325ZVVSG4C}"
export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-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}"
export AWS_REGION="ap-southeast-2"
BUCKET="ppt-deepagent"
KEY="speed-test-10m.bin"
PROXY="http://127.0.0.1:7897"
LOCAL_TMP="/tmp/s3-speed-10m.bin"

echo "=== 0. 生成 10MB 测试文件 ==="
dd if=/dev/zero of="$LOCAL_TMP" bs=1m count=10 2>/dev/null

echo "=== 1. 上传 10MB (经 AWS CLI, 用 IAM 用户凭证) ==="
aws s3api put-object \
  --bucket "$BUCKET" --key "$KEY" --body "$LOCAL_TMP" \
  --region "$AWS_REGION" >/dev/null
echo "上传完成"

echo "=== 2. 直连下载 10MB (中国大陆直连悉尼) ==="
curl -s -o /dev/null -w "吞吐=%{speed_download} B/s  建连=%{time_connect}s  总耗时=%{time_total}s\n" \
  "https://${BUCKET}.s3.${AWS_REGION}.amazonaws.com/${KEY}"

echo "=== 3. 经 Clash 代理(127.0.0.1:7897) 下载 10MB ==="
curl -s -o /dev/null -w "吞吐=%{speed_download} B/s  建连=%{time_connect}s  总耗时=%{time_total}s\n" \
  -x "$PROXY" "https://${BUCKET}.s3.${AWS_REGION}.amazonaws.com/${KEY}"

echo "=== 4. 清理 ==="
aws s3api delete-object \
  --bucket "$BUCKET" --key "$KEY" \
  --region "$AWS_REGION" >/dev/null
rm -f "$LOCAL_TMP"
echo "测试对象与本地临时文件已清理"
