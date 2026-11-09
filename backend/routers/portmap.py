"""工位网口图路由（PortMap）
提供：图纸列表/详情（任意登录用户可读）、图纸与格子的增删改（仅管理员）、
批量改色/批量改字段（仅管理员）、首次访问自动铺底图。

所有端点挂在 /api/portmap 下，与「硬件台账 resource_items」完全解耦。
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime

from models.database import get_db
from models.portmap import PortMap, PortCell, PortMapUndo, PORT_FIELDS
from routers.auth import get_current_user, require_admin

router = APIRouter(
    prefix="/api/portmap",
    tags=["工位网口图"],
    dependencies=[Depends(get_current_user)],
)

# kind 默认色（与源表 座位图.xlsx 的填色一致：蓝=网口 / 绿=过道功能 / 粉=柜体 / 青=消防通道）
KIND_COLORS = {
    "port":    "#ABD6FF",
    "aisle":   "#E3E8EC",
    "zone":    "#60B051",
    "cabinet": "#FEBED9",
    "tag":     "#F1F3F4",
}
STATUS_COLORS = {
    "空闲":   "#ABD6FF",
    "使用中": "#B7E1CD",
    "预留":   "#FCE8B2",
    "故障":   "#F4B6B6",
    "停用":   "#D9D9D9",
}


# ---------------- 序列化 ----------------
def map_dict(m: PortMap, count: int = 0) -> dict:
    return {
        "id": m.id,
        "name": m.name,
        "floor": m.floor or "",
        "rows": m.rows or 22,
        "cols": m.cols or 24,
        "cell_w": m.cell_w or 76,
        "cell_h": m.cell_h or 51,
        "display_field": m.display_field or "",
        "legend": m.legend or [],
        "remark": m.remark or "",
        "sort_order": m.sort_order or 0,
        "cell_count": count,
        "updated_at": m.updated_at.strftime("%Y-%m-%d %H:%M:%S") if m.updated_at else "",
    }


def cell_dict(c: PortCell) -> dict:
    return {
        "id": c.id,
        "map_id": c.map_id,
        "row": c.row,
        "col": c.col,
        "row_span": c.row_span or 1,
        "col_span": c.col_span or 1,
        "label": c.label or "",
        "kind": c.kind or "port",
        "bg_color": c.bg_color or "",
        "display_field": c.display_field or "",
        "fields": c.fields or {},
    }


# ---------------- 底图种子（源：座位图.xlsx，逐格导出） ----------------
# 由 Excel 直接导出：行/列/跨行/跨列/类型/底色/标签。⛔ 勿手改，
# 改图请改 Excel 后重新导出（含「有填色但没文字」的格子，那些也是网口/过道/功能区）。
# 色义：ABD6FF=网口  60B051=过道  C3EBE0=消防通道  E1ADF7=展厅  F9DE18=机房  FEBED9=电梯间/入口/柜体
_SEED_TSV = """r	c	rs	cs	kind	bg	label
0	0	1	4	aisle	C3EBE0	消防通道
0	4	1	1	port	ABD6FF	
0	5	1	1	port	ABD6FF	
0	6	1	1	port	ABD6FF	
0	7	1	1	port	ABD6FF	
0	8	1	1	port	ABD6FF	
0	9	1	1	port	ABD6FF	
0	10	1	1	port	ABD6FF	
0	11	1	1	port	ABD6FF	
0	12	1	1	port	ABD6FF	
0	13	1	1	port	ABD6FF	
0	14	1	1	port	ABD6FF	
0	15	1	1	aisle	C3EBE0	
0	16	7	1	zone	E1ADF7	展厅
0	17	7	3	zone	FEBED9	电梯间
1	0	1	1	aisle	60B051	
1	1	1	1	aisle	60B051	
1	2	1	1	aisle	60B051	
1	3	1	1	aisle	60B051	
1	4	1	1	aisle	60B051	
1	5	1	1	aisle	60B051	
1	6	1	1	aisle	60B051	
1	7	1	1	aisle	60B051	
1	8	1	1	aisle	60B051	
1	9	1	1	aisle	60B051	
1	10	1	1	aisle	60B051	
1	11	1	1	aisle	60B051	
1	12	1	1	aisle	60B051	
1	13	1	1	aisle	60B051	
1	14	1	1	aisle	60B051	
1	15	1	1	zone	FEBED9	入口
2	0	1	1	aisle	60B051	
2	1	1	1	cabinet	FEBED9	铁皮柜
2	2	1	1	cabinet	FEBED9	铁皮柜
2	3	1	4	cabinet	FEBED9	小白柜
2	7	1	1	aisle	60B051	
2	8	1	7	cabinet	FEBED9	小白柜
2	15	1	1	aisle	60B051	
3	0	1	1	port	ABD6FF	A10
3	1	1	1	port	ABD6FF	A11
3	2	1	1	port	ABD6FF	A12
3	3	1	1	port	ABD6FF	A13
3	4	1	1	port	ABD6FF	A14
3	5	1	1	port	ABD6FF	A15
3	6	1	1	port	ABD6FF	A16
3	7	1	1	aisle	60B051	
3	8	1	1	port	ABD6FF	J9
3	9	1	1	port	ABD6FF	J10
3	10	1	1	port	ABD6FF	J11
3	11	1	1	port	ABD6FF	J12
3	12	1	1	port	ABD6FF	J13
3	13	1	1	port	ABD6FF	J14
3	14	1	1	port	ABD6FF	J15
3	15	1	1	aisle	60B051	
4	0	1	1	aisle	60B051	
4	1	1	1	aisle	60B051	
4	2	1	1	aisle	60B051	
4	3	1	1	aisle	60B051	
4	4	1	1	aisle	60B051	
4	5	1	1	aisle	60B051	
4	6	1	1	aisle	60B051	
4	7	1	1	aisle	60B051	
4	8	1	1	aisle	60B051	
4	9	1	1	aisle	60B051	
4	10	1	1	aisle	60B051	
4	11	1	1	aisle	60B051	
4	12	1	1	aisle	60B051	
4	13	1	1	aisle	60B051	
4	14	1	1	aisle	60B051	
4	15	1	1	aisle	60B051	
5	0	1	1	port	ABD6FF	B1
5	1	1	1	port	ABD6FF	B2
5	2	1	1	port	ABD6FF	B3
5	3	1	1	port	ABD6FF	B4
5	4	1	1	port	ABD6FF	B5
5	5	1	1	port	ABD6FF	B6
5	6	1	1	port	ABD6FF	B7
5	7	1	1	aisle	60B051	
5	8	1	1	port	ABD6FF	K1
5	9	1	1	port	ABD6FF	K2
5	10	1	1	port	ABD6FF	K3
5	11	1	1	port	ABD6FF	K4
5	12	1	1	port	ABD6FF	K5
5	13	1	1	port	ABD6FF	K6
5	14	1	1	port	ABD6FF	K7
5	15	1	1	aisle	60B051	
6	0	1	1	port	ABD6FF	B8
6	1	1	1	port	ABD6FF	B9
6	2	1	1	port	ABD6FF	B10
6	3	1	1	port	ABD6FF	B11
6	4	1	1	port	ABD6FF	B12
6	5	1	1	port	ABD6FF	B13
6	6	1	1	port	ABD6FF	B14
6	7	1	1	aisle	60B051	
6	8	1	1	port	ABD6FF	K8
6	9	1	1	port	ABD6FF	K9
6	10	1	1	port	ABD6FF	K10
6	11	1	1	port	ABD6FF	K11
6	12	1	1	port	ABD6FF	K12
6	13	1	1	port	ABD6FF	K13
6	14	1	1	port	ABD6FF	K14
6	15	1	1	aisle	60B051	
7	0	1	1	aisle	60B051	
7	1	1	1	aisle	60B051	
7	2	1	1	aisle	60B051	
7	3	1	1	aisle	60B051	
7	4	1	1	aisle	60B051	
7	5	1	1	aisle	60B051	
7	6	1	1	aisle	60B051	
7	7	1	1	aisle	60B051	
7	8	1	1	aisle	60B051	
7	9	1	1	aisle	60B051	
7	10	1	1	aisle	60B051	
7	11	1	1	aisle	60B051	
7	12	1	1	aisle	60B051	
7	13	1	1	aisle	60B051	
7	14	1	1	aisle	60B051	
7	15	1	1	aisle	60B051	
7	16	1	1	aisle	60B051	
7	17	1	1	aisle	60B051	
7	18	1	1	aisle	60B051	
7	19	1	1	aisle	60B051	
8	0	1	1	port	ABD6FF	C1
8	1	1	1	port	ABD6FF	C2
8	2	1	1	port	ABD6FF	C3
8	3	1	1	port	ABD6FF	C4
8	4	1	1	port	ABD6FF	C5
8	5	1	1	port	ABD6FF	C6
8	6	1	1	port	ABD6FF	C7
8	7	1	1	aisle	60B051	
8	8	1	1	zone	FEBED9	
8	9	1	1	zone	FEBED9	
8	10	1	1	zone	FEBED9	
8	11	1	1	zone	FEBED9	
8	12	1	1	zone	FEBED9	
8	13	1	1	zone	FEBED9	
8	14	1	1	aisle	60B051	
8	15	1	1	aisle	60B051	
8	16	1	1	aisle	60B051	
8	17	1	1	aisle	C3EBE0	
8	18	1	1	aisle	C3EBE0	
8	19	1	1	aisle	C3EBE0	
9	0	1	1	port	ABD6FF	C8
9	1	1	1	port	ABD6FF	C9
9	2	1	1	port	ABD6FF	C10
9	3	1	1	port	ABD6FF	C11
9	4	1	1	port	ABD6FF	C12
9	5	1	1	port	ABD6FF	C13
9	6	1	1	port	ABD6FF	C14
9	7	1	1	aisle	60B051	
9	8	1	1	port	ABD6FF	L1
9	9	1	1	port	ABD6FF	L2
9	10	1	1	port	ABD6FF	L3
9	11	1	1	port	ABD6FF	L4
9	12	1	1	port	ABD6FF	L5
9	13	1	1	port	ABD6FF	L6
9	14	1	1	port	ABD6FF	L7
9	15	1	1	aisle	60B051	
9	16	1	1	aisle	60B051	
9	17	1	1	aisle	C3EBE0	
9	18	1	1	aisle	C3EBE0	
9	19	1	1	aisle	C3EBE0	
10	0	1	1	aisle	60B051	
10	1	1	1	aisle	60B051	
10	2	1	1	aisle	60B051	
10	3	1	1	aisle	60B051	
10	4	1	1	aisle	60B051	
10	5	1	1	aisle	60B051	
10	6	1	1	aisle	60B051	
10	7	1	1	aisle	60B051	
10	8	1	1	aisle	60B051	
10	9	1	1	aisle	60B051	
10	10	1	1	aisle	60B051	
10	11	1	1	aisle	60B051	
10	12	1	1	aisle	60B051	
10	13	1	1	aisle	60B051	
10	14	1	1	aisle	60B051	
10	15	1	1	aisle	60B051	
10	16	1	1	aisle	60B051	
10	17	1	1	aisle	C3EBE0	
10	18	1	1	aisle	C3EBE0	
10	19	1	1	aisle	C3EBE0	
11	0	1	1	port	ABD6FF	D1
11	1	1	1	port	ABD6FF	D2
11	2	1	1	port	ABD6FF	D3
11	3	1	1	port	ABD6FF	D4
11	4	1	1	port	ABD6FF	D5
11	5	1	1	port	ABD6FF	D6
11	6	1	1	port	ABD6FF	D7
11	7	1	1	aisle	60B051	
11	8	1	1	port	ABD6FF	M1
11	9	1	1	port	ABD6FF	M2
11	10	1	1	port	ABD6FF	M3
11	11	1	1	port	ABD6FF	M4
11	12	1	1	port	ABD6FF	M5
11	13	1	1	port	ABD6FF	M6
11	14	1	1	port	ABD6FF	M7
11	15	1	1	aisle	60B051	
11	16	1	1	aisle	60B051	
11	17	1	1	aisle	C3EBE0	
11	18	1	1	aisle	C3EBE0	
11	19	1	1	aisle	C3EBE0	
12	0	1	1	port	ABD6FF	D8
12	1	1	1	port	ABD6FF	D9
12	2	1	1	port	ABD6FF	D10
12	3	1	1	port	ABD6FF	D11
12	4	1	1	port	ABD6FF	D12
12	5	1	1	port	ABD6FF	D13
12	6	1	1	port	ABD6FF	D14
12	7	1	1	aisle	60B051	
12	8	1	1	port	ABD6FF	M8
12	9	1	1	port	ABD6FF	M9
12	10	1	1	port	ABD6FF	M10
12	11	1	1	port	ABD6FF	M11
12	12	1	1	port	ABD6FF	M12
12	13	1	1	port	ABD6FF	M13
12	14	1	1	port	ABD6FF	M14
12	15	1	1	aisle	60B051	
12	16	1	1	aisle	60B051	
12	17	1	1	aisle	C3EBE0	
12	18	1	1	aisle	C3EBE0	
12	19	1	1	aisle	C3EBE0	
13	0	1	1	aisle	60B051	
13	1	1	1	aisle	60B051	
13	2	1	1	aisle	60B051	
13	3	1	1	aisle	60B051	
13	4	1	1	aisle	60B051	
13	5	1	1	aisle	60B051	
13	6	1	1	aisle	60B051	
13	7	1	1	aisle	60B051	
13	8	1	1	aisle	60B051	
13	9	1	1	aisle	60B051	
13	10	1	1	aisle	60B051	
13	11	1	1	aisle	60B051	
13	12	1	1	aisle	60B051	
13	13	1	1	aisle	60B051	
13	14	1	1	aisle	60B051	
13	15	1	1	aisle	60B051	
13	16	1	1	aisle	60B051	
13	17	1	1	aisle	C3EBE0	
13	18	1	1	aisle	C3EBE0	
13	19	1	1	aisle	C3EBE0	
14	0	1	1	port	ABD6FF	E1
14	1	1	1	port	ABD6FF	E2
14	2	1	1	port	ABD6FF	E3
14	3	1	1	port	ABD6FF	E4
14	4	1	1	port	ABD6FF	E5
14	5	1	1	port	ABD6FF	E6
14	6	1	1	port	ABD6FF	E7
14	7	1	1	aisle	60B051	
14	8	1	1	port	ABD6FF	L8
14	9	1	1	port	ABD6FF	L9
14	10	1	1	port	ABD6FF	L10
14	11	1	1	port	ABD6FF	L11
14	12	1	1	port	ABD6FF	L12
14	13	1	1	port	ABD6FF	L13
14	14	1	1	port	ABD6FF	L14
14	15	1	1	aisle	60B051	
14	16	1	1	aisle	C3EBE0	
14	17	1	1	zone	FEBED9	
14	18	1	1	zone	FEBED9	
14	19	1	1	zone	FEBED9	
15	0	1	1	aisle	60B051	
15	1	1	1	aisle	60B051	
15	2	1	1	aisle	60B051	
15	3	1	1	aisle	60B051	
15	4	1	1	aisle	60B051	
15	5	1	1	aisle	60B051	
15	6	1	1	aisle	60B051	
15	7	1	1	aisle	60B051	
15	8	1	1	aisle	60B051	
15	9	1	1	aisle	60B051	
15	10	1	1	aisle	60B051	
15	11	1	1	aisle	60B051	
15	12	1	1	aisle	60B051	
15	13	1	1	aisle	60B051	
15	14	1	1	aisle	60B051	
15	15	1	1	aisle	60B051	
15	16	1	1	aisle	C3EBE0	
15	17	1	1	zone	FEBED9	
15	18	1	1	zone	FEBED9	
15	19	1	1	zone	FEBED9	
16	0	1	1	port	ABD6FF	F1
16	1	1	1	port	ABD6FF	F2
16	2	1	1	port	ABD6FF	F3
16	3	1	1	port	ABD6FF	F4
16	4	1	1	port	ABD6FF	F5
16	5	1	1	port	ABD6FF	F6
16	6	1	1	port	ABD6FF	F7
16	7	1	1	aisle	60B051	
16	8	1	1	port	ABD6FF	N1
16	9	1	1	port	ABD6FF	N2
16	10	1	1	port	ABD6FF	N3
16	11	1	1	port	ABD6FF	N4
16	12	1	1	port	ABD6FF	N5
16	13	1	1	port	ABD6FF	N6
16	14	1	1	port	ABD6FF	N7
16	15	1	1	aisle	60B051	
16	16	3	1	zone	F9DE18	机房
16	17	1	1	zone	FEBED9	
16	18	1	1	zone	FEBED9	
16	19	1	1	zone	FEBED9	
17	0	1	1	port	ABD6FF	F8
17	1	1	1	port	ABD6FF	F9
17	2	1	1	port	ABD6FF	F10
17	3	1	1	port	ABD6FF	F11
17	4	1	1	port	ABD6FF	F12
17	5	1	1	port	ABD6FF	F13
17	6	1	1	port	ABD6FF	F14
17	7	1	1	aisle	60B051	
17	8	1	1	port	ABD6FF	N8
17	9	1	1	port	ABD6FF	N9
17	10	1	1	port	ABD6FF	N10
17	11	1	1	port	ABD6FF	N11
17	12	1	1	port	ABD6FF	N12
17	13	1	1	port	ABD6FF	N13
17	14	1	1	port	ABD6FF	N14
17	15	1	1	aisle	60B051	
17	17	1	1	zone	FEBED9	
17	18	1	1	zone	FEBED9	
17	19	1	1	zone	FEBED9	
18	0	1	1	aisle	60B051	
18	1	1	1	aisle	60B051	
18	2	1	1	aisle	60B051	
18	3	1	1	aisle	60B051	
18	4	1	1	aisle	60B051	
18	5	1	1	aisle	60B051	
18	6	1	1	aisle	60B051	
18	7	1	1	aisle	60B051	
18	8	1	1	aisle	60B051	
18	9	1	1	aisle	60B051	
18	10	1	1	aisle	60B051	
18	11	1	1	aisle	60B051	
18	12	1	1	aisle	60B051	
18	13	1	1	aisle	60B051	
18	14	1	1	aisle	60B051	
18	15	1	1	aisle	60B051	
18	17	1	1	zone	FEBED9	
18	18	1	1	zone	FEBED9	
18	19	1	1	zone	FEBED9	
19	0	1	1	port	ABD6FF	
19	1	1	1	port	ABD6FF	
19	2	1	1	port	ABD6FF	
19	3	1	1	port	ABD6FF	
19	4	1	1	port	ABD6FF	
19	5	1	1	port	ABD6FF	
19	6	1	1	port	ABD6FF	
19	7	1	1	aisle	60B051	
19	8	1	1	port	ABD6FF	
19	9	1	1	port	ABD6FF	
19	10	1	1	port	ABD6FF	
19	11	1	1	port	ABD6FF	
19	12	1	1	port	ABD6FF	
19	13	1	1	port	ABD6FF	
19	14	1	1	port	ABD6FF	
19	15	1	1	aisle	60B051	
19	16	1	1	aisle	60B051	
19	17	1	1	zone	FEBED9	
19	18	1	1	zone	FEBED9	
19	19	1	1	zone	FEBED9	
20	0	1	1	port	ABD6FF	1
20	1	1	1	port	ABD6FF	2
20	2	1	1	port	ABD6FF	3
20	3	1	1	port	ABD6FF	4
20	4	1	1	port	ABD6FF	5
20	5	1	1	port	ABD6FF	6
20	6	1	1	port	ABD6FF	7
20	7	1	1	aisle	60B051	
20	8	1	1	port	ABD6FF	8
20	9	1	1	port	ABD6FF	9
20	10	1	1	port	ABD6FF	10
20	11	1	1	port	ABD6FF	11
20	12	1	1	port	ABD6FF	12
20	13	1	1	port	ABD6FF	13
20	14	1	1	port	ABD6FF	14
20	15	1	1	aisle	60B051	
20	16	1	1	aisle	60B051	
20	17	1	1	zone	FEBED9	
20	18	1	1	zone	FEBED9	
20	19	1	1	zone	FEBED9	
21	0	1	1	aisle	60B051	
21	1	1	1	aisle	60B051	
21	2	1	1	aisle	60B051	
21	3	1	1	aisle	60B051	
21	4	1	1	aisle	60B051	
21	5	1	1	aisle	60B051	
21	6	1	1	aisle	60B051	
21	7	1	1	aisle	60B051	
21	8	1	1	aisle	60B051	
21	9	1	1	aisle	60B051	
21	10	1	1	aisle	60B051	
21	11	1	1	aisle	60B051	
21	12	1	1	aisle	60B051	
21	13	1	1	aisle	60B051	
21	14	1	1	aisle	60B051	
21	15	1	1	aisle	60B051	
21	16	1	1	aisle	60B051	
21	17	1	1	zone	FEBED9	
21	18	1	1	zone	FEBED9	
21	19	1	1	zone	FEBED9	"""


def _seed_cells() -> List[dict]:
    out: List[dict] = []
    for ln in _SEED_TSV.strip().splitlines()[1:]:
        if not ln.strip():
            continue
        r, c, rs, cs, kind, bg, label = (ln.split("\t") + [""] * 7)[:7]
        out.append({
            "row": int(r), "col": int(c),
            "row_span": int(rs), "col_span": int(cs),
            "label": label, "kind": kind, "bg_color": "#" + bg,
            "fields": {"status": "空闲"} if kind == "port" else {},
        })
    return out


def seed_map(db: Session, name="4F 办公区", floor="4F") -> PortMap:
    # cols=20：源表 col20–23 整列为空（无填色），按内容裁掉，画布不留死白
    m = PortMap(name=name, floor=floor, rows=22, cols=20, cell_w=76, cell_h=51,
                display_field="status", legend=[], creator="admin",
                remark="由 座位图.xlsx 自动铺底，可自由增删格与改色")
    db.add(m)
    db.flush()
    for i, c in enumerate(_seed_cells()):
        db.add(PortCell(map_id=m.id, sort_order=i, **c))
    db.commit()
    db.refresh(m)
    return m


# ---------------- 请求体 ----------------
class MapIn(BaseModel):
    name: Optional[str] = None
    floor: Optional[str] = None
    rows: Optional[int] = None
    cols: Optional[int] = None
    cell_w: Optional[int] = None
    cell_h: Optional[int] = None
    display_field: Optional[str] = None
    remark: Optional[str] = None


class CellIn(BaseModel):
    map_id: int
    row: int = 0
    col: int = 0
    row_span: Optional[int] = 1
    col_span: Optional[int] = 1
    label: Optional[str] = ""
    kind: Optional[str] = "port"
    bg_color: Optional[str] = ""
    display_field: Optional[str] = ""
    fields: Optional[Dict[str, Any]] = None


class CellPatch(BaseModel):
    row: Optional[int] = None
    col: Optional[int] = None
    row_span: Optional[int] = None
    col_span: Optional[int] = None
    label: Optional[str] = None
    kind: Optional[str] = None
    bg_color: Optional[str] = None
    display_field: Optional[str] = None
    fields: Optional[Dict[str, Any]] = None


class MergeIn(BaseModel):
    ids: List[int]


class BulkIn(BaseModel):
    ids: List[int]
    patch: CellPatch
    # 追加动作：clear_bg=清空填充色；color=整片覆盖色（等价 patch.bg_color）


# ---------------- 撤回（整图快照，后进先出） ----------------
UNDO_KEEP = 12          # 每张图最多保留 12 步
_SNAP_FIELDS = ("row", "col", "row_span", "col_span", "label", "kind",
                "bg_color", "display_field", "fields")


def _snapshot(db: Session, map_id: int, action: str) -> None:
    """把该图当前全部格子存一份快照，供「撤回」还原。须与后续写操作同一事务。"""
    cs = (db.query(PortCell).filter(PortCell.map_id == map_id)
          .order_by(PortCell.row, PortCell.col).all())
    payload = []
    for c in cs:
        item = {}
        for k in _SNAP_FIELDS:
            v = getattr(c, k)
            item[k] = (v or {}) if k == "fields" else v
        payload.append(item)
    db.add(PortMapUndo(map_id=map_id, action=action, payload=payload))
    db.flush()
    olds = (db.query(PortMapUndo).filter(PortMapUndo.map_id == map_id)
            .order_by(PortMapUndo.id.desc()).offset(UNDO_KEEP).all())
    for o in olds:
        db.delete(o)


# ---------------- 图纸 ----------------
@router.get("/maps")
def list_maps(db: Session = Depends(get_db)):
    if db.query(PortMap).count() == 0:
        # 首次访问自动铺底图（幂等：只在没有任何图纸时执行）
        try:
            seed_map(db)
        except Exception:
            db.rollback()
    ms = db.query(PortMap).order_by(PortMap.sort_order, PortMap.id).all()
    cnt = dict(
        db.query(PortCell.map_id, func.count(PortCell.id))
        .group_by(PortCell.map_id).all()
    )
    return {"ok": True, "data": [map_dict(m, cnt.get(m.id, 0)) for m in ms]}


@router.post("/maps", dependencies=[Depends(require_admin)])
def create_map(data: MapIn, db: Session = Depends(get_db)):
    m = PortMap(
        name=(data.name or "新图纸").strip() or "新图纸",
        floor=data.floor or "",
        rows=max(1, min(data.rows or 22, 200)),
        cols=max(1, min(data.cols or 24, 60)),
        cell_w=max(28, min(data.cell_w or 76, 200)),
        cell_h=max(20, min(data.cell_h or 51, 160)),
        display_field=data.display_field or "",
        remark=data.remark or "",
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return {"ok": True, "data": map_dict(m, 0)}


@router.put("/maps/{mid}", dependencies=[Depends(require_admin)])
def update_map(mid: int, data: MapIn, db: Session = Depends(get_db)):
    m = db.get(PortMap, mid)
    if not m:
        raise HTTPException(404, "图纸不存在")
    for k, v in data.model_dump(exclude_unset=True).items():
        if v is None:
            continue
        if k in ("rows", "cols", "cell_w", "cell_h"):
            v = int(v)
        setattr(m, k, v)
    db.commit()
    return {"ok": True, "data": map_dict(m)}


@router.delete("/maps/{mid}", dependencies=[Depends(require_admin)])
def delete_map(mid: int, db: Session = Depends(get_db)):
    m = db.get(PortMap, mid)
    if not m:
        raise HTTPException(404, "图纸不存在")
    db.query(PortCell).filter(PortCell.map_id == mid).delete(synchronize_session=False)
    db.delete(m)
    db.commit()
    return {"ok": True, "deleted": mid}


@router.get("/maps/{mid}")
def get_map(mid: int, db: Session = Depends(get_db)):
    m = db.get(PortMap, mid)
    if not m:
        raise HTTPException(404, "图纸不存在")
    cs = (db.query(PortCell)
          .filter(PortCell.map_id == mid)
          .order_by(PortCell.sort_order, PortCell.row, PortCell.col).all())
    undo_left = db.query(PortMapUndo).filter(PortMapUndo.map_id == mid).count()
    last_undo = (db.query(PortMapUndo).filter(PortMapUndo.map_id == mid)
                 .order_by(PortMapUndo.id.desc()).first())
    return {
        "ok": True,
        "data": map_dict(m, len(cs)),
        "undo_count": undo_left,
        "undo_action": (last_undo.action if last_undo else ""),
        "cells": [cell_dict(c) for c in cs],
        "field_defs": [{"key": k, "label": lb} for k, lb in PORT_FIELDS],
        "kind_colors": KIND_COLORS,
        "status_colors": STATUS_COLORS,
    }


@router.post("/maps/{mid}/reset", dependencies=[Depends(require_admin)])
def reset_map(mid: int, db: Session = Depends(get_db)):
    """重建为座位图底图（先删该图全部格子，再按种子重铺）。"""
    m = db.get(PortMap, mid)
    if not m:
        raise HTTPException(404, "图纸不存在")
    _snapshot(db, mid, "还原原图")
    db.query(PortCell).filter(PortCell.map_id == mid).delete(synchronize_session=False)
    for i, c in enumerate(_seed_cells()):
        db.add(PortCell(map_id=mid, sort_order=i, **c))
    db.commit()
    n = db.query(PortCell).filter(PortCell.map_id == mid).count()
    return {"ok": True, "cells": n}


@router.post("/seed", dependencies=[Depends(require_admin)])
def add_seed_map(db: Session = Depends(get_db)):
    m = seed_map(db, name="4F 办公区（副本）", floor="4F")
    return {"ok": True, "data": map_dict(m)}


# ---------------- 格子 ----------------
def _apply_patch(c: PortCell, p: CellPatch):
    d = p.model_dump(exclude_unset=True)
    for k in ("row", "col", "row_span", "col_span"):
        if k in d and d[k] is not None:
            setattr(c, k, max(1, int(d[k])) if k.endswith("span") else int(d[k]))
    if d.get("label") is not None:
        c.label = d["label"]
    if d.get("kind") is not None:
        c.kind = d["kind"]
    if d.get("bg_color") is not None:
        c.bg_color = d["bg_color"]
    if d.get("display_field") is not None:
        c.display_field = d["display_field"]
    if d.get("fields") is not None:
        merged = dict(c.fields or {})
        merged.update(d["fields"])
        c.fields = merged


@router.post("/cells", dependencies=[Depends(require_admin)])
def create_cell(data: CellIn, db: Session = Depends(get_db)):
    if not db.get(PortMap, data.map_id):
        raise HTTPException(404, "图纸不存在")
    c = PortCell(
        map_id=data.map_id, row=data.row, col=data.col,
        row_span=data.row_span or 1, col_span=data.col_span or 1,
        label=data.label or "", kind=data.kind or "port",
        bg_color=data.bg_color or "", display_field=data.display_field or "",
        fields=data.fields or ({"status": "空闲"} if (data.kind or "port") == "port" else {}),
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return {"ok": True, "data": cell_dict(c)}


@router.put("/cells/{cid}", dependencies=[Depends(require_admin)])
def update_cell(cid: int, data: CellPatch, db: Session = Depends(get_db)):
    c = db.get(PortCell, cid)
    if not c:
        raise HTTPException(404, "格子不存在")
    _apply_patch(c, data)
    db.commit()
    db.refresh(c)
    return {"ok": True, "data": cell_dict(c)}


@router.delete("/cells/{cid}", dependencies=[Depends(require_admin)])
def delete_cell(cid: int, db: Session = Depends(get_db)):
    c = db.get(PortCell, cid)
    if not c:
        raise HTTPException(404, "格子不存在")
    db.delete(c)
    db.commit()
    return {"ok": True, "deleted": cid}


@router.post("/cells/bulk", dependencies=[Depends(require_admin)])
def bulk_update(data: BulkIn, db: Session = Depends(get_db)):
    """批量改色 / 批量改字段 / 批量清填充。单事务，任一条失败整体回滚。"""
    if not data.ids:
        return {"ok": True, "updated": 0}
    cs = db.query(PortCell).filter(PortCell.id.in_(data.ids)).all()
    for c in cs:
        _apply_patch(c, data.patch)
    db.commit()
    return {"ok": True, "updated": len(cs), "data": [cell_dict(c) for c in cs]}


@router.post("/cells/merge", dependencies=[Depends(require_admin)])
def merge_cells(data: MergeIn, db: Session = Depends(get_db)):
    """把选中的多个格子合并成一个大格。
    区域 = 选区包围盒；内容保留「左上角那一格」，其余格子删除（等同 Excel 合并）。
    """
    ids = sorted(set(data.ids))
    if len(ids) < 2:
        raise HTTPException(400, "至少需要选中 2 个格子")
    cs = db.query(PortCell).filter(PortCell.id.in_(ids)).all()
    if len(cs) != len(ids):
        raise HTTPException(404, "部分格子已不存在，请刷新后重试")
    if len({c.map_id for c in cs}) != 1:
        raise HTTPException(400, "只能合并同一张图纸里的格子")

    r0 = min(c.row for c in cs)
    c0 = min(c.col for c in cs)
    r1 = max(c.row + (c.row_span or 1) - 1 for c in cs)
    c1 = max(c.col + (c.col_span or 1) - 1 for c in cs)

    # ⚠ 关键：包围盒内「未被选中」的格子也必须吸收掉，否则大格会盖在它们上面，
    #   之后拆分又会按包围盒铺满 ⇒ 格子层层重叠（2026-11-05 线上事故根因）
    _snapshot(db, cs[0].map_id, "合并")
    same_map = db.query(PortCell).filter(PortCell.map_id == cs[0].map_id).all()
    inside = [
        c for c in same_map
        if c.row <= r1 and (c.row + (c.row_span or 1) - 1) >= r0
        and c.col <= c1 and (c.col + (c.col_span or 1) - 1) >= c0
    ]
    anchor = next((c for c in inside if c.row == r0 and c.col == c0), None)
    if anchor is None:
        anchor = sorted(inside, key=lambda c: (c.row, c.col))[0]
        anchor.row, anchor.col = r0, c0
    anchor.row_span = r1 - r0 + 1
    anchor.col_span = c1 - c0 + 1

    removed = []
    for c in inside:
        if c.id != anchor.id:
            removed.append(c.id)
            db.delete(c)
    db.commit()
    db.refresh(anchor)
    return {"ok": True, "data": cell_dict(anchor), "removed": removed,
            "absorbed": len(removed)}


@router.post("/cells/{cid}/split", dependencies=[Depends(require_admin)])
def split_cell(cid: int, db: Session = Depends(get_db)):
    """把合并格拆回单格：内容留在左上角，其余位置新建空网格子（底色与类型沿用）。"""
    c = db.get(PortCell, cid)
    if not c:
        raise HTTPException(404, "格子不存在")
    rs = c.row_span or 1
    csl = c.col_span or 1
    if rs == 1 and csl == 1:
        raise HTTPException(400, "该格子没有合并，无需拆分")

    _snapshot(db, c.map_id, "拆分")

    # 已被别的格子占住的位置不再重复创建（防御历史脏数据导致的重叠）
    taken = set()
    for o in db.query(PortCell).filter(PortCell.map_id == c.map_id, PortCell.id != c.id).all():
        for dr in range(o.row_span or 1):
            for dc in range(o.col_span or 1):
                taken.add((o.row + dr, o.col + dc))

    out = [c]
    c.row_span = 1
    c.col_span = 1
    for dr in range(rs):
        for dc in range(csl):
            if dr == 0 and dc == 0:
                continue
            if (c.row + dr, c.col + dc) in taken:
                continue
            n = PortCell(
                map_id=c.map_id, row=c.row + dr, col=c.col + dc,
                row_span=1, col_span=1, label="", kind=c.kind,
                bg_color=c.bg_color or "", display_field="",
                fields={"status": "空闲"} if c.kind == "port" else {},
            )
            db.add(n)
            out.append(n)
    db.commit()
    for x in out:
        db.refresh(x)
    return {"ok": True, "data": [cell_dict(x) for x in out]}


@router.post("/maps/{mid}/undo", dependencies=[Depends(require_admin)])
def undo_last(mid: int, db: Session = Depends(get_db)):
    """撤回上一步（合并 / 拆分 / 还原原图），恢复该步之前的整图快照。"""
    if not db.get(PortMap, mid):
        raise HTTPException(404, "图纸不存在")
    last = (db.query(PortMapUndo).filter(PortMapUndo.map_id == mid)
            .order_by(PortMapUndo.id.desc()).first())
    if not last:
        raise HTTPException(400, "没有可撤回的操作了")
    action = last.action or "操作"
    snap = last.payload or []
    db.delete(last)
    db.query(PortCell).filter(PortCell.map_id == mid).delete(synchronize_session=False)
    for i, c in enumerate(snap):
        db.add(PortCell(
            map_id=mid,
            row=int(c.get("row") or 0), col=int(c.get("col") or 0),
            row_span=int(c.get("row_span") or 1), col_span=int(c.get("col_span") or 1),
            label=c.get("label") or "", kind=c.get("kind") or "port",
            bg_color=c.get("bg_color") or "", display_field=c.get("display_field") or "",
            fields=c.get("fields") or {}, sort_order=i,
        ))
    db.commit()
    left = db.query(PortMapUndo).filter(PortMapUndo.map_id == mid).count()
    return {"ok": True, "action": action, "cells": len(snap), "left": left}


# ============================================================
# 大屏公开接口（免登录、只读）
#   ⚠ 挂在 /api/portmap/public 下，**不带 get_current_user 依赖**，
#     供外网大屏 / 会议室电视直接 GET。默认只回「编号 + 状态 + 颜色」，
#     使用人/部门/MAC/IP 等敏感字段必须显式传 detail=1 才返回。
# ============================================================
public_router = APIRouter(prefix="/api/portmap/public", tags=["工位网口图-大屏"])


@public_router.get("/maps")
def public_maps(db: Session = Depends(get_db)):
    ms = db.query(PortMap).order_by(PortMap.sort_order, PortMap.id).all()
    cnt = dict(
        db.query(PortCell.map_id, func.count(PortCell.id))
        .group_by(PortCell.map_id).all()
    )
    return {"ok": True, "data": [{
        "id": m.id, "name": m.name, "floor": m.floor or "",
        "rows": m.rows, "cols": m.cols, "cell_count": cnt.get(m.id, 0),
        "display_field": m.display_field or "",
    } for m in ms]}


@public_router.get("/maps/{mid}")
def public_map(mid: int, detail: int = 0, db: Session = Depends(get_db)):
    m = db.get(PortMap, mid)
    if not m:
        raise HTTPException(404, "图纸不存在")
    cs = (db.query(PortCell).filter(PortCell.map_id == mid)
          .order_by(PortCell.sort_order, PortCell.row, PortCell.col).all())
    map_df = (m.display_field or "").strip()      # 图纸级默认外显
    cells = []
    for c in cs:
        f = c.fields or {}
        # 「外显」生效字段：单格覆盖 > 图纸默认。大屏必须照这个显示，而不是一律显示状态
        df = (c.display_field or "").strip() or map_df
        dv = f.get(df) if df else None
        it = {
            "row": c.row, "col": c.col,
            "row_span": c.row_span or 1, "col_span": c.col_span or 1,
            "label": c.label or "", "kind": c.kind or "port",
            "bg_color": c.bg_color or "",
            "display_field": df,                       # 生效字段（已合并图纸默认）
            "display_value": "" if dv is None else str(dv),   # 该字段的值，免登录也返回
            "status": f.get("status") or "",
        }
        # 其余 7 项仍只有 detail=1 才给（避免使用人/MAC/IP 全量裸奔）
        it["fields"] = {k: v for k, v in f.items() if v} if detail else {}
        cells.append(it)
    return {
        "ok": True,
        "data": {
            "id": m.id, "name": m.name, "floor": m.floor or "",
            "rows": m.rows, "cols": m.cols,
            "cell_w": m.cell_w or 76, "cell_h": m.cell_h or 51,
            "display_field": m.display_field or "",
            "updated_at": m.updated_at.strftime("%Y-%m-%d %H:%M:%S") if m.updated_at else "",
        },
        "cells": cells,
        "field_defs": [{"key": k, "label": lb} for k, lb in PORT_FIELDS],
        "status_colors": STATUS_COLORS,
        "kind_colors": KIND_COLORS,
    }
