"""PVE (Proxmox VE) API 采集 — 调 REST API 拉取节点状态与虚拟机清单/实时指标。

认证：PVE API Token，请求头 Authorization: PVEAPIToken=user@realm!tokenid=secret
接口默认 HTTPS :8006，自签证书跳过校验（PVE 安装后默认自签）。
"""
import ssl
import json
import urllib.request
from datetime import datetime


def _make_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _pve_get(base_url, token, path, timeout=5):
    """GET PVE API，返回 data 字段(dict/list)。失败抛异常。"""
    url = f"{base_url}/api2/json{path}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"PVEAPIToken={token}")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=timeout, context=_make_ctx()) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body).get("data")


def discover_nodes(base_url, token, timeout=5):
    """列出 PVE 集群/单节点的节点名。单节点 PVE 也返回 [hostname]。"""
    data = _pve_get(base_url, token, "/nodes", timeout)
    if isinstance(data, list):
        return [n.get("node") for n in data if n.get("node")]
    return []


def get_node_status(base_url, token, node, timeout=5):
    """宿主机节点状态：cpu(0..1)、memory{used,total}、rootfs{used,total} 等。"""
    return _pve_get(base_url, token, f"/nodes/{node}/status", timeout)


def list_vms(base_url, token, timeout=5):
    """集群全部虚拟机(含节点归属)。返回 cluster/resources?type=vm 的列表项。

    每项含：vmid, node, name, status(running/stopped/paused),
    cpu(0..1 单核占用), maxcpu(核数), mem(字节), maxmem(字节), disk(字节), uptime(秒)。
    """
    data = _pve_get(base_url, token, "/cluster/resources?type=vm", timeout)
    if isinstance(data, list):
        return data
    return []
