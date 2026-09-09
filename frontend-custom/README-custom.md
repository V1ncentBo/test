# 前端定制资产（custom overlays）

> 这是监控平台前端**所有热修的唯一样式/脚本来源**。
> ⛔ **不要再直接编辑 `index.html`** —— 一切改动请进这里。

## 为什么会有这个目录

2026-07 ~ 2026-09，平台的侧栏重构、权限门控、视觉改造、账号管理高亮修复等
**十几批热修全部是以「改容器里的 index.html」的方式堆积起来的**（最臃肿时内联近 1000 行）。
结果是：

- `index.html` 与前端源码严重脱节，**任何一次全量构建都会把所有定制一次性抹掉**；
- 定制内容淹没在 SPA 入口 HTML 里，不可 diff、不可 review、不可回滚。

本目录把这些定制抽成两个独立文件，并配了注入器，从此：

| 场景 | 以前 | 现在 |
|---|---|---|
| 日常热修 | 改 55 KB 的 index.html，整文件上传再 commit | 改 `mc-custom.css` / `mc-custom.js` 单文件，上传 + commit |
| 前端全量重建 | 定制全丢，只能人工回忆重做 | 重建后跑一次注入器，定制全部复活 |
| 追查某条样式来源 | 在 index.html 里翻 900 行 | 按分块注释直接定位 |
| 回滚 | 整个 index.html 回退 | 单个文件回退，互不影响 |

## 文件说明

| 文件 | 说明 |
|---|---|
| `mc-custom.css` | 全部自定义样式（17 KB）：侧栏注入项、卡片语言、设备管理排版、去折叠按钮、去顶栏时间等 |
| `mc-custom.js` | 全部自定义脚本（39 KB）：侧栏层级注入、路由-权限门控、账号组件、注入页生命周期、设备管理三卡、导航高亮修复 |
| `apply_custom.py` | 重建后的一键注入器（幂等，可反复运行） |
| `README-custom.md` | 本文件 |

## 日常怎么改

热修只需三步（以 CSS 为例）：

```bash
# 1. 改文件（本地）
vi mc-custom.css

# 2. 传到宿主机
#    (Windows) python conn.py --put mc-custom.css /opt/monitor-platform/frontend-custom/mc-custom.css

# 3. 进容器 + 校验 + 固化
docker cp /opt/monitor-platform/frontend-custom/mc-custom.css \
          monitor-frontend:/usr/share/nginx/html/custom/mc-custom.css
docker exec monitor-frontend md5sum /usr/share/nginx/html/custom/mc-custom.css
docker commit monitor-frontend monitor-frontend:optimized
```

> 不用碰 `index.html`，不用重新启动容器 —— nginx 直接服务静态文件。
> 浏览器硬刷新（Ctrl+F5）即可看到效果：`/custom/` 走 `no-cache`，不会有长缓存挡住。

## 前端全量重建后怎么恢复

假设新的 dist 在 `/opt/monitor-platform/frontend/dist`：

```bash
cd /opt/monitor-platform/frontend-custom

# 先看会做什么（不写盘）
python3 apply_custom.py --dist ../frontend/dist --dry-run

# 确认无误，实际注入
python3 apply_custom.py --dist ../frontend/dist
```

注入器会做四件事：

1. 备份原 `index.html` → `index.html.orig-<时间戳>`；
2. 把 `mc-custom.css` / `mc-custom.js` 复制进 `dist/custom/`；
3. 在 `</head>` 前注入 `<link>`（含自动识别的 modulepreload）与 `</body>` 前注入 `<script>`；
4. 清掉旧的死 `importmap` 和**已过期的 preload**（避免指向失效的 hash 名）。

**幂等**：重复运行会自我保护并拒绝再次注入；确实要重写加 `--force`。

### modulepreload 是自动的

echarts vendor chunk 文件名带内容 hash（`index-<hash>.js`），每次构建都会变。
注入器按两条判据自动发现，不需要人工维护：

- echarts vendor：文件名 `index-*.js` 且体积 > 500 KB
- Dashboard 路由 chunk：`Dashboard-*.js` / `Dashboard-*.css`

> 以前这里是手工写在 index.html 里的固定文件名，重建后必然 404 —— 现已自动化。

## 抽取来源与保证

由 `_custom_extract.py` 从生产 index.html 抽取（2026-09-09），当时做了三重校验：

- 行覆盖：978 行中仅 1 行未归属（空行）；
- 内容等价：CSS 152 条、JS 698 条，**丢失 0、新增 0**；
- 瘦身后 index.html 无任何定制残留。

此后所有的验证基线是 `_navactive_verify.js`（侧栏高亮 21 项断言）。

## 注意事项

- `mc-custom.js` 内的东西**沿用了原有的 DOM 注入写法**（MutationObserver + 文本匹配），
  前端源码若改动侧栏文案（如把「设备管理」改名），注入逻辑会失效 —— 这是历史包袱，
  后续若要更稳，应推动前端源码原生支持这些定制入口。
- 不要把这些文件放进 `assets/`：`/assets/` 配了 `immutable` 长缓存，改了不会立即生效。
  `/custom/` 走 `no-cache`，改完刷新即生效。
