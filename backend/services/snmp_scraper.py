"""SNMP 采集器 — 交换机/路由器等网络设备指标采集

设计要点：
- 交换机/路由器跑不了 node_exporter，靠 SNMP 暴露指标。标准 MIB-2 (RFC1213) 即可拿到端口流量/状态/设备信息。
- 设备级：sysName / sysDescr / sysUpTime（一次 GET）
- 端口级：WALK ifTable 拿每个端口 ifDescr/ifSpeed/ifOperStatus/ifInOctets/ifOutOctets
- 速率(in/out Mbps) 需要两次采样做差，由 collector 维护上一次样本计算，这里只返回原始累计字节数。
"""
from pysnmp.hlapi import (
    SnmpEngine, CommunityData, UdpTransportTarget, ContextData,
    ObjectType, ObjectIdentity, getCmd, nextCmd, bulkCmd,
)
import logging
logger = logging.getLogger("snmp_scraper")

# ifTable 列号 → 含义
IF_DESCR = 2
IF_SPEED = 5
IF_OPERSTATUS = 8
IF_IN_OCTETS = 10
IF_IN_DISCARDS = 13
IF_IN_ERRORS = 14
IF_OUT_OCTETS = 16
IF_OUT_DISCARDS = 19
IF_OUT_ERRORS = 20

# 1=up, 2=down, 3=testing, 4=unknown, 5=dormant, 6=notPresent, 7=lowerLayerDown
OPER_STATUS_NAME = {1: "up", 2: "down", 3: "testing", 4: "unknown",
                    5: "dormant", 6: "notPresent", 7: "lowerLayerDown"}

IF_TABLE_OID = "1.3.6.1.2.1.2.2"

# IP/MAC -> 端口 解析所需 OID（标准 MIB-2，只读即可，无需写权限）
IP_NET_TO_MEDIA_PHYS = "1.3.6.1.2.1.4.22.1.2"        # ARP 表: OID.<ifIndex>.<ip4> = MAC
DOT1D_TP_FDB_PORT = "1.3.6.1.2.1.17.4.3.1.2"          # MAC 转发表: OID.<mac6> = 桥端口
DOT1D_BASE_PORT_IFINDEX = "1.3.6.1.2.1.17.1.4.1.2"    # 桥端口表: OID.<bridgePort> = ifIndex


def _mp_model(version: str) -> int:
    return 0 if version == "v1" else 1  # v2c / v3 统一按 v2c 处理 community


def _snmp_get(ip, port, community, version, oids, timeout, retries):
    """GET 一组 OID，返回 {oid: value} 或 None（超时/错误）"""
    try:
        iterator = getCmd(
            SnmpEngine(),
            CommunityData(community, mpModel=_mp_model(version)),
            UdpTransportTarget((ip, int(port)), timeout=timeout, retries=retries),
            ContextData(),
            *[ObjectType(ObjectIdentity(oid)) for oid in oids],
        )
        errorIndication, errorStatus, errorIndex, varBinds = next(iterator)
        if errorIndication:
            logger.warning(f"[SNMP] GET {ip}:{port} 错误: {errorIndication}")
            return None
        if errorStatus:
            logger.warning(f"[SNMP] GET {ip}:{port} status: {errorStatus.prettyPrint()}")
            return None
        return {str(v[0]): v[1] for v in varBinds}
    except Exception as e:
        logger.warning(f"[SNMP] GET {ip}:{port} 异常: {e}")
        return None


def _snmp_walk(ip, port, community, version, base_oid, timeout, retries):
    """WALK 一个子树，返回 {oid: value}。

    v2c/v3 使用 GETBULK（bulkCmd）批量取数，单台 52 端口交换机的 ifTable 从约 260 个
    GETNEXT 报文降到 3~5 个 PDU；v1 无 GETBULK，回退 GETNEXT。
    """
    result = {}
    try:
        target = UdpTransportTarget((ip, int(port)), timeout=timeout, retries=retries)
        mp_model = _mp_model(version)
        if mp_model == 0:
            # SNMP v1：无 GETBULK，逐行 GETNEXT
            for (errorIndication, errorStatus, errorIndex, varBinds) in nextCmd(
                SnmpEngine(),
                CommunityData(community, mpModel=0),
                target, ContextData(),
                ObjectType(ObjectIdentity(base_oid)),
                lexicographicMode=False,
            ):
                if errorIndication:
                    logger.warning(f"[SNMP] WALK {ip}:{port} {base_oid} 错误: {errorIndication}")
                    break
                if errorStatus:
                    logger.warning(f"[SNMP] WALK {ip}:{port} status: {errorStatus.prettyPrint()}")
                    break
                for varBind in varBinds:
                    result[str(varBind[0])] = varBind[1]
        else:
            # SNMP v2c/v3：GETBULK，max-repetitions=50 大幅减少往返
            for (errorIndication, errorStatus, errorIndex, varBinds) in bulkCmd(
                SnmpEngine(),
                CommunityData(community, mpModel=mp_model),
                target, ContextData(),
                0, 50,
                ObjectType(ObjectIdentity(base_oid)),
                lexicographicMode=False,
            ):
                if errorIndication:
                    logger.warning(f"[SNMP] BULK {ip}:{port} {base_oid} 错误: {errorIndication}")
                    break
                if errorStatus:
                    logger.warning(f"[SNMP] BULK {ip}:{port} status: {errorStatus.prettyPrint()}")
                    break
                for varBind in varBinds:
                    result[str(varBind[0])] = varBind[1]
    except Exception as e:
        logger.warning(f"[SNMP] WALK {ip}:{port} {base_oid} 异常: {e}")
    return result


def scrape_snmp(ip: str, port: int = 161, community: str = "public",
                version: str = "v2c", timeout: int = 3, retries: int = 1) -> dict:
    """采集单台 SNMP 设备，返回结构化 dict。不可达返回 {"online": False}。"""
    # 设备级信息
    dev = _snmp_get(ip, port, community, version,
                    ["1.3.6.1.2.1.1.5.0", "1.3.6.1.2.1.1.1.0", "1.3.6.1.2.1.1.3.0"],
                    timeout, retries)
    if dev is None:
        return {"online": False}

    def _to_str(v):
        try:
            return str(v).strip()
        except Exception:
            return ""

    def _to_int(v):
        try:
            return int(v)
        except Exception:
            return 0

    sys_name = _to_str(dev.get("1.3.6.1.2.1.1.5.0"))
    sys_descr = _to_str(dev.get("1.3.6.1.2.1.1.1.0"))
    # sysUpTime 单位百分之一秒
    sys_uptime = _to_int(dev.get("1.3.6.1.2.1.1.3.0")) // 100

    # 端口级：WALK 整个 ifTable
    walk = _snmp_walk(ip, port, community, version, IF_TABLE_OID, timeout, retries)
    ports = {}
    for oid, val in walk.items():
        parts = oid.split(".")
        if len(parts) < 2:
            continue
        try:
            col = int(parts[-2])
            idx = int(parts[-1])
        except ValueError:
            continue
        ports.setdefault(idx, {})[col] = val

    port_list = []
    for idx, cols in ports.items():
        descr = _to_str(cols.get(IF_DESCR))
        speed = _to_int(cols.get(IF_SPEED))
        oper = _to_int(cols.get(IF_OPERSTATUS))
        in_octets = _to_int(cols.get(IF_IN_OCTETS))
        out_octets = _to_int(cols.get(IF_OUT_OCTETS))
        in_errors = _to_int(cols.get(IF_IN_ERRORS))
        out_errors = _to_int(cols.get(IF_OUT_ERRORS))
        in_discards = _to_int(cols.get(IF_IN_DISCARDS))
        out_discards = _to_int(cols.get(IF_OUT_DISCARDS))
        port_list.append({
            "index": idx,
            "descr": descr or f"ifIndex{idx}",
            "speed": speed,
            "oper_status": oper,
            "oper_status_name": OPER_STATUS_NAME.get(oper, "unknown"),
            "in_octets": in_octets,
            "out_octets": out_octets,
            "in_errors": in_errors,
            "out_errors": out_errors,
            "in_discards": in_discards,
            "out_discards": out_discards,
        })

    return {
        "online": True,
        "sys_name": sys_name,
        "sys_descr": sys_descr,
        "sys_uptime": sys_uptime,
        "ports": port_list,
    }


def _mac_from_val(val) -> str:
    """把 pysnmp 的 OctetString 值转成 6 字节 MAC 文本"""
    try:
        b = val.asOctets()
    except Exception:
        try:
            b = bytes(val)
        except Exception:
            return ""
    return ":".join("%02X" % x for x in b)


def scrape_port_ipmac(ip: str, port: int = 161, community: str = "public",
                      version: str = "v2c", timeout: int = 3, retries: int = 1) -> dict:
    """解析 端口 <-> IP/MAC 映射，返回 {if_index(int): [{'ip':.., 'mac':..}, ...]}

    链路: ARP(4.22) IP->MAC  →  FDB(17.4.3.1.2) MAC->桥端口  →  桥端口表(17.1.4.1.2) 桥端口->ifIndex
    全程只读团体字即可，无需写权限。交换机无 802.1Q MIB 时仍可读到 IP/MAC。
    """
    # 1) ARP: OID 4.22.1.2.<ifIndex>.<ip4> = MAC  ->  ip -> mac
    arp = _snmp_walk(ip, port, community, version, IP_NET_TO_MEDIA_PHYS, timeout, retries)
    ip_mac = {}
    for oid, val in arp.items():
        comps = oid.split(".")[-5:]
        if len(comps) != 5:
            continue
        ip_addr = ".".join(comps[1:6])   # 后 4 段为 IP
        if ip_addr == "0.0.0.0":
            continue
        mac = _mac_from_val(val)
        if not mac or mac == "00:00:00:00:00:00":
            continue
        ip_mac[ip_addr] = mac

    # 2) FDB: OID 17.4.3.1.2.<mac6> = 桥端口  ->  mac -> 桥端口
    fdb = _snmp_walk(ip, port, community, version, DOT1D_TP_FDB_PORT, timeout, retries)
    mac_bport = {}
    for oid, val in fdb.items():
        comps = oid.split(".")[-6:]
        if len(comps) != 6:
            continue
        try:
            mac = ":".join("%02X" % int(c) for c in comps)
            mac_bport[mac] = int(val)
        except ValueError:
            continue

    # 3) 桥端口表: OID 17.1.4.1.2.<bridgePort> = ifIndex  ->  桥端口 -> ifIndex
    bp = _snmp_walk(ip, port, community, version, DOT1D_BASE_PORT_IFINDEX, timeout, retries)
    bport_if = {}
    for oid, val in bp.items():
        try:
            bport_if[int(oid.split(".")[-1])] = int(val)
        except ValueError:
            continue

    # 4) 关联: ip -> mac -> 桥端口 -> ifIndex
    result = {}
    for ip_addr, mac in ip_mac.items():
        bport = mac_bport.get(mac)
        if bport is None:
            continue
        ifidx = bport_if.get(bport)
        if ifidx is None:
            continue
        result.setdefault(ifidx, []).append({"ip": ip_addr, "mac": mac})
    return result

