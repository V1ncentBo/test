"""通过 SSH 到 PVE 宿主机反查虚拟机真实 IP（绕过 API token 权限墙）。

原理：
- 用 Linux root 凭据 SSH 到 PVE 宿主机（Web token 仅有列表级只读，但 root 可 SSH）；
- `qm config <vmid>` 读取网卡 MAC（net0/net1...）；
- 若 ARP 表未覆盖全部 VM（常见：VM 仅以 IPv6 邻居形式存在，IPv4 未被 ARP），
  则从宿主机对本地子网做一次轻量 ARP 扫描（并行 ping /24）补全 IPv4 邻居；
- 按 MAC 匹配，回填 machine_info.ip（仅对 device_type='pve_vm' 的子机）。
可选：若 VM 启用了 qemu-guest-agent，则直接 `qm agent <vmid> network-get-interfaces`
拿到最准确的内网 IP（guest agent 未启用时自动退化为 ARP 方案）。
"""
import re
import time
import ipaddress
import datetime

from models.database import SessionLocal, MachineInfo
from services.crypto import decrypt_password

MAC_RE = re.compile(
    r"\b([0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:"
    r"[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2})\b"
)
IPV4_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")


def _normalize_mac(m: str) -> str:
    return m.strip().lower()


def _connect(host_ip: str, user: str, password: str, key: str, timeout: int = 15):
    # 延迟导入 paramiko，避免在无 paramiko 的环境中影响后端启动
    import paramiko
    import io

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs = dict(
        hostname=host_ip,
        port=22,
        username=user,
        timeout=timeout,
        look_for_keys=False,
        allow_agent=False,
    )
    if key:
        try:
            pkey = paramiko.RSAKey.from_private_key(io.StringIO(key))
        except Exception:
            pkey = paramiko.Ed25519Key.from_private_key(io.StringIO(key))
        kwargs["pkey"] = pkey
    else:
        kwargs["password"] = password
    client.connect(**kwargs)
    return client


def _run(client, cmd: str, timeout: int = 60):
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    return out, err


def _parse_arp(out: str) -> dict:
    """从 `ip neigh` / `/proc/net/arp` 输出解析 {mac_lower: ipv4}。"""
    mapping = {}
    for line in out.splitlines():
        if "IP address" in line and "HW address" in line:
            continue  # /proc/net/arp 表头
        m_mac = MAC_RE.search(line)
        if not m_mac:
            continue
        mac = _normalize_mac(m_mac.group(1))
        m_ip = IPV4_RE.search(line)
        if not m_ip:
            continue
        ip = m_ip.group(1)
        if ip.startswith("169.254."):
            continue  # 跳过链路本地
        mapping.setdefault(mac, ip)
    return mapping


def _parse_vm_macs(cfg_out: str) -> list:
    """从 `qm config <vmid>` 输出解析所有网卡 MAC（net0/net1...）。"""
    macs = []
    for line in cfg_out.splitlines():
        line = line.strip()
        if not line.lower().startswith("net"):
            continue
        m = MAC_RE.search(line)
        if m:
            macs.append(_normalize_mac(m.group(1)))
    return macs


def _parse_guest_ips(agent_out: str) -> list:
    """从 `qm agent network-get-interfaces` 输出解析 IPv4 地址（跳过回环/链路本地）。"""
    ips = []
    for line in agent_out.splitlines():
        s = line.strip()
        if '"ip-address"' not in s:
            continue
        parts = s.split('"ip-address"', 1)[1].split('"')
        if len(parts) >= 2:
            ip = parts[1]
            if ":" not in ip and not ip.startswith("127.") and not ip.startswith("169.254."):
                ips.append(ip)
    return ips


def _get_subnet(client, host_ip: str):
    """从宿主机网卡配置推断本地子网 CIDR（优先匹配宿主机自身 IP 所在网段）。"""
    out, _ = _run(client, "ip -o -f inet addr show 2>/dev/null", timeout=15)
    cidr = None
    for line in out.splitlines():
        if host_ip not in line:
            continue
        for token in line.split():
            if token.count(".") == 3 and "/" in token:
                cidr = token
                break
        if cidr:
            break
    if not cidr:
        cidr = host_ip + "/24"  # 兜底假设 /24
    try:
        return ipaddress.ip_network(cidr, strict=False)
    except Exception:
        return None


def _arp_sweep(client, net):
    """对子网做一次并行 ping 扫描，补全宿主机 ARP 表中的 IPv4 邻居。"""
    if net.prefixlen < 24:
        return  # 网段过大（< /24）不扫描，避免海量 ping
    # 并行 ping 所有主机位，超时 1s，整体等待完成（通常 2~4s）
    ips = " ".join(str(ip) for ip in net.hosts())
    cmd = (
        f"for ip in {ips}; do "
        f"(ping -c1 -W1 $ip >/dev/null 2>&1 &); done; wait"
    )
    _run(client, cmd, timeout=60)


def pve_ssh_resolve_ips(host) -> tuple:
    """为 host（device_type='pve'）的所有 pve_vm 子机解析并回填 IP。

    返回 (updated_count, detail_str)。
    """
    user = (host.pve_ssh_user or "root").strip()
    password = decrypt_password(host.pve_ssh_pass) if host.pve_ssh_pass else ""
    key = host.pve_ssh_key or ""
    if not password and not key:
        return 0, "no-ssh-credentials"

    try:
        client = _connect(host.ip, user, password, key)
    except Exception as e:
        return 0, f"ssh-connect-failed: {e}"

    try:
        # 1) ARP 表（ip neigh 为主，/proc/net/arp 兜底）
        arp_out, _ = _run(client, "ip neigh 2>/dev/null; echo '---ARP---'; cat /proc/net/arp 2>/dev/null")
        arp_map = _parse_arp(arp_out)
        # 2) 若宿主机能拿到本地子网，做一次轻量 ARP 扫描补全 IPv4 邻居
        net = _get_subnet(client, host.ip)
        if net and net.prefixlen >= 24:
            _arp_sweep(client, net)
            arp_out2, _ = _run(client, "ip neigh 2>/dev/null; echo '---ARP---'; cat /proc/net/arp 2>/dev/null")
            arp_map = _parse_arp(arp_out2)

        # 3) 查询该宿主机的 pve_vm 子机
        db = SessionLocal()
        try:
            children = db.query(MachineInfo).filter(
                MachineInfo.parent_id == host.id,
                MachineInfo.device_type == "pve_vm",
            ).all()
            if not children:
                return 0, "no-pve-vm-children"

            total = len(children)
            updated = 0
            for vm in children:
                vmid = vm.pve_vmid
                if not vmid:
                    continue
                ip = None
                # 优先 guest agent（若启用，IP 最准确）
                agent_out, _ = _run(client, f"qm agent {vmid} network-get-interfaces 2>&1", timeout=10)
                if agent_out and "No QEMU guest agent" not in agent_out and "not running" not in agent_out:
                    gips = _parse_guest_ips(agent_out)
                    if gips:
                        ip = gips[0]
                # 退化为 ARP：qm config 取 MAC -> ARP 表取 IP
                if not ip:
                    cfg_out, _ = _run(client, f"qm config {vmid} 2>&1", timeout=10)
                    for mac in _parse_vm_macs(cfg_out):
                        if mac in arp_map:
                            ip = arp_map[mac]
                            break
                if ip and ip != vm.ip:
                    vm.ip = ip
                    vm.updated_at = datetime.datetime.now()
                    updated += 1
            if updated:
                db.commit()
            return updated, f"resolved {updated}/{total} via ssh"
        finally:
            db.close()
    finally:
        client.close()
