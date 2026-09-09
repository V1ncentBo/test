==============================================================
 智能监控平台 运维操作手册（monitor-platform @ 192.168.1.66）
 整理日期: 2026-09-09        适用: 全体运维/值班人员
==============================================================

一、这台机器上跑着什么
--------------------------------------------------------------
Docker 容器（compose 文件: /opt/monitor-platform/docker-compose.yml）
  monitor-mysql        MySQL8 业务库（root，数据在卷 monitor-platform_mysql_data）
  monitor-influxdb     InfluxDB2.7 监控时序数据（数据在卷 monitor-platform_influxdb_data）
  monitor-backend      FastAPI 后端 :8000（代码挂载 /opt/monitor-platform/backend，改文件即自动生效）
  monitor-frontend     nginx 前端 :80（镜像 monitor-frontend:optimized，代码在镜像里）
  monitor-collector    采集器（UTC 时区）

定时任务（root crontab）
  每天 03:10  MySQL 自动备份 -> /opt/monitor-platform/backups/mysql/（保留 14 天）
  每小时     InfluxDB 降采样（原始数据留 30 天，小时均值永久保留在 metrics_hourly 桶）

一键脚本: /opt/monitor-platform/tools/platform_ctl.sh

二、日常命令（背下这一节就够了）
--------------------------------------------------------------
cd /opt/monitor-platform

1) 看状态:        ./tools/platform_ctl.sh status
2) 全栈体检:      ./tools/platform_ctl.sh verify      <- 任何"感觉不对"先跑这个
3) 冷启动(开机后): ./tools/platform_ctl.sh start       <- 其实开机全自动，此命令兜底
4) 全部滚动重启:   ./tools/platform_ctl.sh restart     <- 按 mysql→influxdb→backend→frontend→collector 顺序
5) 只重启单个服务: ./tools/platform_ctl.sh restart backend
6) 看日志:        docker logs -f --tail 100 monitor-backend

三、宕机/断电后怎么恢复
--------------------------------------------------------------
正常情况下【什么都不用做】:
  docker 已设开机自启，5 个容器 restart:always，约 1~2 分钟自动全部拉起。
  开机后执行 ./tools/platform_ctl.sh verify，看到 "=== 体检全部通过 ===" 即收工。

若 verify 有 FAIL:
  ./tools/platform_ctl.sh start     (按依赖顺序强制拉起 + 再体检)
  仍失败 -> docker logs <容器名> 看报错，联系平台负责人。

四、绝对禁止（做了才会真出事故）
--------------------------------------------------------------
  ✗ docker compose down -v          # -v 会删掉 MySQL/InfluxDB 数据卷 = 清库！
  ✗ docker compose up --build       # 会用落后的旧源码重建镜像，抹掉全部热修
  ✗ 删除 monitor-frontend:optimized 镜像/tag（前端代码全在里面）
  ✗ 直接改容器里的 index.html（前端定制已外置，见第五节）

五、前端热修流程（改页面功能找这里）
--------------------------------------------------------------
前端定制代码已外置，改这两个文件（不要动 index.html，它只有 23 行壳）:
  宿主机持久副本: /opt/monitor-platform/frontend-custom/mc-custom.js / mc-custom.css
  容器内位置:     /usr/share/nginx/html/custom/（同名文件）
流程: 改宿主机副本 -> docker cp 进容器 -> curl 校验 HTTP 200 -> 
      docker commit monitor-frontend monitor-frontend:optimized（不 commit 重启就丢！）
独立注入页(admin-users/admin-options/resources/cabinets/physical.html)在容器
/usr/share/nginx/html/ 下，同样改完要 docker commit。
注入页可用直达链接: http://192.168.1.66/u/admin-users 等（可刷新可分享）。

六、后端改代码
--------------------------------------------------------------
backend 是挂载目录: /opt/monitor-platform/backend/ -> 容器 /app/
改文件即自动生效（uvicorn --reload），无需重启容器。
改之前备份: cp routers/xxx.py routers/xxx.py.bak-<日期>
改完体检: ./tools/platform_ctl.sh verify

七、备份与恢复
--------------------------------------------------------------
自动备份: /opt/monitor-platform/backups/mysql/monitor_platform_*.sql.gz（每天 03:10，留 14 天）
手工备份: /opt/monitor-platform/tools/backup_mysql.sh
恢复（先确认再执行！）:
  1) gunzip < 备份文件.gz > /tmp/restore.sql
  2) docker exec -i monitor-mysql mysql -uroot -p"$MYSQL_ROOT_PASSWORD" monitor_platform < /tmp/restore.sql
其他备份: /opt/monitor-platform/backups/assets_removed_20260909.tar.gz（历史 assets）

八、账号与权限模型（一句话版）
--------------------------------------------------------------
角色分 admin / user；user 按 15 项权限 key 精细授权（账号管理页勾选）。
后端读接口已按权限收紧（2026-09-09），写接口一律 admin。
前端未授权菜单自动隐藏；注入页支持 /u/<页名> 直达并自动做权限校验。
改权限相关后端代码前先跑矩阵测试，别把"全局组件依赖"的接口收紧
（resource-cmdb 三个读、/api/machines/ 列表、/api/metrics/dashboard —— 每页都调）。

九、故障速查表
--------------------------------------------------------------
现象                          -> 排查
--------------------------------------------------------------
页面打不开                    -> platform_ctl.sh verify; curl -I http://127.0.0.1/
页面开但数据不出              -> docker logs monitor-backend; curl 127.0.0.1:8000/api/auth/me 应 401
点菜单"点不动"                -> 刷新页面（F5）；仍不行查浏览器控制台报错
登录后马上被登出              -> 检查 backend 与 mysql 时间是否同步（TZ=Asia/Shanghai）
磁盘紧张                      -> docker system df; docker image prune -f（勿 -a！会删 optimized）
InfluxDB 占用大               -> 正常（362MB 量级）；原始点 30 天自动过期

十、关键路径速记
--------------------------------------------------------------
compose:        /opt/monitor-platform/docker-compose.yml
一键脚本:       /opt/monitor-platform/tools/{platform_ctl.sh, backup_mysql.sh}
前端定制:       /opt/monitor-platform/frontend-custom/
后端代码:       /opt/monitor-platform/backend/
备份:           /opt/monitor-platform/backups/
本手册:         /data/README.txt
==============================================================
