/* ===========================================================
   智能监控平台 · 前端定制脚本（mc-custom.js）
   ------------------------------------------------------------
   本文件由 tools/_custom_extract.py 从生产 index.html 抽取，
   是 2026-07 ~ 2026-09 所有前端热修的**唯一脚本来源**。
   包含：侧栏注入(监控中心/资源管理层级)、路由-权限门控、
        账号组件、注入页生命周期(enter/destroy)、
        设备管理三卡(MC-MACH-STATS)、账号管理高亮修复、
        注入页加载期的点击意图补做(_pending)。
   ⛔ 不要再直接改 index.html —— 一切改动进这里。
   由 tools/apply_custom.py 在「前端全量重建后」一键缝回 dist。
   =========================================================== */
/* ====ASSET-BODY-BEGIN==== */
/* ══ 主 IIFE：侧栏注入/权限门控/账号组件/注入页生命周期 ══ */
  (function () {
    'use strict';
    var ROOT_ID = 'res-main-root';
    var _active = '';
    var _busy = false;
    /* _routing：goRoute() 正在用 it.click() 驱动原生路由。capture 入口据此放行，
       否则那个 click 会被自己再拦一次（自调用）。 */
    var _routing = false;
    /* 注入页内联脚本在 document 上注册的监听器台账（记账见 enter()，摘除见 destroy()） */
    var _injLs = [];
    function releaseInjectedListeners() {
      if (!_injLs.length) return;
      _injLs.forEach(function (l) { try { document.removeEventListener(l[0], l[1], l[2]); } catch (e) {} });
      _injLs = [];
    }
    /* 注入页 fetch 期间收到的切换意图：记住最后一次，加载完立刻补做。
       ⚠ 不要直接 return 丢弃（2026-09-09 修复）：冷启动/慢网时 admin-users.html
       可能加载 >2s，此时用户点别的目录会毫无反应 —— 表现为"点不动"。 */
    var _pending = null;
    function flushPending() {
      if (!_pending) return;
      var p = _pending; _pending = null;
      if (p === _active) { afterBusySyncUser(); return; } // 已经落在这一页了：别把刚加载好的页再 toggle 关掉
      switchTo(p);
    }
    /* 连点补做后，若最终停在账号管理页，手工高亮必须仍在（_stress_pending T3 曾因
       toggleRc/markSub 的 clearNavActive 把高亮摘掉而偶发 hl=[]）。幂等、无副作用。 */
    function afterBusySyncUser() {
      if (_active === 'admin-users') { try { setHl(userItem()); } catch (e) {} }
    }
    var _saved = null;
    var _obs = null, _obsSide = null;

    /* ---- 注入页加载「防卡死」三件套（2026-09-17 修「点目录整页卡住」）----
       旧实现：enter() 先把 .main-area 的全部子节点 display:none、并显示空的注入容器，
       然后 fetch(注入页 HTML) —— 而那个 fetch **既无超时也无 abort**。只要连接假死
       （既不 resolve 也不 reject），_busy 就永远为 true：
         · 主区停在「空白」态（原内容已隐藏、注入容器还是空的）
         · switchTo() 把之后的**每一次**点击都塞进 _pending 且永不执行
       实测复现：挂起该请求后主区空白、点「数据库/账号管理」全无反应、等 12s 不自愈。
       修法：① AbortController + 超时；② _busy 看门狗（超时即 abort + destroy + 补做待办）；
             ③ 加载代号 _gen —— 过期回调一律丢弃，避免旧请求的 .catch 把新导航拆掉。 */
    var ENTER_TIMEOUT = 6000;
    var _gen = 0;
    var _abort = null;
    var _busyT = 0;
    function endBusy() {
      _busy = false;
      if (_busyT) { clearTimeout(_busyT); _busyT = 0; }
      _abort = null;
    }

    var _devItem = null;
    function devItem() {
      if (_devItem && _devItem.isConnected) return _devItem;
      _devItem = null;
      var items = document.querySelectorAll('.sidebar-nav .nav-item');
      for (var i = 0; i < items.length; i++) {
        if (items[i].dataset.rcDev) { _devItem = items[i]; return _devItem; }
      }
      for (var i = 0; i < items.length; i++) {
        if (items[i].classList.contains('mc-sub') || items[i].classList.contains('rc-sub') || items[i].classList.contains('mc-parent')) continue;
        if (/设备管理|监控中心/.test(items[i].textContent || '')) { _devItem = items[i]; return _devItem; }
      }
      return null;
    }
    // 设备管理 → 监控中心（SPA 原生菜单，运行时改文本）
    function renameDevItem() {
      var items = document.querySelectorAll('.sidebar-nav .nav-item');
      for (var i = 0; i < items.length; i++) {
        var it = items[i];
        if (it.classList.contains('mc-sub') || it.classList.contains('rc-sub') || it.classList.contains('mc-parent')) continue; // 注入项跳过
        if (/设备管理/.test(it.textContent || '')) {
          var lbl = it.querySelector('.nav-label');
          if (lbl) lbl.textContent = '监控中心';
          it.dataset.rcDev = '1';
          return;
        }
      }
    }
    function makeSub() {
      var a = document.createElement('a');
      a.className = 'nav-item res-sub';
      a.id = 'res-cmdb-sub';
      a.href = 'javascript:void(0)';
      a.innerHTML = '<span class="nav-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.66 3.58 3 8 3s8-1.34 8-3V5"/><path d="M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3"/></svg></span><span class="nav-label">资源管理</span><span class="rc-arrow"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg></span><span class="nav-active-bar"></span>';
      a.onclick = function(e) {
        e.preventDefault(); e.stopPropagation();
        toggleRc();
      };
      return a;
    }
    function ensureSub() {
      var di = devItem();
      if (!di || document.getElementById('res-cmdb-sub')) return;
      di.insertAdjacentElement('afterend', makeSub());
      ensureRcSubs();
    }
    // 清除 SPA 其它菜单项的 active 态（进入资源中心时设备管理不该亮着）
    function clearNavActive() {
      document.querySelectorAll('.sidebar-nav .nav-item').forEach(function(it) {
        if (it.id !== 'res-cmdb-sub') {
          it.classList.remove('active', 'router-link-active', 'router-link-exact-active');
        }
      });
    }
    /* 注入页 → 是否属于「资源管理」族：只有这些页才点亮资源管理父级。
       账号管理(admin-users) 等独立注入页不再误点亮资源管理（2026-09-08 修复） */
    var RC_PAGES = { 'admin-options': 1, 'resources': 1, 'cabinets': 1, 'physical': 1, 'filelib': 1 };
    /* 手工高亮：账号管理点开的是注入页，原生路由并未跳转，Vue 不会给它 active，
       需要我们自己加/摘（_hlEl 保存元素引用，Vue 重渲染后可重新定位） */
    var _hlEl = null;
    function setHl(el) {
      if (_hlEl && _hlEl !== el) mcActiveClasses(_hlEl, false);
      _hlEl = el || null;
      applyHl();
    }
    function applyHl() {
      if (!_hlEl) return;
      if (!document.contains(_hlEl)) _hlEl = userItem(); // Vue 重渲染替换节点后重新定位
      if (!_hlEl) return;
      /* 账号管理是注入页：原生路由并未跳转，Vue 不会摘掉上一个原生目录项的高亮
         （AI 分析/数据报表/通知设置/部署指南的 router-link-active 会滞留）→ 这里统一清场。
         注入项不动：rc-sub 归 __RC_SET_ACTIVE__，mc-sub/mc-parent 归 syncMcActive，res-sub(父级) 归 markSub。 */
      document.querySelectorAll('.sidebar-nav .nav-item').forEach(function (it) {
        if (it === _hlEl) return;
        if (it.classList.contains('rc-sub') || it.classList.contains('mc-sub') ||
            it.classList.contains('mc-parent') || it.classList.contains('res-sub')) return;
        mcActiveClasses(it, false);
      });
      mcActiveClasses(_hlEl, true);
    }
    function markSub() {
      var s = document.getElementById('res-cmdb-sub');
      var rcOn = !!RC_PAGES[_active];
      if (s) {
        s.classList.toggle('active', rcOn);
        if (rcOn) { setHl(null); clearNavActive(); } // 仅资源管理族：清掉其它项（含账号管理）
      }
      syncMcActive(); // 注入页开合/路由切换时同步监控中心子项的强调态
      syncUrl(); // URL 与注入页状态同步（/u/<page> 直达、可刷新可分享）
    }

    /* ---- URL 深链（2026-09-09）：注入页 = /u/<page>，刷新不丢、URL 可分享 ----
       用 replaceState 而非 pushState：不惊动 Vue router（它不认识这些路径）。
       刷新时 Vue router 遇未知路径只影响 router-view，外壳/侧栏照常渲染，
       我们再由 maybeDeepLink 恢复注入页。 */
    var DEEP_PAGES = ['admin-users', 'admin-options', 'resources', 'cabinets', 'physical', 'db-monitor',
                      'sla-monitor', 'topology', 'capacity', 'maintenance', 'filelib'];
    function syncUrl() {
      try {
        var want = _active ? '/u/' + _active : '/';
        if (location.pathname !== want) history.replaceState(null, '', want);
      } catch (e) {}
    }
    var _deepLinked = false;
    function maybeDeepLink() {
      if (_deepLinked) return;
      var m = /^\/u\/([a-z][a-z0-9-]*)$/.exec(location.pathname);
      if (!m) { _deepLinked = true; return; }
      var page = m[1];
      if (DEEP_PAGES.indexOf(page) < 0) { _deepLinked = true; return; }
      if (!_acctState) return; // /me 未回：由 watchSide 观察器/回调再试
      _deepLinked = true;
      if (page === 'admin-users') {
        if (_acctState.role !== 'admin') return; // 非 admin 不自动开
        if (!_active) openUserMgmt(); // 走 openUserMgmt：带上账号管理的手工高亮
        return;
      }
      if (_active) return;
      window.__RC_TAB__ = 'overview'; // 资源族页恢复时明确落在资源总览（与手动点入一致）
      switchTo(page);
    }

    /* ---- 资源管理二级子项（主站左侧栏） ---- */
    var _rcOpen = false;
    var _mcOpen = false;
    var RC_SUBS = [
      {k:'overview', label:'资源总览', perm:'overview'},
      {k:'resources', label:'资源台账', perm:'resources'},
      {k:'physical', label:'硬件台账', perm:'physical'},
      {k:'proj', label:'项目管理', perm:'proj'},
      {k:'owner', label:'负责人', perm:'owner'},
      {k:'cabinets', label:'机房机柜', perm:'cabinets'},
      /* 2026-09-18：运维文件库（镜像/服务包上传下载审计）。
         后端 /api/files/* 独立路由；页面 filelib.html 独立静态页。 */
      {k:'filelib', label:'运维文件库', perm:'filelib', page:'filelib'}
    ];
    /* 监控中心二级子项（需求①：监控详情/告警日志挂监控中心，不挂资源管理）
       2026-09-16：删除「监控详情」子项 —— 设备管理页新增的「列表/卡片」视图已完全覆盖该列表页功能；
       设备详情 /monitor/<id> 仍从设备管理的「详情」按钮进入（见 MC-MACH-VIEW 的代理跳转）。 */
    var MC_SUBS = [
      {k:'home', label:'监控总览', route:'/', perm:'dashboard'},
      {k:'dev', label:'设备管理', route:'/machines', perm:'machines'},
      {k:'db', label:'数据库', page:'db-monitor', perm:'dbs'},
      {k:'alerts', label:'告警日志', route:'/alerts', perm:'alerts'},
      /* 2026-09-17 新增分析层页面（后端 /api/advanced/* 早已就绪、此前无入口） */
      {k:'sla', label:'SLA 看板', page:'sla-monitor', perm:'dashboard'},
      {k:'topo', label:'服务拓扑', page:'topology', perm:'machines'},
      {k:'cap', label:'容量预测', page:'capacity', perm:'machines'},
      {k:'maint', label:'维护窗口', page:'maintenance', perm:'machines'}
    ];
    function applyRcOpen() {
      document.querySelectorAll('.sidebar-nav .rc-sub').forEach(function(a) {
        a.style.display = _rcOpen ? 'flex' : 'none';
      });
      var p = document.getElementById('res-cmdb-sub');
      if (p) p.classList.toggle('open', _rcOpen);
    }
    /* 2026-09-17 修复「资源管理点不动 / 点了没反应」：
       旧实现是对称开关 `_rcOpen = !_rcOpen`。而 `_rcOpen` 在切到原生页时**不会被复位**
       （goRoute() 只清 _active，不动 _rcOpen）→ 于是「先点过资源管理（子菜单已展开）→ 切到
       告警日志 → 再点资源管理」时变成 true→false，只折叠子菜单而**完全不导航**，
       用户看到的就是"点了没反应/菜单卡死"。
       改为可预测语义：① 不在资源族页面时，点父级一律「展开子菜单 + 打开资源总览」；
       ② 已在资源族页面时，才只做子菜单开合（方便收起），不做页面切换。 */
    function toggleRc() {
      if (RC_PAGES[_active]) {          // 已在资源族注入页：仅开合子菜单
        _rcOpen = !_rcOpen;
        applyRcOpen();
        return;
      }
      if (!_rcOpen) { _rcOpen = true; applyRcOpen(); }  // 保证子项可见后再导航
      window.__RC_TAB__ = 'overview';
      switchTo('admin-options');
    }
    function ensureRcSubs() {
      var ref = document.getElementById('res-cmdb-sub');
      if (!ref) return;
      RC_SUBS.forEach(function(s) {
        var id = 'rc-sub-' + s.k;
        var ex = document.getElementById(id);
        if (ex) { ref = ex; return; }
        var a = document.createElement('a');
        a.className = 'nav-item rc-sub';
        a.id = id;
        a.href = 'javascript:void(0)';
        a.innerHTML = '<span class="nav-label">' + s.label + '</span>'; /* 数字角标已删（2026-09-04 需求） */
        if (s.perm) a.dataset.perm = s.perm;
        a.onclick = function(e) {
          e.preventDefault(); e.stopPropagation();
          if (s.route) { goRoute(s.route); return; }
          if (s.k === 'cabinets') { switchTo('cabinets'); return; }
          if (s.k === 'physical') { switchTo('physical'); return; }
          /* 2026-09-18：带 page 字段的子项 = 独立注入页（运维文件库）。
             必须显式 switchTo(s.page)，否则会落进下面 admin-options 的资源内 tab 分支，
             开出的会是资源总览页而非文件库。 */
          if (s.page) { switchTo(s.page); return; }
          if (_active === 'admin-options' && window.__RC_GOTO__) {
            window.__RC_GOTO__(s.k);
          } else {
            window.__RC_TAB__ = s.k;
            switchTo('admin-options');
          }
        };
        a.style.display = _rcOpen ? 'flex' : 'none';
        ref.insertAdjacentElement('afterend', a);
        ref = a;
      });
      applyRcOpen();
    }
    /* ══ 监控中心父级 + 设备管理子级（需求②） ══ */
    function applyMcOpen() {
      MC_SUBS.forEach(function (s) {
        var c = document.getElementById('mc-sub-' + s.k);
        if (c) c.style.display = _mcOpen ? 'flex' : 'none';
      });
      var p = document.getElementById('mc-parent');
      if (p) p.classList.toggle('open', _mcOpen);
    }
    function findRouteItem(route) {
      var nav = document.querySelector('.sidebar-nav');
      if (!nav) return null;
      var items = nav.querySelectorAll('.nav-item');
      for (var i = 0; i < items.length; i++) {
        var it = items[i];
        if (it.classList.contains('rc-sub') || it.classList.contains('mc-sub') || it.classList.contains('mc-parent') || it.classList.contains('res-sub')) continue;
        var r = it.getAttribute('data-route') || it.getAttribute('to') || it.getAttribute('href');
        if (r === route) return it;
      }
      return null;
    }
    // 监控中心子项点击后要有与其它菜单一致的蓝色强调：SPA 只把 active 加在它自己那个被隐藏的原始项上，
    // 我们可见的注入子项必须自己按当前路由补 active / router-link-exact-active。
    function mcActiveClasses(el, on) {
      if (!el) return;
      ['active', 'router-link-active', 'router-link-exact-active'].forEach(function (c) {
        if (el.classList.contains(c) !== on) el.classList.toggle(c, on); // 状态相同则不写，避免自触发 MutationObserver 死循环
      });
    }
    function syncMcActive() {
      var path = location.pathname || '/';
      /* 注入页打开时（_active 非空），路由型子项一律不点亮；
         但注入页本身若挂在「监控中心」下（目前只有「数据库」= db-monitor：
         MC_SUBS 里唯一带 page 字段的项），必须点亮它自己，
         否则点进数据库页后侧栏整列无高亮（2026-09-16 修复）。 */
      var pageKey = _active || '';
      var any = false;
      MC_SUBS.forEach(function (s) {
        // 「监控详情」子项已删（2026-09-16）：/monitor 列表页与 /monitor/<id> 设备详情页统一归属「设备管理」，
        // 详情是设备管理的下钻，这样从设备管理点「详情」进去后侧栏不会整列熄灭。
        var own = (s.k === 'dev') && /^\/monitor(\/|$)/.test(path);
        var on = pageKey ? (s.page === pageKey) : (s.route === path || own);
        if (on) any = true;
        mcActiveClasses(document.getElementById('mc-sub-' + s.k), on);
      });
      mcActiveClasses(document.getElementById('mc-parent'), any); // 父级跟随（与资源管理父级同样处理）
      if (any && !_mcOpen) { _mcOpen = true; applyMcOpen(); } // 直达/后退进来时自动展开，避免选中项不可见
      applyHl(); // 保留账号管理的手工高亮（Vue 重渲染/路由切换后重新贴回）
    }
    var _mcSyncT = 0;
    function scheduleMcActive() {
      if (_mcSyncT) return;
      _mcSyncT = setTimeout(function () { _mcSyncT = 0; syncMcActive(); }, 300);
    }
    window.addEventListener('popstate', function () { syncMcActive(); setTimeout(syncMcActive, 300); });
    document.addEventListener('click', scheduleMcActive, true);
    // 退出注入页模式并经隐藏的 SPA 原生 router-link 导航（无整页刷新）
    function goRoute(route) {
      /* 2026-09-17：作废任何「在途/待办」的注入页加载。
         否则「点注入页 → 紧接着点原生目录」时，那个注入页的 fetch 会在原生路由之后才落地，
         把页面内容/URL 覆盖回注入页（表现为 URL 与内容不一致、看着像卡住）。
         ⚠ 必须同时 endBusy()：过期回调走 `if (my !== _gen) return` 不会释放 _busy，
           而看门狗也因 `my !== _gen` 跳过 → 不同步释放就会把后续注入页点击**永久吞掉**。 */
      _gen++;
      _pending = null;
      endBusy();
      destroy(); _active = ''; markSub();
      var it = findRouteItem(route);
      if (it) {
        var prev = _routing; _routing = true;
        try { it.click(); } finally { _routing = prev; }
      }
      syncMcActive(); setTimeout(syncMcActive, 120); setTimeout(syncMcActive, 450);
    }
    function ensureMcSub() {
      var ref = document.getElementById('res-cmdb-sub') || devItem();
      if (!ref || document.getElementById('mc-parent')) return;
      var p = document.createElement('a');
      p.className = 'nav-item mc-parent'; p.id = 'mc-parent'; p.href = 'javascript:void(0)';
      p.innerHTML = '<span class="nav-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg></span><span class="nav-label">监控中心</span><span class="rc-arrow"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg></span><span class="nav-active-bar"></span>';
      p.dataset.perm = 'machines';
      p.onclick = function (e) { e.preventDefault(); e.stopPropagation(); _mcOpen = !_mcOpen; applyMcOpen(); };
      ref.insertAdjacentElement('beforebegin', p);
      var prev = p;
      MC_SUBS.forEach(function (s) {
        var c = document.createElement('a');
        c.className = 'nav-item mc-sub'; c.id = 'mc-sub-' + s.k; c.href = 'javascript:void(0)';
        c.innerHTML = '<span class="nav-label">' + s.label + '</span>';
        if (s.perm) c.dataset.perm = s.perm;
        c.onclick = function (e) { e.preventDefault(); e.stopPropagation(); if (s.page) { switchTo(s.page); } else { goRoute(s.route); } };
        prev.insertAdjacentElement('afterend', c);
        prev = c;
      });
      applyMcOpen();
    }
    // （原 hideMovedTopItems 已删除：顶级隐藏改由 CSS 属性选择器完成，Vue 重渲染也不会失效）

    // admin 页调用：高亮当前子项 / 更新数量角标
    /* ⚠ 防「过期注入页脚本覆盖高亮」（2026-09-17 修 _stress_pending T2/T3）：
       场景「账号管理(加载中) → 资源管理父级 → 硬件台账/账号管理」连点：
         · toggleRc() 把 admin-options 排入 _pending（其 HTML 被注入、内联脚本立即执行）；
         · 随后被 physical / admin-users 取代，最终页正确渲染；
         · 但 admin-options 的**异步 load()** 稍后才完成，其 go() 末尾调用 __RC_SET_ACTIVE__(k)
           → 用陈旧的 __RC_TAB__ 覆盖当前页的子项高亮（URL/内容都对，只有侧栏错）。
       这里以 **当前真正打开的注入页 `_active`** 做真源校验（不再用独立变量，避免 destroy 时序坑）：
         · `_active === 'admin-options'` 时，overview/resources/proj/owner 都是它的 tab → 受理；
         · 否则只受理与 `_active` 同名的键（physical/cabinets/resources…）；
         · `''` / '__none__'（明确熄灭）与 `_active` 为空时永远受理（不挡正常初始化）。 */
    window.__RC_SET_ACTIVE__ = function(k) {
      try {
        var isNone = (k === '' || k === '__none__');
        if (!isNone && _active) {
          var tabHost = (_active === 'admin-options' && ['overview', 'resources', 'proj', 'owner', 'pve'].indexOf(k) >= 0);
          if (!tabHost && _active !== k) return; // 过期页脚本：丢弃
        }
      } catch (e) {}
      document.querySelectorAll('.sidebar-nav .rc-sub').forEach(function(a) {
        a.classList.toggle('active', a.id === 'rc-sub-' + k);
      });
    };
    window.__RC_SET_COUNTS__ = function(counts) {
      counts = counts || {};
      RC_SUBS.forEach(function(s) {
        var a = document.getElementById('rc-sub-' + s.k);
        var c = a && a.querySelector('.rc-sub-cnt');
        if (!c) return;
        var n = counts[s.k];
        if (s.k === 'overview' || n === undefined || n === null) { c.textContent = ''; c.style.display = 'none'; return; }
        c.textContent = n;
        c.style.display = '';
      });
    };
    // 启动时拉 options/resources 填角标
    function refreshRcCounts() {
      var TOKEN = localStorage.getItem('token') || '';
      var hd = {'Content-Type':'application/json'};
      if (TOKEN) hd.Authorization = 'Bearer ' + TOKEN;
      Promise.all([
        fetch('/api/resource-cmdb/options', {headers:hd}).then(function(x){return x.json()}).catch(function(){return null}),
        fetch('/api/resource-cmdb/resources', {headers:hd}).then(function(x){return x.json()}).catch(function(){return []}),
        fetch('/api/machines/', {headers:hd}).then(function(x){return x.json()}).catch(function(){return []}),
        fetch('/api/resource-cmdb/cabinets', {headers:hd}).then(function(x){return x.json()}).catch(function(){return []})
      ]).then(function(rs) {
        var o = (rs[0] && rs[0].data) ? rs[0].data : {};
        var res = Array.isArray(rs[1]) ? rs[1] : ((rs[1] && rs[1].data) ? rs[1].data : []);
        var mach = Array.isArray(rs[2]) ? rs[2] : [];
        var cab = Array.isArray(rs[3]) ? rs[3] : ((rs[3] && rs[3].data) ? rs[3].data : []);
        var p = (o.bindings || {}).pve_location || [];
        var excl = o.machine_exclude || [];
        var imported = {};
        res.forEach(function(r){ if(r.machine_id!=null) imported[r.machine_id]=true; });
        var machN = 0;
        mach.forEach(function(mc){ if(excl.indexOf(mc.id)<0 && !imported[mc.id]) machN++; });
        var vmN = res.filter(function(r){ return r.category !== 'physical'; }).length;
        var phyN = res.filter(function(r){ return r.category === 'physical'; }).length;
        var rooms = {};
        cab.forEach(function(c){ if(c && c.room_name) rooms[c.room_name]=1; });
        var roomN = Object.keys(rooms).length;
        window.__RC_SET_COUNTS__({
          resources: vmN, /* 角标 = 资源台账真实行数（2026-09-04 口径核准；旧值 vmN+machN 混入未录入机器导致与页面 28 行对不上） */
          physical: phyN,
          cabinets: roomN,
          proj: (o.projects || []).length,
          owner: (o.owners || []).length,
          type: (o.machine_types || []).length
        });
      }).catch(function() {});
    }

    /* ═══════════════════════════════════════════════
       账号组件 / 权限门控 / 退出 / 修改密码
       ═════════════════════════════════════════════ */
    var _acctState = null;
    // 侧栏路由 → 权限 key 映射（管理员恒为全部）
    var ROUTE_PERM = {
      '/': 'dashboard', '/machines': 'machines', '/monitor': 'monitor',
      '/alerts': 'alerts', '/ai-analysis': 'ai', '/reports': 'reports',
      '/settings': 'settings', '/users': 'users', '/deploy-guide': 'deploy'
    };

    function esc(s) {
      return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
      });
    }
    function authHeaders() {
      var t = localStorage.getItem('token') || '';
      var h = { 'Content-Type': 'application/json' };
      if (t) h.Authorization = 'Bearer ' + t;
      return h;
    }
    function fetchMe(cb) {
      fetch('/api/auth/me', { headers: authHeaders() })
        .then(function (x) { return x.ok ? x.json() : null; })
        .then(function (me) {
          if (me && me.username) {
            _acctState = { username: me.username, role: me.role, permissions: me.permissions || [] };
          }
          if (cb) cb(_acctState);
        })
        .catch(function () { if (cb) cb(_acctState); });
    }
    function applyPermGating() {
      if (!_acctState) return;
      var perms = _acctState.permissions || [];
      var isAdmin = _acctState.role === 'admin';
      document.querySelectorAll('.sidebar-nav .nav-item').forEach(function (it) {
        if (it.classList.contains('res-sub')) return; // 资源管理父级始终可见
        // 注入子项/父级（rc-sub / mc-parent / mc-sub）：按 data-perm 门控
        if (it.classList.contains('rc-sub') || it.classList.contains('mc-sub') || it.classList.contains('mc-parent')) {
          var pk = it.getAttribute('data-perm');
          if (!pk) { it.classList.remove('rc-no-perm'); return; }
          if (isAdmin || perms.indexOf(pk) >= 0) it.classList.remove('rc-no-perm');
          else it.classList.add('rc-no-perm');
          return;
        }
        var route = it.getAttribute('data-route') || it.getAttribute('to') || it.getAttribute('href');
        if (!route && it.querySelector('a')) route = it.querySelector('a').getAttribute('href') || it.querySelector('a').getAttribute('to');
        if (!route) return;
        var key = ROUTE_PERM[route];
        if (!key) return;
        if (isAdmin) { it.classList.remove('rc-no-perm'); return; }
        if (perms.indexOf(key) < 0) it.classList.add('rc-no-perm');
        else it.classList.remove('rc-no-perm');
      });
      // 资源管理父级：普通用户对 6 个子模块均无权限时，父级一并隐藏（2026-09-07 资源管理权限拆分）
      var anyRc = false;
      document.querySelectorAll('.sidebar-nav .rc-sub').forEach(function (a) {
        if (!a.classList.contains('rc-no-perm')) anyRc = true;
      });
      var rcParent = document.getElementById('res-cmdb-sub');
      if (rcParent && !isAdmin) rcParent.classList.toggle('rc-no-perm', !anyRc);
    }
    function ensureAcctWidget() {
      if (!_acctState) return;
      // 右上角账号下拉
      var right = document.querySelector('.topbar-right');
      if (right && !document.getElementById('rcAcct')) {
        var wrap = document.createElement('div');
        wrap.className = 'rc-acct';
        wrap.id = 'rcAcct';
        var nm = _acctState.display_name || _acctState.username;
        var initial = (nm || '?').slice(0, 1).toUpperCase();
        wrap.innerHTML =
          '<button class="rc-acct-btn" type="button"><span class="av">' + esc(initial) + '</span><span class="nm">' + esc(nm) + '</span><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg></button>' +
          '<div class="rc-acct-menu" id="rcAcctMenu" style="display:none">' +
          '<button data-act="pw"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="9" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>修改密码</button>' +
          '<div class="sep"></div>' +
          '<button data-act="logout"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>退出登录</button>' +
          '</div>';
        var timeEl = right.querySelector('.topbar-time');
        if (timeEl) right.insertBefore(wrap, timeEl); else right.appendChild(wrap);
        var btn = wrap.querySelector('.rc-acct-btn');
        var menu = wrap.querySelector('.rc-acct-menu');
        btn.addEventListener('click', function (e) { e.stopPropagation(); menu.style.display = menu.style.display === 'none' ? 'block' : 'none'; });
        menu.addEventListener('click', function (e) {
          var b = e.target.closest('button'); if (!b) return;
          var act = b.getAttribute('data-act');
          if (act === 'logout') { menu.style.display = 'none'; doLogout(); }
          else if (act === 'pw') { menu.style.display = 'none'; openChangePw(); }
        });
      }
      applyPermGating();
      ensureUserNav();
    }
    function userItem() {
      var items = document.querySelectorAll('.sidebar-nav .nav-item');
      for (var i = 0; i < items.length; i++) {
        if (/账号管理/.test(items[i].textContent || '') && getComputedStyle(items[i]).display !== 'none') return items[i];
      }
      return null;
    }
    function ensureUserNav() {
      var items = document.querySelectorAll('.sidebar-nav .nav-item');
      for (var i = 0; i < items.length; i++) {
        var it = items[i];
        if (/账号管理/.test(it.textContent || '')) {
          if (it.dataset.rcUserBound) return;
          it.dataset.rcUserBound = '1';
          it.addEventListener('click', function (e) {
            if (!_acctState || _acctState.role !== 'admin') return; // 非 admin：放行给原生路由
            e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
            openUserMgmt();
          }, true);
          return;
        }
      }
    }
    /* 目录点击统一入口（capture，早于元素上的监听）：
       - 点「账号管理」：admin 走注入页（阻止原生跳转）并手工高亮；非 admin **放行**给 SPA 原生 /users
       - 点其它目录项：摘掉账号管理的手工高亮，避免双高亮
       ⚠ 时序坑（2026-09-09）：/api/auth/me 可能还没回来（_acctState 为 null），
         早期写法此时直接放行 → 被 Vue router-link 抢走而跳到原生 /users。
         改为**无论如何先拦下**，未就绪就补拉一次 /me 再决定，行为完全确定。
       ⚠ 自触发坑（2026-09-09）：goRoute() 内部用 it.click() 驱动原生路由，
         那个 click 会被本 handler 再次拦下形成自调用 → 用 _routing 放行。 */
    document.addEventListener('click', function (e) {
      var a = (e.target && e.target.closest) ? e.target.closest('.nav-item') : null;
      if (!a) return;
      if (_routing) return; // 我们自己发出的路由点击：原样放行给 Vue router-link
      if (/账号管理/.test(a.textContent || '')) {
        if (_acctState && _acctState.role !== 'admin') { setHl(null); return; } // 非 admin：交给原生路由
        e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
        if (_acctState) {
          openUserMgmt(); // 角色已确认为 admin
        } else {
          fetchMe(function (st) {
            if (st && st.role === 'admin') openUserMgmt(); else goRoute('/users');
          });
        }
        return;
      }
      if (a !== _hlEl) setHl(null);
    }, true);
    function doLogout() {
      localStorage.removeItem('token');
      localStorage.removeItem('userInfo');
      try { localStorage.removeItem('user'); } catch (e) {}
      location.reload();
    }
    function openUserMgmt() {
      /* ⚠ 已在账号管理页时**不能直接 return**（2026-09-17 修 _stress_pending T3）：
         场景「账号管理 → 资源管理父级 → 账号管理」三连点：
           · 第 2 次点击(toggleRc/markSub)会 clearNavActive() + setHl(null)，把账号管理的手工高亮摘掉；
           · 第 3 次点击若因 _active 仍为 'admin-users' 而提前 return，高亮就**永久丢失**（侧栏整列无高亮）。
         这里改为：重复进入同一页时至少把手工高亮重新贴回（幂等，无副作用）。 */
      if (_active === 'admin-users') { setHl(userItem()); return; }
      window.__RC_TAB__ = 'users';
      switchTo('admin-users');
      setHl(userItem()); // 注入页不带原生路由，手工给「账号管理」加高亮
    }
    function openChangePw() {
      var st = document.getElementById('rcpw-style');
      if (!st) {
        st = document.createElement('style'); st.id = 'rcpw-style';
        st.textContent = '.rcpw-layer{position:fixed;inset:0;z-index:7000;display:flex;align-items:center;justify-content:center;background:rgba(15,23,42,.45)}' +
          '.rcpw-box{width:340px;background:#fff;border-radius:14px;padding:20px;box-shadow:0 20px 60px rgba(0,0,0,.3)}' +
          '.rcpw-title{font-size:16px;font-weight:600;margin-bottom:14px;color:#1f2937}' +
          '.rcpw-box label{display:block;font-size:12px;color:#6b7280;margin:10px 0 4px}' +
          '.rcpw-box input{width:100%;height:36px;border:1px solid #d1d5db;border-radius:8px;padding:0 10px;font-size:13px;box-sizing:border-box}' +
          '.rcpw-box input:focus{outline:none;border-color:#1B3F7A}' +
          '.rcpw-err{color:#dc2626;font-size:12px;min-height:16px;margin-top:8px}' +
          '.rcpw-actions{display:flex;justify-content:flex-end;gap:10px;margin-top:16px}' +
          '.rcpw-cancel,.rcpw-ok{height:34px;padding:0 16px;border-radius:8px;font-size:13px;cursor:pointer;border:1px solid #d1d5db;background:#fff;color:#374151}' +
          '.rcpw-ok{background:#1B3F7A;border-color:#1B3F7A;color:#fff}.rcpw-ok:hover{background:#16335f}';
        document.head.appendChild(st);
      }
      var layer = document.getElementById('rcPwLayer');
      if (!layer) {
        layer = document.createElement('div'); layer.id = 'rcPwLayer'; layer.className = 'rcpw-layer';
        layer.innerHTML = '<div class="rcpw-box">' +
          '<div class="rcpw-title">修改密码</div>' +
          '<label>原密码</label><input id="rcpwOld" type="password" autocomplete="off"/>' +
          '<label>新密码</label><input id="rcpwNew" type="password" autocomplete="off"/>' +
          '<label>确认新密码</label><input id="rcpwNew2" type="password" autocomplete="off"/>' +
          '<div class="rcpw-err" id="rcpwErr"></div>' +
          '<div class="rcpw-actions"><button class="rcpw-cancel" id="rcpwCancel">取消</button><button class="rcpw-ok" id="rcpwOk">确定</button></div>' +
          '</div>';
        document.body.appendChild(layer);
        layer.addEventListener('click', function (e) { if (e.target === layer) closeChangePw(); });
        layer.querySelector('#rcpwCancel').addEventListener('click', closeChangePw);
        layer.querySelector('#rcpwOk').addEventListener('click', function () {
          var old = layer.querySelector('#rcpwOld').value;
          var nv = layer.querySelector('#rcpwNew').value;
          var nv2 = layer.querySelector('#rcpwNew2').value;
          var err = layer.querySelector('#rcpwErr');
          err.textContent = '';
          if (nv.length < 6) { err.textContent = '新密码至少 6 位'; return; }
          if (nv !== nv2) { err.textContent = '两次输入的新密码不一致'; return; }
          fetch('/api/auth/change-password', {
            method: 'PUT', headers: authHeaders(),
            body: JSON.stringify({ old_password: old, new_password: nv })
          }).then(function (x) {
            return x.json().then(function (d) { return { ok: x.ok, d: d }; });
          }).then(function (r) {
            if (r.ok) { closeChangePw(); alert('密码修改成功，请重新登录'); doLogout(); }
            else { err.textContent = (r.d && r.d.detail) || '修改失败'; }
          }).catch(function () { err.textContent = '网络错误'; });
        });
      }
      layer.style.display = 'flex';
    }
    function closeChangePw() { var l = document.getElementById('rcPwLayer'); if (l) l.style.display = 'none'; }
    document.addEventListener('click', function (e) {
      var m = document.getElementById('rcAcctMenu');
      if (m && m.style.display !== 'none' && !(e.target.closest && e.target.closest('#rcAcct'))) m.style.display = 'none';
    });

    function getMain() { return document.querySelector('.main-area'); }

    function switchTo(page) {
      if (_busy) { _pending = page; return; }
      if (_active === page) { destroy(); _active = ''; markSub(); return; }
      destroy();
      _active = page; markSub();
      document.body.classList.toggle('has-res-root', _active !== '');
      enter(page);
    }

    function enter(page) {
      var url = '/' + page + '.html';
      var m = getMain();
      if (!m) return;
      var my = ++_gen; // 本次加载代号（过期回调凭它丢弃）
      _busy = true;
      if (_busyT) { clearTimeout(_busyT); _busyT = 0; }
      /* 看门狗：超时未完成就 abort + 复原主区 + 补做最后一次点击意图，
         绝不让 _busy 永远挂住（否则整页菜单都点不动）。 */
      _busyT = setTimeout(function () {
        _busyT = 0;
        if (!_busy || my !== _gen) return;
        try { if (_abort) _abort.abort(); } catch (e) {}
        _busy = false; _abort = null;
        destroy();
        flushPending();
      }, ENTER_TIMEOUT);
      _saved = Array.from(m.children).filter(function(c) {
        return c.id !== ROOT_ID && !c.classList.contains('topbar');
      });
      _saved.forEach(function(c) { c.style.display = 'none'; });
      var root = document.getElementById(ROOT_ID);
      if (!root) { root = document.createElement('div'); root.id = ROOT_ID; m.appendChild(root); }
      root.style.display = '';
      try { if (_abort) _abort.abort(); } catch (e) {} // 取消上一次仍挂着的注入页拉取
      var _ac = null;
      try { _ac = (typeof AbortController !== 'undefined') ? new AbortController() : null; } catch (e) {}
      _abort = _ac;
      fetch(url, { cache: 'no-store', signal: _ac ? _ac.signal : undefined })
        .then(function(r) { return r.text(); })
        .then(function(text) {
          if (my !== _gen) return; // 过期回调：期间已有更新的导航，丢弃，别覆盖当前页
          /* 防竞态：本页仍是当前注入页时，把主区状态重新断言回来。
             （否则若 80ms 延迟 destroy() 或原生路由回调抢先执行，会把 root 隐藏、
               _saved 恢复显示，导致内容渲染进"看不见的 root" → 页面空白/点了没反应） */
          if (_active === page) {
            try {
              var m2 = getMain();
              if (m2) Array.from(m2.children).forEach(function (c) {
                if (c.id !== ROOT_ID && !c.classList.contains('topbar')) c.style.display = 'none';
              });
              root.style.display = '';
              document.body.classList.add('has-res-root');
            } catch (e) {}
            syncUrl(); // 再断言一次 /u/<page>，避免被原生路由的 pushState 覆盖
          }
          window.__RES_INLINE__ = true;
          var doc = new DOMParser().parseFromString(text, 'text/html');
          var css = '';
          doc.querySelectorAll('style').forEach(function(s) {
            var t = s.textContent; t = t.replace(/:root(\.[\w-]+)?\s*\{[^}]*\}/g, ''); css += t + '\n';
          });
          root.innerHTML =
            '<style id="res-inline-style">@scope(#res-main-root){' + css + '}</style>' + doc.body.innerHTML;
          /* 注入页的内联脚本会在 document 上挂 click/keydown/drag 等监听器。
             ⚠ 2026-09-09：这些监听器在切页时从未被摘除 —— 既会泄漏累积（重复触发），
             也会因为引用了已被清空的节点而在后续任何点击时抛 TypeError
             （观测到 "Cannot read properties of null (reading 'style')"）。
             做法：执行注入脚本期间代理 addEventListener 记账，destroy 时统一摘除。 */
          releaseInjectedListeners(); // 兜底：直接进入 enter() 而未走 destroy() 的情况
          var _origAdd = document.addEventListener;
          document.addEventListener = function (t, h, o) {
            _injLs.push([t, h, o]);
            return _origAdd.call(document, t, h, o);
          };
          try {
            doc.querySelectorAll('script').forEach(function(sc) {
              var ns = document.createElement('script'); ns.textContent = sc.textContent; root.appendChild(ns);
            });
          } finally {
            document.addEventListener = _origAdd;
          }
          var fix = document.createElement('style'); fix.id = 'res-fix-style';
          fix.textContent = '@scope(#res-main-root){.sidebar,.toasts,#toasts,[class*="fixed"]{display:none!important}}';
          root.appendChild(fix);
          try { if (page === 'cabinets' && window.__RC_SET_ACTIVE__) window.__RC_SET_ACTIVE__('cabinets'); } catch(e) {}
          try { if (page === 'physical' && window.__RC_SET_ACTIVE__) window.__RC_SET_ACTIVE__('physical'); } catch(e) {}
          /* 通用兜底：任何直接对应 rc-sub 的注入页，进入后都以其自身 k 点亮子项，
             不依赖各页内联脚本是否调用了 __RC_SET_ACTIVE__（也修正 __RC_TAB__ 陈旧的情况）。 */
          try {
            if (window.__RC_SET_ACTIVE__ && RC_SUBS.some(function (x) { return x.k === page; })) {
              window.__RC_SET_ACTIVE__(page);
            }
          } catch(e) {}
          try { if (page === 'db-monitor') { var dbs = document.getElementById('mc-sub-db'); if (dbs) dbs.classList.add('active'); } } catch(e) {}
          if (page === 'admin-users') afterBusySyncUser();
          endBusy();
          flushPending();
        }).catch(function() {
          if (my !== _gen) return; // 旧请求被 abort / 已被新导航取代：不要拆掉当前页
          destroy(); endBusy(); flushPending();
        });
    }

    function destroy() {
      releaseInjectedListeners();
      var root = document.getElementById(ROOT_ID);
      if (root) { root.style.display = 'none'; root.innerHTML = ''; }
      if (_saved) { _saved.forEach(function(c) { c.style.display = ''; }); _saved = null; }
      try { if (window.__resDestroy) window.__resDestroy(); } catch(e) {}
      try { if (window.__cabDestroy) window.__cabDestroy(); } catch(e) {}
      try { if (window.__phyDestroy) window.__phyDestroy(); } catch(e) {}
      /* 2026-09-17 新增注入页的清理钩子：各自持有 setInterval / 全局事件监听，
         不调用就会在切页后继续轮询一个已消失的 DOM（泄漏 + 无谓请求）。 */
      try { if (window.__SLADestroy) window.__SLADestroy(); } catch(e) {}
      try { if (window.__TPDestroy) window.__TPDestroy(); } catch(e) {}
      try { if (window.__CAPDestroy) window.__CAPDestroy(); } catch(e) {}
      try { if (window.__MAINTDestroy) window.__MAINTDestroy(); } catch(e) {}
      try { delete window.__RES_INLINE__; } catch(e) {}
      try { delete window.__CAB_INLINE__; } catch(e) {}
      var st = document.getElementById('res-inline-style'); if (st) st.remove();
      var fx = document.getElementById('res-fix-style'); if (fx) fx.remove();
      try { if (window.__RC_SET_ACTIVE__) window.__RC_SET_ACTIVE__(''); } catch(e) {}
      try { var dbs = document.getElementById('mc-sub-db'); if (dbs) dbs.classList.remove('active'); } catch(e) {}
      markSub();
      document.body.classList.remove('has-res-root');
    }

    // 全局 API：让注入页面（如 admin-options.html）切到 SPA 路由（监控中心设备页等，无整页刷新）
    window.__GO_ROUTE__ = function(route) { goRoute(route); };
    // 全局 API：让注入页面（如 admin-options.html）切换到其它页
    window.__INJECT_TO__ = function(page) {
      if (page === 'resources') {
        // 切到 resources 页同时保留 admin 页（让"打开资源台账"按钮工作）
        enter('resources');
        _active = 'resources'; markSub();
      } else if (page === 'cabinets' || page === 'physical') {
        // 来自物理机管理"定位到机柜"等联动跳转
        switchTo(page);
      }
    };

    document.addEventListener('click', function(e) {
      if (!_active) return;
      var a = (e.target && e.target.closest) ? e.target.closest('.nav-item') : null;
      if (!a || a.classList.contains('res-sub') || a.classList.contains('rc-sub') || a.classList.contains('mc-parent')) return;
      var g = _gen; // 记下加载代号：80ms 内若有新的注入页接管，就不许再拆掉它
      _active = ''; markSub();
      _rcOpen = false; applyRcOpen();
      setTimeout(function() { if (_gen !== g) return; destroy(); }, 80);
    });

    function watchMain() {
      if (_obs) { try { _obs.disconnect(); } catch(e) {} }
      var m = getMain();
      if (!m) return;
      _obs = new MutationObserver(function() {
        if (_mainT) return;
        _mainT = setTimeout(function() {
          _mainT = 0;
          if (_active && !document.getElementById(ROOT_ID))
            setTimeout(function() { if (_active) enter(_active); }, 200);
        }, 60);
      });
      _obs.observe(m, { childList: true });
    }
    // 性能：60ms 防抖，只监听直接子节点变化（不监听 subtree，避免 hover/动画/状态变化频繁触发）
    var _mainT = 0, _sideT = 0;
    /* 幂等的侧栏注入链（renameDevItem 会 return 短路，但注入节点需各自判存在） */
    function injectSide() {
      renameDevItem(); ensureMcSub(); ensureSub(); ensureRcSubs(); syncMcActive();
      if (_acctState) ensureAcctWidget();
      maybeDeepLink();
    }
    /* ⚠ 自愈（2026-09-17 修「侧栏偶发只剩 9 项、资源管理整族消失」）：
       boot() 早期 document.querySelector('.sidebar-nav') 可能命中一个**随后被 Vue 丢弃的中间态 nav**：
       此时我们把 mc-parent / 所有 mc-sub-* / res-cmdb-sub / rc-sub-* 全插进去了，但 Vue 重渲染把整棵子树换掉
       → 注入节点全灭，而 _obsSide 观察的是**已脱离文档的旧 nav**，childList 再也不触发
       → 侧栏永久停在原生 9 项（资源管理/数据库/SLA 等全无）。
       下面这个 1s 巡检做两件事：① nav 节点身份变了就重挂观察器；② 关键锚点缺失就重跑注入链。
       代价极低（仅 3 次 getElementById），且天然覆盖 Vue 任意次重渲染。 */
    var _sideNavRef = null;
    function maintainSide() {
      var nav = document.querySelector('.sidebar-nav');
      if (!nav) return;
      if (nav !== _sideNavRef) { _sideNavRef = nav; watchSide(); }
      // 关键锚点缺失 ⇒ Vue 抹掉了注入 ⇒ 立刻补回（injectSide 幂等）
      if (!document.getElementById('mc-parent') || !document.getElementById('res-cmdb-sub')) injectSide();
    }
    function watchSide() {
      if (_obsSide) { try { _obsSide.disconnect(); } catch(e) {} }
      var nav = document.querySelector('.sidebar-nav');
      if (!nav) return;
      _obsSide = new MutationObserver(function() {
        if (_sideT) return;
        _sideT = setTimeout(function() {
          _sideT = 0;
          injectSide();
        }, 60);
      });
      _obsSide.observe(nav, { childList: true });
    }

    var _t = 0;
    (function boot() {
      if (document.querySelector('.sidebar-nav')) {
        injectSide(); watchMain(); watchSide(); refreshRcCounts();
        fetchMe(function (st) { ensureAcctWidget(); maybeDeepLink(); });
        syncMcActive(); setTimeout(syncMcActive, 600);
        // 自愈巡检：1s 间隔，仅在锚点缺失时真干活
        setInterval(maintainSide, 1000);
      }
      else if (_t++ < 300) setTimeout(boot, 250);
    })();
  })();
/* ══ MC-MACH-STATS 设备管理三卡 ══ */
/* ===MC-MACH-STATS 2026-09-07 设备管理三卡===
/* 设备管理 /machines 顶部三张小卡片
 * - 复用已注入的 .stat-grid/.stat-card（mc CSS 已统一风格），零新增 CSS
 * - 口径＝当前列表（与页面表格/"N 台设备" 自洽），随展开/筛选/搜索实时更新
 * - 数字 count-up 650ms；prefers-reduced-motion 下直接赋值
 */
(function () {
  'use strict';
  var ID = 'mc-mach-stats';
  var ICONS = {
    total: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="7" rx="2"></rect><rect x="2" y="13" width="20" height="7" rx="2"></rect><line x1="6" y1="7.5" x2="6.01" y2="7.5"></line><line x1="6" y1="16.5" x2="6.01" y2="16.5"></line></svg>',
    online: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"></circle><path d="M8.5 12.5l2.5 2.5 4.5-5"></path></svg>',
    offline: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"></circle><path d="M9.5 9.5l5 5"></path><path d="M14.5 9.5l-5 5"></path></svg>'
  };

  function reduced() {
    return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion:reduce)').matches);
  }

  function counts() {
    var mp = document.querySelector('.main-area .machines-page');
    if (!mp) return null;
    var rows = Array.prototype.slice.call(mp.querySelectorAll('.table-wrap tbody tr'));
    var on = 0, off = 0;
    rows.forEach(function (tr) {
      var st = tr.querySelector('.col-status, .status-pill, .status-badge');
      var t = st ? (st.textContent || '').trim() : (tr.textContent || '');
      if (t.indexOf('在线') >= 0) on++;
      else if (t.indexOf('离线') >= 0) off++;
    });
    return { total: rows.length, on: on, off: off };
  }

  function pct(n, total) { return total ? Math.round((n / total) * 100) : 0; }
  /* 同值不写：避免 textContent 赋值自激 MutationObserver 循环（见 countUp 注释） */
  function setTxt(el, s) { if (el && el.textContent !== s) el.textContent = s; }

  function card(label, ico) {
    var el = document.createElement('article');
    el.className = 'stat-card mc-anim';
    el.innerHTML =
      '<div class="stat-top"><span class="stat-icon">' + ico + '</span></div>' +
      '<p class="stat-value">0</p>' +
      '<p class="stat-label">' + label + '</p>' +
      '<p class="stat-sub"></p>';
    return el;
  }

  function countUp(el, to) {
    /* ⚠ 值未变则不写 DOM（2026-09-17）：给 textContent 赋**同值**也会产生 mutation，
        会自触发 200ms 防抖 → schedule() → run() → update() → 再赋值 → **永久 5Hz 自激循环**，
        页面永远不"静默"（实测 /machines、/alerts 静默等待 8s 必然超时、每秒 5 次突变批次）。 */
    if (el.textContent === String(to)) return;
    var from = parseInt(String(el.textContent || '0').replace(/[^\d-]/g, ''), 10);
    if (isNaN(from)) from = 0;
    if (reduced() || from === to) { el.textContent = String(to); return; }
    var dur = 650, t0 = (window.performance && performance.now) ? performance.now() : Date.now();
    function step(t) {
      var p = Math.min(1, (t - t0) / dur);
      var e = 1 - Math.pow(1 - p, 3);
      el.textContent = String(Math.round(from + (to - from) * e));
      if (p < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  function update(g) {
    var c = counts();
    if (!c) return;
    var vs = g.querySelectorAll('.stat-value');
    var ss = g.querySelectorAll('.stat-sub');
    if (vs.length < 3) return;
    countUp(vs[0], c.total);
    countUp(vs[1], c.on);
    countUp(vs[2], c.off);
    setTxt(ss[0], '当前列表');
    setTxt(ss[1], '占比 ' + pct(c.on, c.total) + '%');
    setTxt(ss[2], '占比 ' + pct(c.off, c.total) + '%');
  }

  function build() {
    var g = document.createElement('div');
    g.className = 'stat-grid';
    g.id = ID;
    g.appendChild(card('设备总数', ICONS.total));
    g.appendChild(card('在线', ICONS.online));
    g.appendChild(card('离线', ICONS.offline));
    return g;
  }

  // 立即补齐（Vue 重渲染抹掉时即时补回，避免闪烁）
  function sync() {
    if (location.pathname.replace(/\/$/, '') !== '/machines') {
      var old = document.getElementById(ID);
      if (old && old.parentNode) old.parentNode.removeChild(old);
      return false;
    }
    var mp = document.querySelector('.main-area .machines-page');
    if (!mp) return false;
    var g = document.getElementById(ID);
    if (!g) {
      var anchor = mp.querySelector('.page-header') || mp.firstElementChild;
      g = build();
      if (anchor && anchor.parentNode === mp) anchor.insertAdjacentElement('afterend', g);
      else mp.insertBefore(g, mp.firstChild);
    }
    return true;
  }

  var timer = null, confirm = null;
  function run() {
    try { var g = document.getElementById(ID); if (g) update(g); } catch (e) {}
    // DOM 常分批渲染：更新后 800ms 再确认一次，避免停在未稳定的中间态
    if (confirm) clearTimeout(confirm);
    confirm = setTimeout(function () {
      confirm = null;
      try { var g = document.getElementById(ID); if (g) update(g); } catch (e) {}
    }, 800);
  }

  function schedule() {
    sync();
    if (timer) clearTimeout(timer);
    timer = setTimeout(function () { timer = null; run(); }, 200);
  }

  function boot() {
    try {
      schedule();
      var mo = new MutationObserver(schedule);
      // characterData：状态文字(在线/离线)变化也要触发重算
      mo.observe(document.body, { childList: true, subtree: true, characterData: true });
    } catch (e) {}
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
  window.addEventListener('popstate', schedule);
  var ps = history.pushState;
  if (ps) {
    history.pushState = function () { var r = ps.apply(this, arguments); setTimeout(schedule, 60); return r; };
  }
})();

/* ══ MC-MACH-VIEW 设备管理「列表 / 卡片」视图切换 ══ */
/* ===MC-MACH-VIEW 2026-09-16===
 * 需求：设备管理 /machines 加一个切换按钮，把表格列表变成「监控详情 /monitor」那种卡片。
 * - 数据源＝当前表格行（跟随筛选/搜索，与 MC-MACH-STATS 三卡同口径）
 * - 卡片外观复用 .machine-mini-card（mc-custom.css L133 既有规则），本模块只补内部布局
 * - 卡片点击 → 代理该行原生「详情」按钮（复用原生 /monitor/<id> 跳转，零 id 依赖）
 *     hover 浮出的铅笔 → 代理「编辑」按钮
 * - 进度条配色对齐监控详情：<60 绿 #52c41a / 60~80 橙 #faad14 / >=80 红 #ff4d4f
 * - 视图模式记 localStorage('mcMachView')，默认 list；切换控件挂在 .page-header 右侧（绝对定位，不动原生流）
 */
(function () {
  'use strict';
  var ID = 'mc-mach-view';
  var GRID_ID = 'mc-mach-cards';
  var GROUP_ID = 'mc-mach-groups';
  var LS_KEY = 'mcMachView';
  var C_OK = '#52c41a', C_WARN = '#faad14', C_BAD = '#ff4d4f';

  var ICON_LIST = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="6" x2="21" y2="6"></line><line x1="8" y1="12" x2="21" y2="12"></line><line x1="8" y1="18" x2="21" y2="18"></line><line x1="3" y1="6" x2="3.01" y2="6"></line><line x1="3" y1="12" x2="3.01" y2="12"></line><line x1="3" y1="18" x2="3.01" y2="18"></line></svg>';
  var ICON_CARDS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1.5"></rect><rect x="14" y="3" width="7" height="7" rx="1.5"></rect><rect x="3" y="14" width="7" height="7" rx="1.5"></rect><rect x="14" y="14" width="7" height="7" rx="1.5"></rect></svg>';
  /* 2026-09-17 新增「按分组」视图：数据源 /api/advanced/group-stats（后端早已就绪、此前无入口） */
  var ICON_GROUPS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7h18"></path><path d="M3 12h18"></path><path d="M3 17h18"></path><circle cx="6.5" cy="7" r="1.6"></circle><circle cx="6.5" cy="12" r="1.6"></circle><circle cx="6.5" cy="17" r="1.6"></circle></svg>';
  var ICON_EDIT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"></path><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"></path></svg>';

  function getView() {
    try {
      var v = localStorage.getItem(LS_KEY);
      return (v === 'cards' || v === 'groups') ? v : 'list';
    } catch (e) { return 'list'; }
  }
  function setView(v) { try { localStorage.setItem(LS_KEY, v); } catch (e) {} }
  function page() { return document.querySelector('.main-area .machines-page'); }

  function num(s) { var m = String(s == null ? '' : s).match(/-?\d+(\.\d+)?/); return m ? parseFloat(m[0]) : NaN; }
  function barColor(v) { if (!isFinite(v)) return C_OK; if (v >= 80) return C_BAD; if (v >= 60) return C_WARN; return C_OK; }

  // 需要渲染的行：必须带名称列（排除子机展开行/空态行）
  function rows() {
    var mp = page(); if (!mp) return [];
    return Array.prototype.slice.call(mp.querySelectorAll('.table-wrap tbody tr')).filter(function (tr) {
      return !!tr.querySelector('.col-name');
    });
  }

  function rowData(tr) {
    var txt = function (sel) { var e = tr.querySelector(sel); return e ? (e.textContent || '').trim() : ''; };
    var ms = Array.prototype.slice.call(tr.querySelectorAll('.col-metric')).slice(0, 3).map(function (td) {
      return (td.textContent || '').trim();
    });
    // 名称：优先取名称链接文本（表格里子机带折叠树前缀 ├ └ │，卡片需还原为纯名，与监控详情一致）
    var linkEl = tr.querySelector('.col-name a, .col-name .row-link');
    var name = linkEl ? (linkEl.textContent || '').trim() : txt('.col-name');
    name = name.replace(/^[\s\u2500-\u257F|]+/, '');
    return {
      name: name || '未命名',
      ip: txt('.col-ip'),
      type: txt('.col-type'),
      online: txt('.col-status').indexOf('在线') >= 0,
      cpu: ms[0] || '—', mem: ms[1] || '—', disk: ms[2] || '—',
      tr: tr
    };
  }

  // 代理原生行操作按钮（详情 / 编辑 / AI），完全复用原生跳转逻辑
  function proxyAction(tr, label) {
    try {
      var btns = tr.querySelectorAll('.col-actions button, .col-actions a');
      for (var i = 0; i < btns.length; i++) {
        if ((btns[i].textContent || '').trim() === label) { btns[i].click(); return true; }
      }
      if (label === '详情') {
        var link = tr.querySelector('.col-name a, .row-link');
        if (link) { link.click(); return true; }
      }
    } catch (e) {}
    return false;
  }

  function metric(label, valTxt) {
    var v = num(valTxt);
    var pct = isFinite(v) ? Math.max(0, Math.min(100, v)) : 0;
    var el = document.createElement('div');
    el.className = 'mini-metric';
    el.innerHTML =
      '<span class="label">' + label + '</span>' +
      '<span class="mc-prog"><span class="mc-prog-outer"><span class="mc-prog-inner" style="width:' + pct + '%;background:' + barColor(v) + '"></span></span></span>' +
      '<span class="mc-prog-txt"></span>';
    el.querySelector('.mc-prog-txt').textContent = (valTxt && valTxt !== '—') ? valTxt : '—';
    return el;
  }

  function card(d) {
    var el = document.createElement('article');
    el.className = 'machine-mini-card mc-anim';
    el.setAttribute('data-mc-name', d.name);

    var head = document.createElement('div');
    head.className = 'mini-header';
    head.innerHTML =
      '<span class="mini-name"></span>' +
      '<button class="mc-mini-edit" type="button" title="编辑" aria-label="编辑">' + ICON_EDIT + '</button>' +
      '<span class="status-dot ' + (d.online ? 'online' : 'offline') + '" title="' + (d.online ? '在线' : '离线') + '"></span>';
    head.querySelector('.mini-name').textContent = d.name;

    var ip = document.createElement('div');
    ip.className = 'mini-ip';
    ip.textContent = (d.ip || '—') + ' | ' + (d.type || '—');

    var ms = document.createElement('div');
    ms.className = 'mini-metrics';
    ms.appendChild(metric('CPU', d.cpu));
    ms.appendChild(metric('MEM', d.mem));
    ms.appendChild(metric('DISK', d.disk));

    el.appendChild(head); el.appendChild(ip); el.appendChild(ms);

    el.addEventListener('click', function () { proxyAction(d.tr, '详情'); });
    head.querySelector('.mc-mini-edit').addEventListener('click', function (e) {
      e.stopPropagation();
      proxyAction(d.tr, '编辑');
    });
    return el;
  }

  // 每张卡片的入场节奏（--i 供 CSS animation-delay 使用；封顶避免长列表尾巴太久）
  function stagger(el, i) { el.style.setProperty('--i', String(Math.min(i, 14))); }

  var _sig = '';
  function signature(rs) {
    return rs.length + '|' + rs.map(function (tr) {
      var t = (tr.textContent || '').replace(/\s+/g, '');
      return t.length + ':' + t.slice(0, 48);
    }).join('~');
  }

  function render(g, rs) {
    g.innerHTML = '';
    if (!rs.length) {
      var e = document.createElement('div');
      e.className = 'mc-card-empty';
      e.textContent = '暂无设备';
      g.appendChild(e);
      return;
    }
    var frag = document.createDocumentFragment();
    rs.forEach(function (tr, i) { var c = card(rowData(tr)); stagger(c, i); frag.appendChild(c); });
    g.appendChild(frag);
  }

  function ensureGrid() {
    var mp = page(); if (!mp) return null;
    var wrap = mp.querySelector('.table-wrap'); if (!wrap) return null;
    var g = document.getElementById(GRID_ID);
    if (!g) {
      g = document.createElement('div');
      g.id = GRID_ID; g.className = 'mc-card-grid';
      wrap.insertAdjacentElement('afterend', g);
    } else if (g.previousElementSibling !== wrap) {
      // Vue 重排后归位（保持紧贴表格容器之后）
      wrap.insertAdjacentElement('afterend', g);
    }
    return g;
  }

  /* 分组视图容器（与卡片网格同级、互斥显示）
     ⚠ 只在 groups 模式调用：list/cards 模式绝不触碰本节点，否则「插入→mutation→sync→再插入」
        会构成 MutationObserver 自循环，把主线程 100% 打满（2026-09-17 实测冻死）。 */
  function ensureGroupBox() {
    var mp = page(); if (!mp) return null;
    var wrap = mp.querySelector('.table-wrap'); if (!wrap) return null;
    var g = document.getElementById(GROUP_ID);
    if (!g) {
      g = document.createElement('div');
      g.id = GROUP_ID; g.className = 'mc-group-grid';
      g.style.display = 'none';           // 先隐藏再插入，避免中间态闪动
      wrap.insertAdjacentElement('afterend', g);
    } else if (!g.isConnected) {
      wrap.insertAdjacentElement('afterend', g);
    }
    return g;
  }

  /* ---------- 分组视图（数据源 /api/advanced/group-stats） ---------- */
  var _gLoaded = 0, _gCache = null, _gFetching = false;
  function loadGroups(cb) {
    // 30s 缓存：切来切去不重复请求
    if (_gCache && (Date.now() - _gLoaded) < 30000) { cb(_gCache); return; }
    if (_gFetching) return;              // 在途请求去重：绝不允许同一轮 sync 反复发起
    _gFetching = true;
    var tk = '';
    try { tk = localStorage.getItem('token') || ''; } catch (e) {}
    var hd = { 'Content-Type': 'application/json' };
    if (tk) hd.Authorization = 'Bearer ' + tk;
    fetch('/api/advanced/group-stats', { headers: hd, cache: 'no-store' })
      .then(function (r) { return r.json().catch(function () { return null; }).then(function (j) { return { ok: r.ok, body: j }; }); })
      .then(function (res) {
        _gFetching = false;
        if (!res.ok || !res.body || res.body.code !== 0) { cb(null); return; }
        _gCache = res.body.data || []; _gLoaded = Date.now(); cb(_gCache);
      })
      .catch(function () { _gFetching = false; cb(null); });
  }

  function groupCard(g, i) {
    var total = +g.total || 0, online = +g.online || 0;
    var offline = Math.max(0, total - online);
    var rate = total ? Math.round(online / total * 100) : 0;
    var col = rate >= 90 ? C_OK : (rate >= 60 ? C_WARN : C_BAD);
    var el = document.createElement('article');
    el.className = 'machine-mini-card mc-group-card mc-anim';
    el.setAttribute('data-mc-group', g.name || '');
    var head = document.createElement('div');
    head.className = 'mini-header';
    head.innerHTML = '<span class="mini-name"></span>' +
      '<span class="mc-group-rate" style="color:' + col + '"></span>';
    head.querySelector('.mini-name').textContent = g.name || '未分组';
    head.querySelector('.mc-group-rate').textContent = rate + '%';
    var sub = document.createElement('div');
    sub.className = 'mini-ip';
    sub.textContent = '在线 ' + online + ' / 共 ' + total + ' 台' + (offline ? '　离线 ' + offline : '');
    var bar = document.createElement('div');
    bar.className = 'mini-metrics';
    bar.innerHTML = '<span class="label">在线率</span>' +
      '<span class="mc-prog"><span class="mc-prog-outer"><span class="mc-prog-inner" style="width:' + rate + '%;background:' + col + '"></span></span></span>' +
      '<span class="mc-prog-txt"></span>';
    bar.querySelector('.mc-prog-txt').textContent = online + '/' + total;
    el.appendChild(head); el.appendChild(sub); el.appendChild(bar);
    el.addEventListener('click', function () {
      // 点击分组 → 切回列表并把搜索框填为该分组名（复用原生筛选，零后端依赖）
      var inp = document.querySelector('.main-area .machines-page input[type="search"], .main-area .machines-page input[placeholder*="搜索"], .main-area .machines-page .toolbar input');
      if (inp) {
        inp.value = g.name || '';
        inp.dispatchEvent(new Event('input', { bubbles: true }));
        inp.dispatchEvent(new Event('change', { bubbles: true }));
      }
      setView('list'); _sig = ''; sync();
    });
    return el;
  }

  var _gSig = '';
  function renderGroups(box, arr) {
    box.innerHTML = '';
    if (!arr || !arr.length) {
      var e = document.createElement('div');
      e.className = 'mc-card-empty';
      e.textContent = '暂无分组数据';
      box.appendChild(e);
      return;
    }
    var frag = document.createDocumentFragment();
    arr.forEach(function (g, i) {
      var c = groupCard(g, i); stagger(c, i); frag.appendChild(c);
    });
    box.appendChild(frag);
  }

  function applyView() {
    var mp = page(); if (!mp) return;
    var v = getView();
    var wrap = mp.querySelector('.table-wrap');
    var g = document.getElementById(GRID_ID);
    var gb = document.getElementById(GROUP_ID);
    /* ⚠ 只在值真的变化时写 style：写同值虽不触发 mutation（style 属性仍算 mutation），
       但会白白制造一次属性变更 → 配合 MutationObserver 放大成抖动。 */
    if (wrap) { var wd = (v === 'list') ? '' : 'none'; if (wrap.style.display !== wd) wrap.style.display = wd; }
    if (g) { var gd = (v === 'cards') ? '' : 'none'; if (g.style.display !== gd) g.style.display = gd; }
    if (gb) { var bd = (v === 'groups') ? '' : 'none'; if (gb.style.display !== bd) gb.style.display = bd; }
    var sw = document.getElementById(ID);
    if (sw) {
      Array.prototype.forEach.call(sw.querySelectorAll('.mc-vs-btn'), function (b) {
        b.classList.toggle('is-active', b.getAttribute('data-v') === v);
      });
    }
  }

  function ensureSwitch() {
    var mp = page(); if (!mp) return;
    var ph = mp.querySelector('.page-header'); if (!ph) return;
    if (document.getElementById(ID)) return;
    var sw = document.createElement('div');
    sw.id = ID; sw.className = 'mc-view-switch';
    sw.setAttribute('role', 'group');
    sw.setAttribute('aria-label', '视图切换');
    sw.innerHTML =
      '<button class="mc-vs-btn" type="button" data-v="list" title="列表视图">' + ICON_LIST + '</button>' +
      '<button class="mc-vs-btn" type="button" data-v="cards" title="卡片视图">' + ICON_CARDS + '</button>' +
      '<button class="mc-vs-btn" type="button" data-v="groups" title="分组视图">' + ICON_GROUPS + '</button>';
    sw.addEventListener('click', function (e) {
      var b = (e.target && e.target.closest) ? e.target.closest('.mc-vs-btn') : null;
      if (!b) return;
      var v = b.getAttribute('data-v');
      if (v === getView()) return;
      setView(v); _sig = ''; _gSig = ''; sync();
    });
    ph.appendChild(sw);
  }

  function sync() {
    var path = location.pathname.replace(/\/$/, '');
    if (path !== '/machines') {
      var sw = document.getElementById(ID); if (sw && sw.parentNode) sw.parentNode.removeChild(sw);
      var g0 = document.getElementById(GRID_ID); if (g0 && g0.parentNode) g0.parentNode.removeChild(g0);
      var gb0 = document.getElementById(GROUP_ID); if (gb0 && gb0.parentNode) gb0.parentNode.removeChild(gb0);
      _sig = ''; _gSig = '';
      return false;
    }
    if (!page()) return false;
    ensureSwitch();
    var v = getView();
    /* ⚠ 顺序很重要：只在各自的模式下创建/渲染对应容器，绝不在 list 模式碰它们
       （否则容器插入/layout 会造成 mutation → schedule → sync 自循环，主线程冻死） */
    if (v === 'cards') {
      var g = ensureGrid();
      if (g) {
        var rs = rows();
        var sig = signature(rs);
        if (sig !== _sig) { _sig = sig; render(g, rs); }
      }
    } else if (v === 'groups') {
      var gb = ensureGroupBox();
      if (gb) {
        /* 数据签名：仅当缓存内容或时间戳变化才重渲染，避免 observer 自循环 */
        var sigNow = String(_gCache ? _gCache.length : -1) + '|' + (_gLoaded || 0);
        if (sigNow !== _gSig) {
          if (_gCache) {
            _gSig = sigNow;
            renderGroups(gb, _gCache);
          } else {
            loadGroups(function (arr) {
              var box = document.getElementById(GROUP_ID);
              if (!box) return;
              _gSig = String(arr ? arr.length : -1) + '|' + (_gLoaded || 0);
              renderGroups(box, arr);
              applyView();
            });
          }
        }
      }
    }
    applyView();
    return true;
  }

  var timer = null;
  function schedule() {
    sync();
    if (timer) clearTimeout(timer);
    timer = setTimeout(function () { timer = null; sync(); }, 200);
  }

  function boot() {
    try {
      schedule();
      var mo = new MutationObserver(schedule);
      mo.observe(document.body, { childList: true, subtree: true, characterData: true });
    } catch (e) {}
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
  window.addEventListener('popstate', schedule);
  var ps = history.pushState;
  if (ps) {
    history.pushState = function () { var r = ps.apply(this, arguments); setTimeout(schedule, 60); return r; };
  }
})();


/* ══ MC-ALERT-STATS 告警日志顶部总结卡片 ══ */
/* ===MC-ALERT-STATS 2026-09-16===
 * 需求：告警日志 /alerts 顶部加一排总结卡片（与设备管理 / 监控总览同风格），表格保持表格。
 * - 复用平台既有 .stat-grid / .stat-card（mc CSS 已统一风格），零新增 CSS
 * - 口径＝**全部告警**（与页头「N 条未处理」/ 分页「N 条记录」自洽），不随筛选变化
 * - 数据源＝GET /api/alerts/?limit=500（与页面自身同端点），30s 缓存；标记解决后自动重取
 * - 数字 count-up 650ms；prefers-reduced-motion 下直接赋值
 * - 位置：.alerts-page > .page-header 之后、工具栏之前（与设备管理一致）
 */
(function () {
  'use strict';
  var ID = 'mc-alert-stats';
  var ICONS = {
    pending: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"></circle><path d="M12 7.5v5l3.6 2.1"></path></svg>',
    critical: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3.4 21.2 19.6H2.8z"></path><line x1="12" y1="9.4" x2="12" y2="13.8"></line><line x1="12" y1="16.6" x2="12.01" y2="16.6"></line></svg>',
    warning: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"></circle><line x1="12" y1="7.4" x2="12" y2="13"></line><line x1="12" y1="16.4" x2="12.01" y2="16.4"></line></svg>',
    info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"></circle><line x1="12" y1="11" x2="12" y2="16.4"></line><line x1="12" y1="7.6" x2="12.01" y2="7.6"></line></svg>'
  };

  function page() { return document.querySelector('.main-area .alerts-page'); }
  function reduced() {
    return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion:reduce)').matches);
  }

  // ---- 数据：/api/alerts/?limit=500，30s 缓存 ----
  var _cache = null, _cacheAt = 0, _q = null;
  function load(force, cb) {
    var now = Date.now();
    if (!force && _cache && (now - _cacheAt) < 30000) { cb(_cache); return; }
    if (_q) { _q.push(cb); return; }
    _q = [cb];
    var tk = '';
    try { tk = localStorage.getItem('token') || ''; } catch (e) {}
    var hd = { 'Content-Type': 'application/json' };
    if (tk) hd.Authorization = 'Bearer ' + tk;
    var done = function (arr) {
      var q = _q; _q = null;
      q.forEach(function (f) { try { f(arr); } catch (e) {} });
    };
    fetch('/api/alerts/?limit=500', { headers: hd })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var arr = Array.isArray(d) ? d : (d && (d.items || d.data)) || [];
        _cache = arr; _cacheAt = Date.now();
        done(arr);
      })
      .catch(function () { done(null); });
  }

  function tally(arr) {
    if (!arr) return null;
    var c = { total: 0, pending: 0, critical: 0, warning: 0, info: 0 };
    arr.forEach(function (a) {
      c.total++;
      var lvl = a && a.alert_level ? String(a.alert_level) : '';
      if (lvl === 'critical' || lvl === 'error') c.critical++;
      else if (lvl === 'warning') c.warning++;
      else if (lvl === 'info') c.info++;
      if (!a || a.status !== 'resolved') c.pending++;
    });
    return c;
  }

  function pct(n, total) { return total ? Math.round((n / total) * 100) : 0; }
  /* 同值不写：避免 textContent 赋值自激 MutationObserver 循环（见 countUp 注释） */
  function setTxt(el, s) { if (el && el.textContent !== s) el.textContent = s; }

  // ---- 卡片 ----
  function card(label, sub, ico, bar) {
    var el = document.createElement('article');
    el.className = 'stat-card mc-anim';
    if (bar) el.style.setProperty('--bar', bar);
    el.innerHTML =
      '<div class="stat-top"><span class="stat-icon">' + ico + '</span></div>' +
      '<p class="stat-value">0</p>' +
      '<p class="stat-label">' + label + '</p>' +
      '<p class="stat-sub">' + (sub || '') + '</p>';
    return el;
  }

  function countUp(el, to) {
    /* ⚠ 值未变则不写 DOM（2026-09-17）：给 textContent 赋**同值**也会产生 mutation，
        会自触发 200ms 防抖 → schedule() → run() → update() → 再赋值 → **永久 5Hz 自激循环**，
        页面永远不"静默"（实测 /machines、/alerts 静默等待 8s 必然超时、每秒 5 次突变批次）。 */
    if (el.textContent === String(to)) return;
    var from = parseInt(String(el.textContent || '0').replace(/[^\d-]/g, ''), 10);
    if (isNaN(from)) from = 0;
    if (reduced() || from === to) { el.textContent = String(to); return; }
    var dur = 650, t0 = (window.performance && performance.now) ? performance.now() : Date.now();
    function step(t) {
      var p = Math.min(1, (t - t0) / dur);
      var e = 1 - Math.pow(1 - p, 3);
      el.textContent = String(Math.round(from + (to - from) * e));
      if (p < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  function build() {
    var g = document.createElement('div');
    g.className = 'stat-grid';
    g.id = ID;
    g.appendChild(card('未处理', '', ICONS.pending, 'var(--mc-bar-1)'));
    g.appendChild(card('严重', '', ICONS.critical, 'var(--mc-bar-4)'));
    g.appendChild(card('警告', '', ICONS.warning, 'var(--mc-bar-3)'));
    g.appendChild(card('信息', '', ICONS.info, 'var(--mc-bar-6)'));
    return g;
  }

  function update(arr) {
    var g = document.getElementById(ID);
    if (!g) return;
    var c = tally(arr);
    if (!c) return;
    var vs = g.querySelectorAll('.stat-value');
    var ss = g.querySelectorAll('.stat-sub');
    if (vs.length < 4) return;
    countUp(vs[0], c.pending);
    countUp(vs[1], c.critical);
    countUp(vs[2], c.warning);
    countUp(vs[3], c.info);
    setTxt(ss[0], '共 ' + c.total + ' 条');
    setTxt(ss[1], '占比 ' + pct(c.critical, c.total) + '%');
    setTxt(ss[2], '占比 ' + pct(c.warning, c.total) + '%');
    setTxt(ss[3], '占比 ' + pct(c.info, c.total) + '%');
  }

  // ---- 挂载 ----
  function sync() {
    if (location.pathname.replace(/\/$/, '') !== '/alerts') {
      var old = document.getElementById(ID);
      if (old && old.parentNode) old.parentNode.removeChild(old);
      _sig = null;
      return false;
    }
    var mp = page();
    if (!mp) return false;
    if (!document.getElementById(ID)) {
      var ph = mp.querySelector('.page-header');
      var g = build();
      if (ph && ph.parentNode === mp) ph.insertAdjacentElement('afterend', g);
      else mp.insertBefore(g, mp.firstChild);
    }
    return true;
  }

  // 状态列签名：标记解决 / 翻页后变化 → 重新取数（30s 缓存兜底防抖）
  var _sig = null;
  function statusSig() {
    var mp = page();
    if (!mp) return '';
    return Array.prototype.slice.call(mp.querySelectorAll('.table-wrap tbody tr .col-status'))
      .map(function (e) { return (e.textContent || '').trim(); }).join('|');
  }

  function run() {
    if (!sync()) return;
    var sig = statusSig();
    if (_sig === null) { _sig = sig; load(false, update); return; }
    if (sig !== _sig) { _sig = sig; load(false, update); return; }
    // 未变化：仅用缓存刷新一次（首次渲染 / Vue 重抹后补回）
    load(false, update);
  }

  var timer = null, confirm = null;
  function schedule() {
    if (timer) clearTimeout(timer);
    timer = setTimeout(function () { timer = null; run(); }, 200);
    // DOM 常分批渲染：稍后再确认一次，避免停在未稳定的中间态
    if (confirm) clearTimeout(confirm);
    confirm = setTimeout(function () { confirm = null; run(); }, 800);
  }

  function boot() {
    try {
      run();
      var mo = new MutationObserver(schedule);
      mo.observe(document.body, { childList: true, subtree: true, characterData: true });
    } catch (e) {}
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
  window.addEventListener('popstate', schedule);
  var ps = history.pushState;
  if (ps) {
    history.pushState = function () { var r = ps.apply(this, arguments); setTimeout(schedule, 60); return r; };
  }
})();

/* ══ MC-MD-COMPARE 设备详情「对比」panel ══ */
/* ===MC-MD-COMPARE 2026-09-17 设备详情同环比===
 * 目标：把后端早已就绪但前端无入口的两个接口接上设备详情页 /monitor/:id
 *   GET /api/advanced/compare/{id}?metric=&period=   → 本期 vs 上期 曲线 + 均值 + 变化率
 *   GET /api/advanced/baseline/{id}                  → 14 天基线 mean/std（3 指标）
 * 载体：原生 .detail-page 下的 .chart-row（2 列网格）末尾追加一个 <section class="chart-panel">
 *  - 复用原生 .chart-panel/.chart-header/.chart-title 视觉（零视觉割裂）
 *  - 图形自绘 SVG（原生图是 ECharts 实例，外部脚本无法安全复用其 option）
 *  - 不引入任何新依赖、不做全局样式覆盖
 * 安全：所有网络请求带 Bearer；异常一律静默降级为占位文案，绝不抛错影响原生页面
 */
(function () {
  'use strict';

  var HOST_ID = 'mc-md-compare';
  var PAGE_SEL = '.main-area .detail-page';
  var ROW_SEL = '.chart-row';

  var METRICS = [
    { k: 'cpu_percent', label: 'CPU', color: '#3b82f6' },
    { k: 'memory_percent', label: '内存', color: '#a855f7' },
    { k: 'disk_percent', label: '磁盘', color: '#f59e0b' }
  ];
  var PERIODS = [
    { k: '24h', label: '24h' },
    { k: '7d', label: '7d' },
    { k: '30d', label: '30d' }
  ];
  var BAD = '#f87171', GOOD = '#34d399', NEU = '#94a3b8';

  var ST = { metric: 'cpu_percent', period: '24h', loading: false, id: 0, data: null, sig: '' };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
    });
  }
  function authHeaders() {
    var t = '';
    try { t = localStorage.getItem('token') || ''; } catch (e) {}
    return t ? { Authorization: 'Bearer ' + t } : {};
  }
  function pathId() {
    var m = (location.pathname || '').match(/\/monitor\/(\d+)/);
    return m ? parseInt(m[1], 10) : 0;
  }
  function num(v) { return (typeof v === 'number' && isFinite(v)) ? v : 0; }
  function fmt(v, d) {
    var n = Number(v);
    if (!isFinite(n)) return '—';
    return n.toFixed(d == null ? 1 : d);
  }

  /* ── 自绘 SVG 折线图：本期实线 + 上期虚线，纵轴自适应，含警戒/严重参考线 ── */
  function chartSVG(cur, prev, color) {
    var W = 660, H = 190, PL = 42, PR = 14, PT = 12, PB = 24;
    var iw = W - PL - PR, ih = H - PT - PB;
    var series = [];
    (cur || []).forEach(function (p) { series.push(num(p.value)); });
    (prev || []).forEach(function (p) { series.push(num(p.value)); });
    if (!series.length) return '';

    var mn = Math.min.apply(null, series), mx = Math.max.apply(null, series);
    if (mx <= 0) { mx = 1; }
    var pad = (mx - mn) * 0.12 || 1;
    var lo = Math.max(0, mn - pad), hi = mx + pad;
    var span = (hi - lo) || 1;

    function X(i, n) { return PL + (n <= 1 ? iw / 2 : iw * i / (n - 1)); }
    function Y(v) { return PT + ih - ih * (Math.max(lo, Math.min(hi, v)) - lo) / span; }
    function d(pts) {
      if (!pts.length) return '';
      return pts.map(function (p, i) {
        return (i ? 'L' : 'M') + X(i, pts.length).toFixed(1) + ' ' + Y(num(p.value)).toFixed(1);
      }).join(' ');
    }

    var g = [];
    // 网格 + 纵轴刻度（5 档）
    for (var t = 0; t <= 4; t++) {
      var yv = lo + span * t / 4, y = Y(yv);
      g.push('<line x1="' + PL + '" y1="' + y.toFixed(1) + '" x2="' + (W - PR) + '" y2="' + y.toFixed(1) +
        '" stroke="currentColor" stroke-opacity="0.10" stroke-width="1"/>');
      g.push('<text x="' + (PL - 7) + '" y="' + (y + 3.5).toFixed(1) + '" text-anchor="end" font-size="10" fill="currentColor" fill-opacity="0.45">' +
        esc(fmt(yv, 0)) + '</text>');
    }
    // 警戒(60)/严重(80) 参考线
    [[60, BAD, '0.45'], [80, BAD, '0.55']].forEach(function (r) {
      if (r[0] < lo || r[0] > hi) return;
      var y = Y(r[0]);
      g.push('<line x1="' + PL + '" y1="' + y.toFixed(1) + '" x2="' + (W - PR) + '" y2="' + y.toFixed(1) +
        '" stroke="' + r[1] + '" stroke-opacity="' + r[2] + '" stroke-width="1" stroke-dasharray="4 4"/>');
    });
    // 上期（虚线、低透明度）
    if (prev && prev.length) {
      g.push('<path d="' + d(prev) + '" fill="none" stroke="' + NEU + '" stroke-opacity="0.75" stroke-width="1.5" stroke-dasharray="5 4" stroke-linejoin="round" stroke-linecap="round"/>');
    }
    // 本期（实线 + 渐变面积）
    if (cur && cur.length) {
      var gid = 'mcg-' + Math.abs((ST.id * 31 + (color || '').length));
      var area = d(cur) + ' L' + X(cur.length - 1, cur.length).toFixed(1) + ' ' + (PT + ih) +
        ' L' + X(0, cur.length).toFixed(1) + ' ' + (PT + ih) + ' Z';
      g.unshift('<defs><linearGradient id="' + gid + '" x1="0" y1="0" x2="0" y2="1">' +
        '<stop offset="0%" stop-color="' + color + '" stop-opacity="0.30"/>' +
        '<stop offset="100%" stop-color="' + color + '" stop-opacity="0.02"/></linearGradient></defs>');
      g.push('<path d="' + area + '" fill="url(#' + gid + ')"/>');
      g.push('<path d="' + d(cur) + '" fill="none" stroke="' + color + '" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>');
      // 末点高亮
      g.push('<circle cx="' + X(cur.length - 1, cur.length).toFixed(1) + '" cy="' + Y(num(cur[cur.length - 1].value)).toFixed(1) + '" r="3" fill="' + color + '"/>');
    }
    return '<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" height="' + H + '" preserveAspectRatio="none" style="display:block;overflow:visible">' + g.join('') + '</svg>';
  }

  /* ── 骨架 ── */
  function shell() {
    var mt = METRICS.map(function (m) {
      return '<button type="button" class="mdc-seg" data-metric="' + m.k + '"' +
        (ST.metric === m.k ? ' data-on="1"' : '') + '>' + esc(m.label) + '</button>';
    }).join('');
    var pt = PERIODS.map(function (p) {
      return '<button type="button" class="mdc-tab" data-period="' + p.k + '"' +
        (ST.period === p.k ? ' data-on="1"' : '') + '>' + esc(p.label) + '</button>';
    }).join('');
    return '' +
      '<div class="mdc-head">' +
        '<div class="mdc-title-wrap"><h4 class="chart-title mdc-title">同环比对比</h4>' +
          '<span class="mdc-legend"><i class="mdc-l1"></i>本期<i class="mdc-l2"></i>上期</span>' +
        '</div>' +
        '<div class="mdc-ctrl"><div class="mdc-segs">' + mt + '</div>' +
          '<div class="time-tabs mdc-tabs">' + pt + '</div></div>' +
      '</div>' +
      '<div class="mdc-body" id="mc-mdc-body"><div class="mdc-empty">加载中…</div></div>';
  }

  /* ── 渲染体（KPI 三卡 + 曲线 + 基线） ── */
  function body() {
    var d = ST.data;
    if (!d) return '<div class="mdc-empty">暂无对比数据</div>';
    if (d.error) return '<div class="mdc-empty">数据获取失败：' + esc(d.error) + '</div>';
    var m = null;
    for (var i = 0; i < METRICS.length; i++) if (METRICS[i].k === ST.metric) m = METRICS[i];
    var chg = num(d.change_pct);
    var trend = d.trend || 'stable';
    var cCol = trend === 'up' ? BAD : trend === 'down' ? GOOD : NEU;
    var arrow = trend === 'up' ? '▲' : trend === 'down' ? '▼' : '—';

    var kpis = [
      { k: '本期均值', v: fmt(d.avg_current) + '%', s: (ST.data && d.current_series ? (d.current_series.length + ' 个采样点') : '') , c: m ? m.color : '' },
      { k: '上期均值', v: fmt(d.avg_previous) + '%', s: (d.previous_series ? (d.previous_series.length + ' 个采样点') : ''), c: NEU },
      { k: '环比变化', v: arrow + ' ' + (chg > 0 ? '+' : '') + fmt(chg) + '%', s: trend === 'up' ? '上升' : trend === 'down' ? '下降' : '平稳', c: cCol }
    ].map(function (x) {
      return '<div class="mdc-kpi"><span class="mdc-kpi-k">' + esc(x.k) + '</span>' +
        '<span class="mdc-kpi-v" style="color:' + (x.c || 'inherit') + '">' + esc(x.v) + '</span>' +
        '<span class="mdc-kpi-s">' + esc(x.s) + '</span></div>';
    }).join('');

    var svg = chartSVG(d.current_series, d.previous_series, m ? m.color : '#3b82f6');
    var chart = svg || '<div class="mdc-empty" style="height:190px;display:flex;align-items:center;justify-content:center">该周期无采样数据</div>';

    // 基线（14 天 mean/std）小表
    var b = ST.baseline || {};
    var bl = METRICS.map(function (x) {
      var o = b[x.k] || {};
      var mean = o.mean != null ? fmt(o.mean) : '—';
      var std = o.std != null ? fmt(o.std) : '—';
      return '<div class="mdc-bl"><i style="background:' + x.color + '"></i>' +
        '<span class="mdc-bl-n">' + esc(x.label) + '</span>' +
        '<span class="mdc-bl-v">μ ' + esc(mean) + '</span>' +
        '<span class="mdc-bl-v2">σ ' + esc(std) + '</span></div>';
    }).join('');

    return '<div class="mdc-kpis">' + kpis + '</div>' +
      '<div class="mdc-chart">' + chart + '</div>' +
      '<div class="mdc-baseline"><span class="mdc-bl-h">14 天基线</span>' + bl + '</div>';
  }

  function paintBody() {
    var el = document.getElementById('mc-mdc-body');
    if (!el) return;
    var html = body();
    if (el.innerHTML !== html) el.innerHTML = html;
  }

  /* ── 取数 ── */
  function load(baselineToo) {
    if (!ST.id) return;
    var my = ST.id + '|' + ST.metric + '|' + ST.period;
    ST.loading = true; ST.sig = my;
    var url = '/api/advanced/compare/' + ST.id + '?metric=' + encodeURIComponent(ST.metric) + '&period=' + encodeURIComponent(ST.period);
    fetch(url, { headers: authHeaders() })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (ST.sig !== my) return; // 过期响应丢弃
        var d = (j && j.data) ? j.data : { error: (j && j.msg) || '响应异常' };
        if (d.error) d.error = String(d.error).slice(0, 120);
        ST.data = d; ST.loading = false;
        paintBody();
      })
      .catch(function (e) {
        if (ST.sig !== my) return;
        ST.data = { error: '网络错误' }; ST.loading = false; paintBody();
      });
    if (baselineToo) {
      fetch('/api/advanced/baseline/' + ST.id, { headers: authHeaders() })
        .then(function (r) { return r.json(); })
        .then(function (j) {
          if (ST.sig !== my) return;
          ST.baseline = (j && j.data) ? j.data : {};
          paintBody();
        })
        .catch(function () {});
    }
  }

  /* ── 挂载/替换 ── */
  function host() {
    var dp = document.querySelector(PAGE_SEL);
    if (!dp) return null;
    return dp.querySelector('#' + HOST_ID);
  }

  function mount() {
    var id = pathId();
    if (!id) { unmount(); return; }
    var dp = document.querySelector(PAGE_SEL);
    if (!dp) return;
    var row = dp.querySelector(ROW_SEL);
    if (!row) return; // 图表区没渲染完，等下轮

    var h = document.getElementById(HOST_ID);
    if (h && h.parentElement === row) {
      if (ST.id !== id) { ST.id = id; ST.data = null; ST.baseline = null; paint(); load(true); }
      return;
    }
    if (h && h.parentElement !== row) h.parentNode.removeChild(h);

    ST.id = id; ST.data = null; ST.baseline = null;
    var sec = document.createElement('section');
    sec.className = 'chart-panel mdc-panel';
    sec.id = HOST_ID;
    sec.setAttribute('data-v-mdc', '1');
    sec.innerHTML = '<div class="mdc-head"><div class="mdc-title-wrap"><h4 class="chart-title mdc-title">同环比对比</h4></div><div class="mdc-ctrl"></div></div><div class="mdc-body" id="mc-mdc-body"><div class="mdc-empty">加载中…</div></div>';
    row.appendChild(sec);
    paint();
    load(true);
  }

  function paint() {
    var sec = document.getElementById(HOST_ID);
    if (!sec) return;
    var head = sec.querySelector('.mdc-head');
    if (head) {
      // 仅重建头部控件（保留 body 节点身份，避免闪烁）
      var wrap = sec.querySelector('.mdc-title-wrap');
      var ctrl = sec.querySelector('.mdc-ctrl');
      if (wrap && !wrap.querySelector('.mdc-legend')) {
        wrap.insertAdjacentHTML('beforeend', '<span class="mdc-legend"><i class="mdc-l1"></i>本期<i class="mdc-l2"></i>上期</span>');
      }
      if (ctrl && !ctrl.children.length) {
        var mt = METRICS.map(function (m) {
          return '<button type="button" class="mdc-seg" data-metric="' + m.k + '"' + (ST.metric === m.k ? ' data-on="1"' : '') + '>' + esc(m.label) + '</button>';
        }).join('');
        var pt = PERIODS.map(function (p) {
          return '<button type="button" class="mdc-tab" data-period="' + p.k + '"' + (ST.period === p.k ? ' data-on="1"' : '') + '>' + esc(p.label) + '</button>';
        }).join('');
        ctrl.innerHTML = '<div class="mdc-segs">' + mt + '</div><div class="time-tabs mdc-tabs">' + pt + '</div>';
      }
    }
    paintBody();
  }

  function syncSeg() {
    var sec = document.getElementById(HOST_ID);
    if (!sec) return;
    Array.prototype.forEach.call(sec.querySelectorAll('.mdc-seg'), function (b) {
      if (b.getAttribute('data-metric') === ST.metric) b.setAttribute('data-on', '1');
      else b.removeAttribute('data-on');
    });
    Array.prototype.forEach.call(sec.querySelectorAll('.mdc-tab'), function (b) {
      if (b.getAttribute('data-period') === ST.period) b.setAttribute('data-on', '1');
      else b.removeAttribute('data-on');
    });
  }

  function unmount() {
    var h = document.getElementById(HOST_ID);
    if (h && h.parentNode) h.parentNode.removeChild(h);
    ST.id = 0; ST.data = null; ST.baseline = null;
  }

  /* ── 事件（委托在 document，避免 Vue 重渲染后失效） ── */
  document.addEventListener('click', function (e) {
    var t = e.target;
    if (!t || !t.closest) return;
    var seg = t.closest('#' + HOST_ID + ' .mdc-seg');
    if (seg) {
      var mk = seg.getAttribute('data-metric');
      if (mk && mk !== ST.metric) { ST.metric = mk; syncSeg(); load(false); }
      return;
    }
    var tab = t.closest('#' + HOST_ID + ' .mdc-tab');
    if (tab) {
      var pk = tab.getAttribute('data-period');
      if (pk && pk !== ST.period) { ST.period = pk; syncSeg(); load(false); }
      return;
    }
  }, true);

  /* ── 路由感知的巡检（含 Vue 重渲染自愈） ── */
  var timer = null, confirm = null;
  function schedule() {
    if (timer) clearTimeout(timer);
    timer = setTimeout(function () { timer = null; mount(); }, 180);
    if (confirm) clearTimeout(confirm);
    confirm = setTimeout(function () { confirm = null; mount(); }, 900);
  }

  var _last = location.pathname;
  function boot() {
    try {
      mount();
      var mo = new MutationObserver(schedule);
      mo.observe(document.body, { childList: true, subtree: true });
      // 1s 巡检：路由变化 / Vue 重抹 都能自愈；非详情页自动卸载
      setInterval(function () {
        if (location.pathname !== _last) { _last = location.pathname; schedule(); return; }
        if (pathId() && !document.getElementById(HOST_ID)) schedule();
        if (!pathId() && document.getElementById(HOST_ID)) unmount();
      }, 1000);
    } catch (e) {}
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
  window.addEventListener('popstate', schedule);
  var _ps = history.pushState;
  if (_ps) {
    history.pushState = function () { var r = _ps.apply(this, arguments); setTimeout(schedule, 60); return r; };
  }
})();
