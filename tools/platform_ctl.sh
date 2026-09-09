#!/bin/bash
# ============================================================
# 监控平台 一键管控脚本（2026-09-09）
#   platform_ctl.sh status   查看各容器状态
#   platform_ctl.sh verify   服务体检（前端/后端/MySQL/InfluxDB/降采样任务）
#   platform_ctl.sh start    冷启动后使用（compose 按依赖顺序拉起 + 体检）
#   platform_ctl.sh restart  按依赖顺序滚动重启（不丢数据，不动镜像）
#   platform_ctl.sh restart <service>  只重启单个服务（mysql/influxdb/backend/frontend/collector）
# ⛔ 永远不要执行: docker compose down -v （会删除 mysql/influxdb 数据卷！）
# ============================================================
set -u
cd /opt/monitor-platform
SVCS_ORDER="mysql influxdb backend frontend collector"

c_green='\033[0;32m'; c_red='\033[0;31m'; c_yel='\033[0;33m'; c_end='\033[0m'
ok()   { echo -e "${c_green}[OK]${c_end}   $1"; }
bad()  { echo -e "${c_red}[FAIL]${c_end} $1"; FAIL=1; }
warn() { echo -e "${c_yel}[WARN]${c_end} $1"; }

do_status() {
  docker compose ps --format 'table {{.Name}}\t{{.Status}}'
}

do_verify() {
  FAIL=0
  # 1. 容器状态
  for c in monitor-mysql monitor-influxdb monitor-backend monitor-frontend monitor-collector; do
    st=$(docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}-{{end}}' "$c" 2>/dev/null || echo "missing -")
    running=$(echo "$st" | awk '{print $1}')
    if [ "$running" = "running" ]; then ok "$c $st"; else bad "$c 未运行 ($st)"; fi
  done
  # 2. HTTP 层
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1/ || echo 000)
  [ "$code" = "200" ] && ok "前端 http://IP/ -> 200" || bad "前端 HTTP=$code"
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1/api/auth/me || echo 000)
  [ "$code" = "401" ] && ok "后端 API 裸调 -> 401（鉴权正常）" || bad "后端 API HTTP=$code"
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1/custom/mc-custom.js || echo 000)
  [ "$code" = "200" ] && ok "定制资产 /custom/mc-custom.js -> 200" || bad "定制资产 HTTP=$code"
  # 3. MySQL 数据在位
  n=$(docker exec monitor-mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=\"monitor_platform\""' 2>/dev/null || echo 0)
  [ "$n" -ge 15 ] && ok "monitor_platform 表数量=$n" || bad "monitor_platform 表数量=$n（应 ~20）"
  # 4. InfluxDB 降采样任务
  IT=${INFLUXDB_TOKEN:-}; [ -z "$IT" ] && [ -f /opt/monitor-platform/.env ] && IT=$(sed -n 's/^INFLUXDB_TOKEN=//p' /opt/monitor-platform/.env | tail -1 | tr -d "'")
  t=$(docker exec monitor-influxdb influx task list -t "$IT" 2>/dev/null | grep -c downsample-hourly)
  [ "$t" -ge 1 ] && ok "InfluxDB 降采样任务在位" || warn "InfluxDB 降采样任务未查到（可忽略，不影响主流程）"
  echo
  if [ "$FAIL" = "0" ]; then ok "=== 体检全部通过 ==="; else bad "=== 体检有失败项，见上 ==="; fi
  return $FAIL
}

wait_healthy() { # $1=容器名 $2=超时秒
  local i=0
  while [ $i -lt $2 ]; do
    st=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$1" 2>/dev/null)
    run=$(docker inspect -f '{{.State.Status}}' "$1" 2>/dev/null)
    { [ "$st" = "healthy" ] || { [ "$st" = "none" ] && [ "$run" = "running" ]; }; } && return 0
    sleep 2; i=$((i+2))
  done
  return 1
}

do_start() {
  echo "== 冷启动：compose 按依赖顺序拉起（mysql→influxdb→backend→frontend→collector）=="
  docker compose up -d
  for c in monitor-mysql monitor-influxdb monitor-backend monitor-frontend monitor-collector; do
    if wait_healthy "$c" 90; then ok "$c 已就绪"; else bad "$c 90s 内未就绪（docker logs $c 查看）"; fi
  done
  do_verify
}

do_restart() {
  if [ "$1" = "all" ] || [ -z "${1:-}" ]; then
    for s in $SVCS_ORDER; do
      echo "== 重启 $s =="
      docker compose restart "$s"
      wait_healthy "monitor-$s" 60 && ok "$s 就绪" || bad "$s 未在 60s 内就绪"
    done
  else
    echo "== 重启 $1 =="
    docker compose restart "$1"
    wait_healthy "monitor-$1" 90 && ok "$1 就绪" || bad "$1 未就绪"
  fi
  do_verify
}

case "${1:-status}" in
  status)  do_status ;;
  verify)  do_verify ;;
  start)   do_start ;;
  restart) shift; do_restart "$@" ;;
  *) echo "用法: platform_ctl.sh {status|verify|start|restart [service|all]}"; exit 1 ;;
esac
