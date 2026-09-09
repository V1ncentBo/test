# monitor-platform · 智能监控平台

企业内网设备监控与 CMDB 平台：Proxmox VE 虚拟机/物理机监控、SNMP 交换机监控、告警通知、
AI 报表（日报/周报/月报）、资源台账（资源/硬件/项目/负责人/机房机柜）、账号权限体系。

> 生产环境: http://192.168.1.66 · 本仓库为生产版本的**脱敏镜像**（密钥一律走 `.env`，不入库）

## 架构

| 容器 | 技术栈 | 说明 |
|---|---|---|
| monitor-backend | FastAPI + SQLAlchemy + MySQL8 | REST API / WebSocket，`--reload` 热载 |
| monitor-frontend | nginx + Vue3 SPA + 注入式静态页 | 定制代码外置 `/custom/mc-custom.{js,css}` |
| monitor-mysql | MySQL 8.0 | 业务库 `monitor_platform` |
| monitor-influxdb | InfluxDB 2.7 | 指标时序（raw 30 天 + 小时均值永久） |
| monitor-collector | Python 采集器 | PVE/Node exporter/SNMP 采集 |

## 目录结构

```
backend/            FastAPI 后端（routers/services/models/websocket）
frontend-html/      SPA 壳(index.html) + 注入式独立页(admin-users/admin-options/resources/physical/cabinets)
frontend-custom/    前端定制资产 mc-custom.{js,css} + 重建后一键缝回工具 apply_custom.py
tools/              运维脚本 platform_ctl.sh(状态/体检/重启) backup_mysql.sh(每日备份)
docker-compose.yml  全栈编排（密钥全部引用 .env）
docs/               运维操作手册
.env.example        密钥模板（复制为 .env 填真实值）
```

## 部署

```bash
cp .env.example .env   # 填入真实密钥
docker compose up -d   # 按 mysql→influxdb→backend→frontend→collector 依赖顺序拉起
```

## 运维要点

- **一键管控**：`./tools/platform_ctl.sh {status|verify|start|restart}`（含健康检查等待与全栈体检）
- **宕机恢复**：docker 开机自启 + 容器 `restart: always`，断电重启零操作自动恢复
- **前端热修**：只改 `frontend-custom/mc-custom.{js,css}` → docker cp 进容器 → `docker commit monitor-frontend monitor-frontend:optimized`（不 commit 重启即丢）
- **注入页直达链接**：`http://<host>/u/admin-users`、`/u/admin-options`、`/u/cabinets` 等（可刷新/分享，自动权限校验）
- **⛔ 两条禁令**：禁止 `docker compose down -v`（删数据卷=清库）；禁止 `up --build`（旧源码重建抹掉热修）

## 权限模型

角色 `admin`/`user`，user 按 15 项权限 key 精细授权。后端 `auth.py` 提供
`require_any_perm(*keys)` 依赖工厂：读接口按权限收紧、写接口一律 admin。
全局组件依赖的端点（resource-cmdb 读、machines 列表、metrics/dashboard）对任意登录用户开放。

## 版本

- 2026-09-09：注入页真路由(/u/<page>)、assets 清理(375→31)、读接口最小权限收紧（修复报表计划匿名写漏洞）、InfluxDB 保留策略 30 天 + 每小时降采样、MySQL 每日备份 + 恢复演练
- 2026-09-08：侧栏高亮修复、modulepreload 性能优化
- 2026-09-07：资源管理 6 子模块权限拆分、设备管理三卡、nginx gzip/长缓存
