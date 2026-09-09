#!/usr/bin/env bash
# ============================================================
#  monitor-platform · MySQL 每日自动备份
#  Created: 2026-09-09
#  用法: /opt/monitor-platform/tools/backup_mysql.sh
#  crontab: 10 3 * * * /opt/monitor-platform/tools/backup_mysql.sh
# ============================================================
# 设计要点:
#   1. 密码从 .env 读取，不硬编码；每次运行生成临时 cnf 用完即删
#   2. --single-transaction 保证 InnoDB 一致性且不锁表
#   3. 产物 < 1KB 视为失败（防止导出空文件被当成成功）
#   4. 同一天多次执行不会互相覆盖（文件名带 HHMMSS）
#   5. 按 KEEP_DAYS 滚动删除，避免占满磁盘
# ============================================================
set -uo pipefail

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH

ENV_FILE="/opt/monitor-platform/.env"
CONTAINER="monitor-mysql"
MYSQL_USER="root"
BACKUP_ROOT="/opt/monitor-platform/backups/mysql"
KEEP_DAYS=14
LOG="${BACKUP_ROOT}/backup.log"

mkdir -p "${BACKUP_ROOT}"
log() { echo "[$(date '+%F %T')] $*" >>"${LOG}"; }

# ---- 读配置 ----
DB="$(sed -n 's/^MYSQL_DATABASE=//p' "${ENV_FILE}" 2>/dev/null | tail -1 | tr -d "\"'" | tr -d '[:space:]')"
PASS="$(sed -n 's/^MYSQL_ROOT_PASSWORD=//p' "${ENV_FILE}" 2>/dev/null | tail -1 | tr -d "\"'")"
DB="${DB:-monitor_platform}"
if [[ -z "${PASS}" ]]; then
  log "FAIL: 无法从 ${ENV_FILE} 读取 MYSQL_ROOT_PASSWORD"
  exit 1
fi

# ---- 容器存活检查 ----
if ! /usr/bin/docker ps --format '{{.Names}}' | grep -qx "${CONTAINER}"; then
  log "FAIL: 容器 ${CONTAINER} 未运行"
  exit 1
fi

CNF="/tmp/.bk_mysql_$$.cnf"
cleanup() { /usr/bin/docker exec "${CONTAINER}" rm -f "${CNF}" >/dev/null 2>&1; }
trap cleanup EXIT

{
  echo "[client]"
  echo "user=${MYSQL_USER}"
  echo "password=${PASS}"
} | /usr/bin/docker exec -i "${CONTAINER}" sh -c "cat > ${CNF} && chmod 600 ${CNF}"
if [[ $? -ne 0 ]]; then
  log "FAIL: 无法写入容器内临时凭据文件"
  exit 1
fi

TS="$(date +%Y%m%d-%H%M%S)"
OUT="${BACKUP_ROOT}/${DB}_${TS}.sql.gz"

# ---- 导出 ----
if /usr/bin/docker exec "${CONTAINER}" mysqldump \
    --defaults-extra-file="${CNF}" \
    --single-transaction \
    --routines --triggers \
    --default-character-set=utf8mb4 \
    "${DB}" 2>>"${LOG}" | /usr/bin/gzip -9 >"${OUT}"; then
  :
else
  log "FAIL: mysqldump 非零退出，产物已丢弃"
  rm -f "${OUT}"
  exit 1
fi

SZ="$(/usr/bin/stat -c%s "${OUT}" 2>/dev/null || echo 0)"
if [[ "${SZ}" -lt 1024 ]]; then
  log "FAIL: 产物异常偏小 (${SZ} B): $(basename "${OUT}")"
  rm -f "${OUT}"
  exit 1
fi

# ---- 滚动清理 ----
/usr/bin/find "${BACKUP_ROOT}" -type f -name "${DB}_*.sql.gz" -mtime +${KEEP_DAYS} -delete 2>>"${LOG}"
CNT="$(/usr/bin/find "${BACKUP_ROOT}" -type f -name "${DB}_*.sql.gz" 2>/dev/null | wc -l | tr -d ' ')"
USED="$(/usr/bin/du -sh "${BACKUP_ROOT}" 2>/dev/null | cut -f1)"

log "OK $(basename "${OUT}") ${SZ}B | 留存 ${CNT} 份 / ${KEEP_DAYS} 天 | 占用 ${USED}"
exit 0
