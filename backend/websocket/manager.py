"""WebSocket 实时数据推送管理"""
from fastapi import WebSocket
from typing import Dict, List
from datetime import datetime
import json
import asyncio


def _json_dumps(obj):
    """JSON 序列化，处理 datetime 等特殊类型"""
    return json.dumps(obj, default=str)


class ConnectionManager:
    """WebSocket 连接管理器"""

    def __init__(self):
        # machine_id -> [websocket connections]
        self.machine_connections: Dict[str, List[WebSocket]] = {}
        # dashboard connections (global)
        self.dashboard_connections: List[WebSocket] = []
        # alert connections
        self.alert_connections: List[WebSocket] = []

    async def connect_machine(self, websocket: WebSocket, machine_id: str):
        await websocket.accept()
        if machine_id not in self.machine_connections:
            self.machine_connections[machine_id] = []
        self.machine_connections[machine_id].append(websocket)

    def disconnect_machine(self, websocket: WebSocket, machine_id: str):
        if machine_id in self.machine_connections:
            self.machine_connections[machine_id] = [
                ws for ws in self.machine_connections[machine_id] if ws != websocket
            ]

    async def connect_dashboard(self, websocket: WebSocket):
        await websocket.accept()
        self.dashboard_connections.append(websocket)

    def disconnect_dashboard(self, websocket: WebSocket):
        self.dashboard_connections = [
            ws for ws in self.dashboard_connections if ws != websocket
        ]

    async def connect_alerts(self, websocket: WebSocket):
        await websocket.accept()
        self.alert_connections.append(websocket)

    def disconnect_alerts(self, websocket: WebSocket):
        self.alert_connections = [
            ws for ws in self.alert_connections if ws != websocket
        ]

    async def broadcast_metrics(self, machine_id: str, data: dict):
        """推送实时指标到该设备的所有监听客户端"""
        message = _json_dumps({"type": "metrics", "machine_id": machine_id, "data": data})
        if machine_id in self.machine_connections:
            dead = []
            for ws in self.machine_connections[machine_id]:
                try:
                    await ws.send_text(message)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self.disconnect_machine(ws, machine_id)

    async def broadcast_dashboard(self, data: dict):
        """推送大屏数据到所有大屏客户端"""
        message = _json_dumps({"type": "dashboard", "data": data})
        dead = []
        for ws in self.dashboard_connections:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect_dashboard(ws)

    async def broadcast_alert(self, alert_data: dict):
        """推送告警到所有告警客户端"""
        message = _json_dumps({"type": "alert", "data": alert_data})
        dead = []
        for ws in self.alert_connections:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect_alerts(ws)


ws_manager = ConnectionManager()
