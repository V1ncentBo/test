#!/bin/bash
# =============================================================================
#  Node Exporter 一键安装脚本（监控平台配套）
#  用途：在目标 Linux 服务器上安装 node_exporter，放行 9100 防火墙，
#        并把本机注册到监控平台，使其约 30 秒内被自动采集为「在线」。
#
#  ★ 默认零输入：脚本优先调用平台「免登录注册接口」(/api/register-node)
#    自动把本机加入监控，无需任何账号密码。仅当平台在后端设置了
#    NODE_REGISTER_SECRET 注册密钥时，才需要用 --secret 传入。
#
#  用法（任选其一）：
#    # 方式 A：零输入（推荐）— 拉取并执行，自动免登录注册
#    curl -sL http://192.168.1.66/install-node-exporter.sh -o /tmp/install_ne.sh
#    bash /tmp/install_ne.sh
#
#    # 方式 B：平台启用密钥保护时，用 --secret 传入注册密钥
#    bash /tmp/install_ne.sh --secret '你的NODE_REGISTER_SECRET'
#
#    # 方式 C：非交互回退凭据模式（免登录不可用时的备选）
#    MP_USER=admin MP_PASS='你的密码' bash /tmp/install_ne.sh
#
#    # 方式 D：通过命令行参数
#    bash /tmp/install_ne.sh --user admin --pass '你的密码' --group 机房A
#
#  可选参数：
#    --user <账号>     平台管理员账号（也可用 MP_USER 环境变量）
#    --pass <密码>     平台管理员密码（也可用 MP_PASS 环境变量）
#    --secret <密钥>   平台注册密钥（也可用 MP_SECRET 环境变量）
#    --name <名称>     平台中显示的设备名称（默认取本机 hostname）
#    --group <分组>    平台中的设备分组（默认 default）
#    --ip <IP>         注册到平台的 IP（默认取本机主网卡 IP）
#    --host <IP>       监控服务器地址（默认 192.168.1.66）
#    -h, --help        显示帮助
#
#  说明：若免登录接口不可用（如平台未开启或网络隔离），脚本会自动回退到
#        凭据模式（交互输入或由环境变量/参数提供账号密码）；若仍失败，
#        仍会装好 node_exporter 并放行 9100，并在末尾提示手动注册。
# =============================================================================

set -u

# ----------------------------- 可配置项 --------------------------------------
NE_VERSION="1.8.2"
INSTALL_DIR="/usr/local/bin"
SERVICE_FILE="/etc/systemd/system/node_exporter.service"
MP_HOST="${MP_HOST:-192.168.1.66}"
MP_PROTO="http"
MP_API=""

USER_ARG="" PASS_ARG="" NAME_ARG="" GROUP_ARG="" IP_ARG="" SECRET_ARG=""

# ----------------------------- 参数解析 --------------------------------------
usage() {
  grep '^#' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    --user)  USER_ARG="$2"; shift 2;;
    --pass)  PASS_ARG="$2"; shift 2;;
    --secret) SECRET_ARG="$2"; shift 2;;
    --name)  NAME_ARG="$2"; shift 2;;
    --group) GROUP_ARG="$2"; shift 2;;
    --ip)    IP_ARG="$2"; shift 2;;
    --host)  MP_HOST="$2"; shift 2;;
    -h|--help) usage; exit 0;;
    *) echo "未知参数: $1"; usage; exit 1;;
  esac
done

# ----------------------------- 工具函数 --------------------------------------
log()  { echo -e "\033[32m[INFO]\033[0m $*"; }
warn() { echo -e "\033[33m[WARN]\033[0m $*"; }
err()  { echo -e "\033[31m[ERR ]\033[0m $*"; }

detect_ip() {
  local ip=""
  ip=$(hostname -I 2>/dev/null | awk '{print $1}')
  if [ -z "$ip" ]; then
    ip=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}')
  fi
  echo "$ip"
}

# 自动探测可用的 API 端口（优先 8000，回退 80；两者均不可达则用 8000）
probe_api() {
  for p in 8000 80; do
    if curl -fsS --connect-timeout 5 "${MP_PROTO}://${MP_HOST}:${p}/api/health" >/dev/null 2>&1; then
      MP_API="${MP_PROTO}://${MP_HOST}:${p}/api"
      return 0
    fi
  done
  MP_API="${MP_PROTO}://${MP_HOST}:8000/api"
  return 1
}

# 下载并安装 node_exporter 二进制（多镜像兜底 + 从监控服务器 scp 兜底）
install_binary() {
  local base="https://github.com/prometheus/node_exporter/releases/download/v${NE_VERSION}/node_exporter-${NE_VERSION}.linux-amd64.tar.gz"
  local mirrors=(
    "https://mirror.ghproxy.com/${base}"
    "https://ghproxy.net/${base}"
    "${base}"
  )
  for m in "${mirrors[@]}"; do
    log "尝试下载: $m"
    if curl -fsSL --connect-timeout 15 --max-time 300 -o /tmp/ne.tar.gz "$m" 2>/dev/null; then
      log "下载成功，开始解压安装..."
      tar xzf /tmp/ne.tar.gz -C /tmp/ || { err "解压失败"; return 1; }
      local src
      src=$(ls -d /tmp/node_exporter-* 2>/dev/null | head -1)
      [ -z "$src" ] && { err "未找到解压目录"; return 1; }
      mv "${src}/node_exporter" "${INSTALL_DIR}/"
      chmod +x "${INSTALL_DIR}/node_exporter"
      rm -rf /tmp/ne.tar.gz "${src}"
      return 0
    fi
    warn "该源失败，尝试下一个..."
  done

  # 所有镜像失败 → 尝试从监控服务器直接复制（需已配置 SSH 免密，否则快速跳过）
  warn "所有下载源失败，尝试从监控服务器 ${MP_HOST} 复制二进制..."
  if scp -o StrictHostKeyChecking=no -o BatchMode=yes -o ConnectTimeout=15 \
        "root@${MP_HOST}:/usr/local/bin/node_exporter" /tmp/ne_bin 2>/dev/null; then
    mv /tmp/ne_bin "${INSTALL_DIR}/node_exporter"
    chmod +x "${INSTALL_DIR}/node_exporter"
    log "已从监控服务器复制 node_exporter"
    return 0
  fi

  err "node_exporter 下载/复制均失败，请手动下载后重跑本脚本。"
  return 1
}

# 注册为 systemd 服务；无 systemd 时降级为 nohup 后台运行
setup_service() {
  if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
    log "写入 systemd 服务并启动..."
    cat > "$SERVICE_FILE" <<'EOF'
[Unit]
Description=Node Exporter
After=network.target

[Service]
ExecStart=/usr/local/bin/node_exporter --web.listen-address=:9100
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable --now node_exporter
    sleep 2
    if systemctl is-active --quiet node_exporter; then
      log "node_exporter 服务已运行 (systemd)"
    else
      warn "systemd 服务未正常启动，请检查: systemctl status node_exporter"
    fi
  else
    warn "未检测到 systemd，使用 nohup 后台运行"
    pkill -f '/usr/local/bin/node_exporter' 2>/dev/null || true
    nohup "${INSTALL_DIR}/node_exporter" --web.listen-address=:9100 \
      >/var/log/node_exporter.log 2>&1 &
    sleep 2
    log "node_exporter 已在后台启动 (PID $!)"
  fi
}

# 放行 9100 端口（自动识别防火墙类型）
open_firewall() {
  log "配置防火墙放行 9100..."
  if command -v firewall-cmd >/dev/null 2>&1; then
    firewall-cmd --add-port=9100/tcp --permanent 2>/dev/null
    firewall-cmd --reload 2>/dev/null
    log "firewalld: 已永久放行 9100"
  elif command -v ufw >/dev/null 2>&1; then
    ufw allow 9100/tcp 2>/dev/null
    log "ufw: 已放行 9100"
  elif command -v iptables >/dev/null 2>&1; then
    iptables -I INPUT -p tcp --dport 9100 -j ACCEPT 2>/dev/null
    log "iptables: 已放行 9100（重启后失效，建议持久化）"
  else
    warn "未检测到常见防火墙工具，跳过；若平台无法拉取数据请手动放行 9100"
  fi
}

# 免登录注册（优先）：调用平台 /api/register-node，零输入
register_open() {
  local ip="$1" name="$2" group="$3" secret="$4"
  probe_api || { warn "无法连接平台 API，跳过免登录注册"; return 1; }

  local secret_esc=""
  if [ -n "$secret" ]; then
    secret_esc=$(printf '%s' "$secret" | sed 's/\\/\\\\/g; s/"/\\"/g')
  fi
  local body="{\"ip\":\"${ip}\",\"name\":\"${name}\",\"group_name\":\"${group}\"}"
  if [ -n "$secret_esc" ]; then
    body="{\"ip\":\"${ip}\",\"name\":\"${name}\",\"group_name\":\"${group}\",\"secret\":\"${secret_esc}\"}"
  fi

  log "使用平台免登录注册接口: ${MP_API}/register-node"
  local http_code
  http_code=$(curl -sS --connect-timeout 10 --max-time 30 -o /tmp/reg_resp.json -w '%{http_code}' \
    -X POST "${MP_API}/register-node" -H 'Content-Type: application/json' -d "$body" 2>/dev/null)
  http_code="${http_code:-000}"

  if [ "$http_code" = "200" ] && grep -q '"ok"' /tmp/reg_resp.json 2>/dev/null; then
    log "免登录注册成功: ${name} (${ip})，约 30 秒后自动在线"
    return 0
  fi
  if [ "$http_code" = "403" ]; then
    warn "平台启用了注册密钥保护，请加 --secret '密钥' 或 MP_SECRET=密钥 传入后重跑"
  fi
  return 1
}

# 凭据模式注册（回退）：登录平台后调用 /api/machines
register_device() {
  local ip="$1" name="$2" group="$3"
  local user="$USER_ARG" pass="$PASS_ARG"

  if [ -z "$user" ] && [ -n "${MP_USER:-}" ]; then
    user="$MP_USER"; pass="${MP_PASS:-}"
  fi

  if [ -z "$user" ]; then
    read -r -p "请输入监控平台管理员账号: " user
    read -r -s -p "请输入密码: " pass
    echo
  fi

  [ -z "$user" ] && { warn "未提供平台凭据，跳过自动注册（详见末尾手动步骤）"; return 1; }

  # 转义密码中的双引号与反斜杠，避免破坏 JSON
  local pass_esc
  pass_esc=$(printf '%s' "$pass" | sed 's/\\/\\\\/g; s/"/\\"/g')

  probe_api
  log "使用平台 API: $MP_API"

  local login_resp
  login_resp=$(curl -fsS --connect-timeout 10 --max-time 30 -X POST "${MP_API}/auth/login" \
    -H 'Content-Type: application/json' \
    -d "{\"username\":\"${user}\",\"password\":\"${pass_esc}\"}" 2>/dev/null)
  if [ -z "$login_resp" ]; then
    warn "登录失败（账号/密码错误或平台不可达），跳过自动注册"
    return 1
  fi

  local token
  token=$(echo "$login_resp" | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')
  if [ -z "$token" ]; then
    warn "未获取到 token，跳过自动注册"
    return 1
  fi
  log "登录成功"

  # 检查 IP 是否已存在，避免重复注册
  local list_resp
  list_resp=$(curl -fsS --connect-timeout 10 --max-time 30 "${MP_API}/machines" \
    -H "Authorization: Bearer ${token}" 2>/dev/null) || list_resp="[]"
  if echo "$list_resp" | grep -q "\"ip\":\"${ip}\""; then
    log "设备 IP ${ip} 已在平台中，跳过注册"
    return 0
  fi

  local name_esc
  name_esc=$(printf '%s' "$name" | sed 's/\\/\\\\/g; s/"/\\"/g')
  local group_esc
  group_esc=$(printf '%s' "$group" | sed 's/\\/\\\\/g; s/"/\\"/g')

  local create_resp
  create_resp=$(curl -fsS --connect-timeout 10 --max-time 30 -X POST "${MP_API}/machines" \
    -H "Authorization: Bearer ${token}" -H 'Content-Type: application/json' \
    -d "{\"name\":\"${name_esc}\",\"ip\":\"${ip}\",\"group_name\":\"${group_esc}\",\"monitor_enabled\":true}" \
    2>/dev/null)
  if [ -n "$create_resp" ]; then
    log "设备已注册到平台: ${name} (${ip})，约 30 秒后自动转为在线"
    return 0
  else
    warn "注册请求未返回成功，可稍后在「设备管理」手动添加 IP ${ip}"
    return 1
  fi
}

# ----------------------------- 主流程 ----------------------------------------
echo "============================================================"
echo " Node Exporter 一键安装（监控平台配套 · 默认免登录零输入）"
echo "============================================================"

IP="${IP_ARG:-$(detect_ip)}"
NAME="${NAME_ARG:-$(hostname | tr -d '"')}"
GROUP="${GROUP_ARG:-default}"

if [ -z "$IP" ]; then
  err "无法自动获取本机 IP，请使用 --ip 参数指定"
  exit 1
fi
log "本机 IP: ${IP} | 名称: ${NAME} | 分组: ${GROUP}"

if [ "$(id -u)" -ne 0 ]; then
  warn "建议以 root 运行（systemd 注册与防火墙需要权限），当前非 root，将尽力执行"
fi

if [ -x "${INSTALL_DIR}/node_exporter" ]; then
  log "node_exporter 已安装，跳过下载"
else
  install_binary || exit 1
fi

setup_service
open_firewall

# ----------------------------- 注册策略 -------------------------------------
# 优先免登录接口（零输入）；失败则回退凭据模式；再失败提示手动
REG_METHOD="manual"
SECRET="${SECRET_ARG:-${MP_SECRET:-}}"
if register_open "$IP" "$NAME" "$GROUP" "$SECRET"; then
  REG_METHOD="open"
elif register_device "$IP" "$NAME" "$GROUP"; then
  REG_METHOD="credential"
fi

echo "------------------------------------------------------------"
log "本机自检（应能看到 node_ 开头的指标）:"
curl -s --connect-timeout 5 "http://localhost:9100/metrics" 2>/dev/null | head -3 || warn "本机 9100 未响应，请检查服务状态"

echo ""
if [ "$REG_METHOD" = "open" ]; then
  log "完成：已通过免登录接口自动注册，约 30 秒后刷新平台「首页总览 / 设备管理」即可看到本机在线与指标。"
elif [ "$REG_METHOD" = "credential" ]; then
  log "完成：已使用平台凭据自动注册，约 30 秒后刷新平台即可看到本机在线与指标。"
else
  log "完成：node_exporter 与防火墙已就绪，但自动注册未完成。"
cat <<TIP

请到平台「设备管理」手动添加本机 IP（${IP}），保存后约 30 秒自动在线；
或重跑并传入凭据：     MP_USER=admin MP_PASS='密码' bash $0
（若平台启用了注册密钥，用 --secret '密钥' 或 MP_SECRET=密钥 bash $0）
TIP
fi
echo "============================================================"
