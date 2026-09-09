"""定时任务报表 — 三层完全差异化 AI prompt"""
import json, os
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta
from routers.auth import require_any_perm, require_admin

router = APIRouter(prefix="/api/reports/schedule", tags=["报表面板"], dependencies=[Depends(require_any_perm("reports"))])

SCHEDULE_FILE = "/app/data/report_schedules.json"
REPORTS_FILE = "/app/data/scheduled_reports.json"


class ScheduleCreate(BaseModel):
    name: str; frequency: str; time_of_day: str = "09:00"
    day_of_week: int = 1; day_of_month: int = 1
    machine_types: List[str] = ["physical", "kvm", "vmware", "virtualbox"]
    include_charts: bool = True; include_alerts: bool = True

class ScheduleUpdate(BaseModel):
    name: Optional[str] = None; frequency: Optional[str] = None
    time_of_day: Optional[str] = None; day_of_week: Optional[int] = None
    day_of_month: Optional[int] = None; machine_types: Optional[List[str]] = None
    include_charts: Optional[bool] = None; include_alerts: Optional[bool] = None
    enabled: Optional[bool] = None


def _load():
    if os.path.exists(SCHEDULE_FILE):
        with open(SCHEDULE_FILE, "r") as f: return json.load(f)
    return []

def _save(data):
    os.makedirs(os.path.dirname(SCHEDULE_FILE), exist_ok=True)
    with open(SCHEDULE_FILE, "w") as f: json.dump(data, f, indent=2, ensure_ascii=False)


@router.get("")
def list_schedules():
    scheds = _load()
    for s in scheds:
        if not s.get("next_run"): s["next_run"] = _calc_next_run(s)
    return scheds

@router.post("", dependencies=[Depends(require_admin)])
def create_schedule(data: ScheduleCreate):
    scheds = _load(); new_id = max([s.get("id",0) for s in scheds], default=0)+1
    item = data.model_dump(); item["id"]=new_id; item["enabled"]=True
    item["created_at"]=datetime.now().isoformat(); item["last_run"]=None
    item["next_run"]=_calc_next_run(item); scheds.append(item); _save(scheds)
    return item

@router.put("/{schedule_id}", dependencies=[Depends(require_admin)])
def update_schedule(schedule_id: int, data: ScheduleUpdate):
    scheds = _load()
    for s in scheds:
        if s["id"]==schedule_id:
            for k,v in data.model_dump(exclude_none=True).items(): s[k]=v
            s["next_run"]=_calc_next_run(s); _save(scheds); return s
    raise HTTPException(status_code=404)

@router.delete("/{schedule_id}", dependencies=[Depends(require_admin)])
def delete_schedule(schedule_id: int):
    _save([s for s in _load() if s["id"]!=schedule_id]); return {"ok":True}


# ═══════════════════════════════════════════════════════════
#  核心：数据采集
# ═══════════════════════════════════════════════════════════

def _collect_data(types: list) -> tuple:
    from models.database import SessionLocal, MachineInfo
    db = SessionLocal()
    machines = db.query(MachineInfo).filter(
        MachineInfo.device_type.in_(types)
    ).all() if types else db.query(MachineInfo).all()

    import influxdb_client
    client = influxdb_client.InfluxDBClient(
        url=os.environ.get("INFLUXDB_URL", "http://influxdb:8086"),
        token=os.environ.get("INFLUXDB_TOKEN", ""),
        org=os.environ.get("INFLUXDB_ORG", "monitor-org"),
    )
    query_api = client.query_api()

    data = []
    for m in machines:
        q = f'from(bucket:"machine_metrics") |> range(start: -24h) |> filter(fn:(r) => r["machine_id"] == "{m.id}") |> last()'
        result = query_api.query(q)
        cpu, mem, disk, load = 0, 0, 0, 0
        for table in result:
            for record in table.records:
                fld = record.get_field(); val = record.get_value()
                if fld == "cpu_percent": cpu = val
                elif fld == "memory_percent": mem = val
                elif fld == "disk_percent": disk = val
                elif fld == "load_1m": load = val
        data.append({
            "name": m.name, "ip": m.ip, "type": m.device_type or "",
            "status": m.online_status,
            "cpu": round(cpu,1) if isinstance(cpu,(int,float)) else 0,
            "memory": round(mem,1) if isinstance(mem,(int,float)) else 0,
            "disk": round(disk,1) if isinstance(disk,(int,float)) else 0,
            "load": round(load,2) if isinstance(load,(int,float)) else 0,
        })
    return data, client, db


# ═══════════════════════════════════════════════════════════
#  三个完全不同的 AI prompt
# ═══════════════════════════════════════════════════════════

def _call_ai(machines: list, period: str, extra_context: str = "") -> str:
    """调用 AI，失败回退到本地"""
    try:
        from services.ai_service import _client as ai_client, _model as ai_model
        from config import bj_now
        now = bj_now().strftime("%Y-%m-%d %H:%M")
        today = bj_now().strftime("%Y-%m-%d")

        machine_json = json.dumps(machines, ensure_ascii=False)[:6000]
        total = len(machines)
        online = sum(1 for m in machines if m.get("status") == "online")
        cpu_vals = [m.get("cpu",0) for m in machines]
        mem_vals = [m.get("memory",0) for m in machines]
        disk_vals = [m.get("disk",0) for m in machines]
        avg_cpu = round(sum(cpu_vals)/max(len(cpu_vals),1), 1)
        avg_mem = round(sum(mem_vals)/max(len(mem_vals),1), 1)
        avg_disk = round(sum(disk_vals)/max(len(disk_vals),1), 1)
        max_cpu = max(cpu_vals) if cpu_vals else 0
        max_mem = max(mem_vals) if mem_vals else 0
        max_disk = max(disk_vals) if disk_vals else 0

        if period == "daily":
            prompt = f"""当前时间: {now} (北京时间)
你是资深运维工程师，请为 {total} 台设备生成一份简洁的**日报**。

设备数据:
{machine_json}

要求（简洁为主，不需要长篇大论）：
## 今日概览
一句话概括整体状态（设备数、在线数、CPU/内存/磁盘均值）。

## 异常与告警
列出有异常的设备（CPU>60%或内存>70%或磁盘>75%），没有则写"今日无异常"。

## 关注事项
最多 1-2 条需要跟进的事项，没有则写"运行正常"。

直接输出报告，不要前言。"""
        elif period == "weekly":
            prompt = f"""当前时间: {now} (北京时间)，本周一至今
你是资深运维架构师，请为 {total} 台设备生成一份详细的**周报**。

汇总数据：设备 {total} 台，在线 {online} 台，CPU 均值 {avg_cpu}%（峰值 {max_cpu}%），
内存均值 {avg_mem}%（峰值 {max_mem}%），磁盘均值 {avg_disk}%（峰值 {max_disk}%）。

设备数据:
{machine_json}

格式要求（严格遵守，用 ## 标题、Markdown 表格）：
## 运行概况
整体状态概述，附带关键指标汇总表（| 指标 | 数值 |），1-2 句话。

## 异常统计
按设备列出异常情况，用表格呈现（| 设备 | IP | 类型 | 异常指标 | 严重程度 |）。

## 资源分析
分 CPU / 内存 / 磁盘 三个小节（用 ### 子标题），每节含：
- 整体评估 1-2 句
- 按使用率降序的设备数据表格（| 设备 | CPU% | 内存% | 磁盘% | 状态 |）
- 关键发现

## 优化建议
2-5 条可执行的建议，标注优先级（🔴紧急 / 🟡建议 / 🟢常规）。

## 下阶段关注
下周需要重点跟踪的 2-3 个事项。

直接输出报告，专业、详实。"""
        else:  # monthly
            prompt = f"""当前时间: {now} (北京时间)，本月1日至今（约 {today[:7]} 月）
你是资深运维架构师，请基于以下监控数据生成一份专业的**月度运维报告**。

报告日期: {today}
报告周期: {today[:7]}-01 至 {today}
生成时间: {now}

汇总数据：设备 {total} 台，在线 {online} 台，CPU 均值 {avg_cpu}%（峰值 {max_cpu}%），
内存均值 {avg_mem}%（峰值 {max_mem}%），磁盘均值 {avg_disk}%（峰值 {max_disk}%）。

设备数据:
{machine_json}

格式要求（严格遵守，用 ## 标题、Markdown 表格）：
## 运行概况
整体概述 2-3 句，附带关键指标汇总表（| 指标 | 数值 |）。

## 异常统计
按设备列出所有异常，含根因推断。用表格（| 设备 | IP | 类型 | 异常指标 | 严重程度 | 根因分析 |）。

## 资源分析
分 CPU / 内存 / 磁盘 三个小节（用 ### 子标题），每节含：
- 整体水平评估
- 按使用率降序的详细表格（| 设备 | 当前值% | 状态 | 风险评估 |）
- 关键发现（2-3 条）
- 对高风险设备给出具体处理建议

## 优化建议
按紧急程度（🔴紧急 → 🟡建议 → 🟢常规）给出 4-6 条具体可执行的建议，
每条含操作步骤和预期效果。

## 下阶段关注
列出 4-5 条下个月需要重点跟踪的事项，含预期目标和完成标准。

直接输出报告，全面深入，像专业运维月报。"""

        import openai
        if ai_client:
            response = ai_client.chat.completions.create(
                model=ai_model,
                messages=[
                    {"role": "system", "content": "你是资深运维架构师，回复专业、详实、结构清晰。用 Markdown 表格呈现数据。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3, max_tokens=4096,
            )
            return response.choices[0].message.content
    except Exception as e:
        print(f"[AI Report] {period} custom prompt failed: {e}")

    # Fallback to standard AI service
    try:
        from services.ai_service import ai_service
        return ai_service.generate_report(machines, period)
    except:
        from services.local_analyzer import LocalAnalyzer
        return LocalAnalyzer.generate_report(machines, period)


# ═══════════════════════════════════════════════════════════
#  主生成函数
# ═══════════════════════════════════════════════════════════

def generate_report(schedule: dict) -> dict:
    period = schedule.get("frequency", "daily")
    types = schedule.get("machine_types", [])
    now = datetime.now()

    machine_data, influx_client, db = _collect_data(types)

    # ── 日报：结构化概览 + AI 总结 ──
    if period == "daily":
        total = len(machine_data)
        online = sum(1 for m in machine_data if m.get("status") == "online")
        cpu_vals = [m["cpu"] for m in machine_data]
        mem_vals = [m["memory"] for m in machine_data]
        avg_cpu = round(sum(cpu_vals)/max(len(cpu_vals),1), 1)
        avg_mem = round(sum(mem_vals)/max(len(mem_vals),1), 1)
        avg_disk = round(sum([m["disk"] for m in machine_data])/max(len(machine_data),1), 1)

        lines = [f"# 日报 - {now.strftime('%Y年%m月%d日')}\n"]
        lines.append("| 指标 | 数值 |")
        lines.append("|------|------|")
        lines.append(f"| 设备 | **{total}** 台（在线 {online} / 离线 {total-online}）|")
        lines.append(f"| CPU | 平均 **{avg_cpu}%** / 峰值 **{max(cpu_vals) if cpu_vals else 0}%** |")
        lines.append(f"| 内存 | 平均 **{avg_mem}%** / 峰值 **{max(mem_vals) if mem_vals else 0}%** |")
        lines.append(f"| 磁盘 | 平均 **{avg_disk}%** |")

        # 设备状态表
        lines.append(f"\n## 设备状态")
        lines.append("| 设备 | CPU | 内存 | 磁盘 | 状态 |")
        lines.append("|------|-----|------|------|------|")
        for m in sorted(machine_data, key=lambda x: x["cpu"], reverse=True):
            st = "🟢" if m["cpu"]<60 and m["memory"]<60 else ("🟡" if m["cpu"]<85 else "🔴")
            lines.append(f"| {m['name']} | {m['cpu']}% | {m['memory']}% | {m['disk']}% | {st} |")

        # 异常告警速览
        lines.append(f"\n## 异常速览")
        alert_count = db.query(__import__('models.database', fromlist=['AlertLog']).AlertLog).filter(
            __import__('models.database', fromlist=['AlertLog']).AlertLog.created_at >= now.replace(hour=0, minute=0, second=0)
        ).count()
        high = [m for m in machine_data if m["cpu"]>70 or m["memory"]>80 or m["disk"]>80]
        if high:
            for m in high:
                reason = "CPU" if m["cpu"]>70 else ("内存" if m["memory"]>80 else "磁盘")
                lines.append(f"- 🔴 **{m['name']}**: {reason}偏高 (CPU {m['cpu']}% / MEM {m['memory']}% / DISK {m['disk']}%)")
        else:
            lines.append("✅ 所有设备运行正常")
        if alert_count:
            lines.append(f"- 今日告警: {alert_count} 个")

        # AI 一句话
        ai_report = _call_ai(machine_data, "daily")
        lines.append(f"\n{ai_report}")
        report = "\n".join(lines)

    # ── 周报：AI 主力生成 + 健康评分补充 ──
    elif period == "weekly":
        report = _call_ai(machine_data, "weekly")

        # 附加健康评分
        report += "\n\n---\n## 附：设备健康评分（100分制）\n"
        report += "| 设备 | CPU(25) | 内存(25) | 磁盘(20) | 在线(10) | **总分** | 评级 |\n"
        report += "|------|---------|----------|----------|----------|----------|------|\n"
        for m in machine_data:
            cpu_s = min(25, max(0, int(25 - m["cpu"] * 0.3)))
            mem_s = min(25, max(0, int(25 - m["memory"] * 0.3)))
            disk_s = min(20, max(0, int(20 - m["disk"] * 0.25)))
            online_s = 10 if m.get("status") == "online" else 0
            total_s = cpu_s + mem_s + disk_s + online_s
            grade = "A" if total_s>=90 else ("B" if total_s>=75 else ("C" if total_s>=60 else "D"))
            report += f"| {m['name']} | {cpu_s} | {mem_s} | {disk_s} | {online_s} | **{total_s}** | {grade} |\n"

    # ── 月报：AI 全面生成 ──
    else:
        report = _call_ai(machine_data, "monthly")

        # 附加容量规划
        report += "\n\n---\n## 附：容量规划快照\n"
        report += "| 设备 | CPU现状 | 内存现状 | 30天预估CPU | 30天预估内存 | 建议 |\n"
        report += "|------|---------|----------|-------------|-------------|------|\n"
        for m in sorted(machine_data, key=lambda x: x["cpu"], reverse=True):
            pred_cpu = min(100, round(m["cpu"]*1.05, 1))
            pred_mem = min(100, round(m["memory"]*1.03, 1))
            adv = "⚠ 关注" if pred_cpu>70 or pred_mem>85 else "✅ 正常"
            report += f"| {m['name']} | {m['cpu']}% | {m['memory']}% | {pred_cpu}% | {pred_mem}% | {adv} |\n"

    influx_client.close()
    db.close()

    # ── 保存 ──
    reports = []
    if os.path.exists(REPORTS_FILE):
        with open(REPORTS_FILE, "r") as f: reports = json.load(f)
    reports.insert(0, {
        "id": int(datetime.now().timestamp() * 1000),
        "schedule_id": schedule["id"],
        "schedule_name": schedule["name"],
        "title": schedule["name"],
        "content": report,
        "period": period,
        "created_at": datetime.now().isoformat(),
        "machine_types": types,
    })
    with open(REPORTS_FILE, "w") as f:
        json.dump(reports[:100], f, indent=2, ensure_ascii=False)

    scheds = _load()
    for s in scheds:
        if s["id"] == schedule["id"]:
            s["last_run"] = datetime.now().isoformat()
            s["next_run"] = _calc_next_run(s); break
    _save(scheds)

    return {"ok": True, "report": report, "title": schedule["name"]}


# ── 路由 ──
@router.post("/{schedule_id}/run", dependencies=[Depends(require_admin)])
def run_now(schedule_id: int):
    scheds = _load()
    for s in scheds:
        if s["id"] == schedule_id: return generate_report(s)
    raise HTTPException(status_code=404)

@router.get("/reports")
def list_scheduled_reports():
    if os.path.exists(REPORTS_FILE):
        with open(REPORTS_FILE, "r") as f: return json.load(f)
    return []

@router.delete("/reports/{report_id}", dependencies=[Depends(require_admin)])
def delete_scheduled_report(report_id: int):
    if os.path.exists(REPORTS_FILE):
        with open(REPORTS_FILE,"r") as f: reports = json.load(f)
        with open(REPORTS_FILE,"w") as f: json.dump([r for r in reports if r.get("id")!=report_id], f, indent=2, ensure_ascii=False)
    return {"ok": True}

def _calc_next_run(s: dict):
    freq = s.get("frequency","daily"); t = s.get("time_of_day","09:00")
    try: h,m = map(int, t.split(":"))
    except: h,m = 9,0
    now = datetime.now()
    if freq == "daily":
        nd = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if nd <= now: nd += timedelta(days=1)
    elif freq == "weekly":
        dow = s.get("day_of_week",1); da = dow - now.isoweekday()
        if da <= 0: da += 7
        nd = (now + timedelta(days=da)).replace(hour=h, minute=m, second=0, microsecond=0)
    elif freq == "monthly":
        dom = s.get("day_of_month",1)
        if now.day >= dom:
            if now.month==12: nd = now.replace(year=now.year+1, month=1, day=dom, hour=h, minute=m, second=0)
            else: nd = now.replace(month=now.month+1, day=dom, hour=h, minute=m, second=0)
        else: nd = now.replace(day=dom, hour=h, minute=m, second=0)
    else: return None
    return nd.isoformat()
