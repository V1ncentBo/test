#!/bin/bash
# ============================================================
# 监控平台 自愈看门狗 (monitor-watchdog)
# ------------------------------------------------------------
# 覆盖四类故障：
#   ① 容器进程退出/崩溃 -> docker `restart: always` 原生拉起（本脚本兜底核对）
#   ② 容器假死无响应    -> restart 策略无能为力（进程没退出），本脚本计数后 docker restart
#   ③ 被人工 docker stop/kill -> restart 策略按设计不生效，本脚本计数后拉起
#   ④ Docker 守护进程挂死 -> 所有 docker 命令失败，本脚本计数后 systemctl restart docker
# 触发方式：systemd timer（monitor-watchdog.timer）每 60s 一次
#
# 环境变量：
#   WD_FAIL_THRESHOLD  连续失败多少次才动手（默认 3 --> 约 3 分钟）
#   WD_COOLDOWN        两次自动重启最短间隔秒数，防重启风暴（默认 300）
#   WD_DRY_RUN=1       只判定不重启（演练）
# 维护开关：
#   touch /opt/monitor-platform/tools/.watchdog.pause   --> 暂停一切自愈动作
#   rm    /opt/monitor-platform/tools/.watchdog.pause   --> 恢复
# 退出码恒 0（不算 systemd 失败），诊断信息全写日志。
# ============================================================
set -u

BASE=/opt/monitor-platform
LOG="$BASE/logs/watchdog.log"
STATE="$BASE/tools/.watchdog.failcount"
STAMP="$BASE/tools/.watchdog.lastrestart"
PAUSE="$BASE/tools/.watchdog.pause"

FAIL_THRESHOLD="${WD_FAIL_THRESHOLD:-3}"
COOLDOWN="${WD_COOLDOWN:-300}"
DRY_RUN="${WD_DRY_RUN:-0}"

mkdir -p "$(dirname "$LOG")" 2>/dev/null

# 日志超过 5MB 滚动一次
if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG" 2>/dev/null || echo 0)" -gt 5242880 ]; then
  mv -f "$LOG" "$LOG.1" 2>/dev/null
fi

log() { echo "[$(date '+%F %T')] $*" >> "$LOG"; }

# ---------------- 维护开关 ----------------
if [ -f "$PAUSE" ]; then
  # 只在计数非 0 时记一笔，避免每分钟刷屏
  if [ "$(cat "$STATE" 2>/dev/null || echo 0)" != "0" ]; then
    log "维护暂停中（$PAUSE 存在），跳过自愈并清零计数"
    echo 0 > "$STATE"
  fi
  exit 0
fi

fail_count=0
reasons=""
targets=""

# 容器是否 running（存在健康检查时，unhealthy 也算异常）
check_container() {  # $1=容器名  返回 0=正常
  local st h
  st=$(docker inspect -f '{{.State.Status}}' "$1" 2>/dev/null || echo missing)
  [ "$st" = "running" ] || { reasons="$reasons $1:$st"; return 1; }
  h=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$1" 2>/dev/null || echo none)
  [ "$h" = "unhealthy" ] && { reasons="$reasons $1:unhealthy"; return 1; }
  return 0
}

# HTTP 探活
check_url() {  # $1=url $2=期望码 $3=标签  返回 0=正常
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 6 "$1" 2>/dev/null || echo 000)
  [ "$code" = "$2" ] || { reasons="$reasons $3:http$code"; return 1; }
  return 0
}

# ---------------- 先看 Docker 守护进程本身 ----------------
docker_ok=1
if ! timeout 10 docker info >/dev/null 2>&1; then
  docker_ok=0
  fail_count=$((fail_count+1))
  reasons="$reasons docker_daemon_unreachable"
fi

# ---------------- 容器与接口检查（守护进程可用时才做）----------------
if [ "$docker_ok" -eq 1 ]; then
  # 后端：容器 + 健康接口（任一失败都重启 backend）
  if ! check_container monitor-backend || ! check_url "http://127.0.0.1/api/health" 200 backend_health; then
    fail_count=$((fail_count+1)); targets="$targets monitor-backend"
  fi
  # 前端：容器 + 首页
  if ! check_container monitor-frontend || ! check_url "http://127.0.0.1/" 200 frontend_root; then
    fail_count=$((fail_count+1)); targets="$targets monitor-frontend"
  fi
  # 数据层与采集器：只看容器状态
  for c in monitor-mysql monitor-influxdb monitor-collector; do
    if ! check_container "$c"; then fail_count=$((fail_count+1)); targets="$targets $c"; fi
  done
fi

# ---------------- 健康：清零计数 ----------------
if [ "$fail_count" -eq 0 ]; then
  [ "$(cat "$STATE" 2>/dev/null || echo 0)" != "0" ] && log "恢复正常，失败计数清零"
  echo 0 > "$STATE"
  exit 0
fi

# ---------------- 失败：累加计数 ----------------
n=$(cat "$STATE" 2>/dev/null || echo 0)
case "$n" in ''|*[!0-9]*) n=0;; esac
n=$((n+1)); echo "$n" > "$STATE"
log "检测异常 ${n}/${FAIL_THRESHOLD}：${reasons}"

[ "$n" -lt "$FAIL_THRESHOLD" ] && exit 0

# ---------------- 冷却检查 ----------------
# ⚠ 冷却只约束"同一目标集合"：否则一次无关的早期重启会把另一个组件的新故障挡在门外。
# 实测踩过：collector 的故障被 262s 前的 backend 重启冷却挡住，白等 60s 才自愈。
now=$(date +%s)
now_targets=$(echo "${targets:-docker-daemon}" | tr -s ' ' | sed 's/^ //; s/ $//')
last_info=$(cat "$STAMP" 2>/dev/null || echo "")
last_ts="${last_info%%|*}"
last_targets="${last_info#*|}"
case "$last_ts" in ''|*[!0-9]*) last_ts=0;; esac
# 兼容旧格式（只有时间戳、没有目标）
case "$last_info" in *"|"*) : ;; *) last_targets="";; esac
if [ "$now_targets" = "$last_targets" ] && [ $((now - last_ts)) -lt "$COOLDOWN" ]; then
  log "同一目标处于冷却期（$((now - last_ts))s < ${COOLDOWN}s），本次跳过：$now_targets"
  exit 0
fi

if [ "$DRY_RUN" = "1" ]; then
  if [ "$docker_ok" -eq 0 ]; then
    log "[DRY-RUN] 本应执行：systemctl restart docker"
  else
    log "[DRY-RUN] 本应执行：docker restart$targets"
  fi
  exit 0
fi

# ---------------- 自愈 ----------------
echo "$now|$now_targets" > "$STAMP"

if [ "$docker_ok" -eq 0 ]; then
  log "==> Docker 守护进程不可达，执行：systemctl restart docker"
  systemctl restart docker >> "$LOG" 2>&1
  sleep 30
else
  log "==> 触发自愈：docker restart$targets"
  # shellcheck disable=SC2086
  docker restart $targets >> "$LOG" 2>&1
  sleep 20
fi

echo 0 > "$STATE"
if curl -s -o /dev/null -w '%{http_code}' --max-time 8 http://127.0.0.1/api/health 2>/dev/null | grep -q '^200$'; then
  log "自愈成功，/api/health 已恢复 200"
else
  log "⚠ 自愈后 /api/health 仍不健康 —— 请人工介入（可跑 platform_ctl.sh verify）"
fi
exit 0
