#!/bin/sh
# 备份状态库：把整个数据库（public 和 dbos 两个 schema）用 pg_dump 的 custom 格式导出到目录 $1，
# 文件名是开始备份的 UTC 时刻；成功以后只留最新的 KEEP 份。失败时不删任何旧备份。
# 由 tradingagentspro-backup.service 以 root 运行，见 deploy/README.md 和 add-deployment 的 design D8。
set -eu

KEEP=14 # 备份保留份数
dir=$1

cd "$(dirname "$0")" # compose.yaml 所在的目录
install -d -m 700 "$dir"
# 先写到 incomplete，成功后才改名为 .dump：失败时留下的不会被当作备份，下次覆盖
docker compose exec -T postgres \
    pg_dump --username=tradingagentspro --dbname=tradingagentspro --format=custom \
    >"$dir/incomplete"
mv "$dir/incomplete" "$dir/$(date -u +%Y%m%dT%H%M%SZ).dump"

# 文件名是 UTC 时刻，按名字排序就是按时间排序，最旧的在前
set -- "$dir"/*.dump
while [ "$#" -gt "$KEEP" ]; do
    rm -- "$1"
    shift
done
