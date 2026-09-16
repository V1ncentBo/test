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
      if (p === _active) return; // 已经落在这一页了：别把刚加载好的页再 toggle 关掉
      switchTo(p);
    }
    var _saved = null;
    var _obs = null, _obsSide = null;

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
    var RC_PAGES = { 'admin-options': 1, 'resources': 1, 'cabinets': 1, 'physical': 1 };
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
    var DEEP_PAGES = ['admin-users', 'admin-options', 'resources', 'cabinets', 'physical', 'db-monitor'];
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
      {k:'cabinets', label:'机房机柜', perm:'cabinets'}
    ];
    /* 监控中心二级子项（需求①：监控详情/告警日志挂监控中心，不挂资源管理）
       2026-09-16：删除「监控详情」子项 —— 设备管理页新增的「列表/卡片」视图已完全覆盖该列表页功能；
       设备详情 /monitor/<id> 仍从设备管理的「详情」按钮进入（见 MC-MACH-VIEW 的代理跳转）。 */
    var MC_SUBS = [
      {k:'home', label:'监控总览', route:'/', perm:'dashboard'},
      {k:'dev', label:'设备管理', route:'/machines', perm:'machines'},
      {k:'db', label:'数据库', page:'db-monitor', perm:'dbs'},
      {k:'alerts', label:'告警日志', route:'/alerts', perm:'alerts'}
    ];
    function applyRcOpen() {
      document.querySelectorAll('.sidebar-nav .rc-sub').forEach(function(a) {
        a.style.display = _rcOpen ? 'flex' : 'none';
      });
      var p = document.getElementById('res-cmdb-sub');
      if (p) p.classList.toggle('open', _rcOpen);
    }
    function toggleRc() {
      _rcOpen = !_rcOpen;
      applyRcOpen();
      if (_rcOpen && _active !== 'admin-options') {
        window.__RC_TAB__ = 'overview';
        switchTo('admin-options');
      }
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
    window.__RC_SET_ACTIVE__ = function(k) {
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
      if (_active === 'admin-users') return;
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
      _busy = true;
      _saved = Array.from(m.children).filter(function(c) {
        return c.id !== ROOT_ID && !c.classList.contains('topbar');
      });
      _saved.forEach(function(c) { c.style.display = 'none'; });
      var root = document.getElementById(ROOT_ID);
      if (!root) { root = document.createElement('div'); root.id = ROOT_ID; m.appendChild(root); }
      root.style.display = '';
      fetch(url, { cache: 'no-store' })
        .then(function(r) { return r.text(); })
        .then(function(text) {
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
          try { if (page === 'db-monitor') { var dbs = document.getElementById('mc-sub-db'); if (dbs) dbs.classList.add('active'); } } catch(e) {}
          _busy = false;
          flushPending();
        }).catch(function() { destroy(); _busy = false; flushPending(); });
    }

    function destroy() {
      releaseInjectedListeners();
      var root = document.getElementById(ROOT_ID);
      if (root) { root.style.display = 'none'; root.innerHTML = ''; }
      if (_saved) { _saved.forEach(function(c) { c.style.display = ''; }); _saved = null; }
      try { if (window.__resDestroy) window.__resDestroy(); } catch(e) {}
      try { if (window.__cabDestroy) window.__cabDestroy(); } catch(e) {}
      try { if (window.__phyDestroy) window.__phyDestroy(); } catch(e) {}
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
      _active = ''; markSub();
      _rcOpen = false; applyRcOpen();
      setTimeout(function() { destroy(); }, 80);
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
    function watchSide() {
      if (_obsSide) { try { _obsSide.disconnect(); } catch(e) {} }
      var nav = document.querySelector('.sidebar-nav');
      if (!nav) return;
      _obsSide = new MutationObserver(function() {
        if (_sideT) return;
        _sideT = setTimeout(function() {
          _sideT = 0;
          renameDevItem(); ensureMcSub(); ensureSub(); ensureRcSubs(); syncMcActive();
          if (_acctState) ensureAcctWidget();
          maybeDeepLink();
        }, 60);
      });
      _obsSide.observe(nav, { childList: true });
    }

    var _t = 0;
    (function boot() {
      if (document.querySelector('.sidebar-nav')) { renameDevItem(); ensureMcSub(); ensureSub(); ensureRcSubs(); watchMain(); watchSide(); refreshRcCounts(); fetchMe(function (st) { ensureAcctWidget(); maybeDeepLink(); }); syncMcActive(); setTimeout(syncMcActive, 600); }
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
    if (ss[0]) ss[0].textContent = '当前列表';
    if (ss[1]) ss[1].textContent = '占比 ' + pct(c.on, c.total) + '%';
    if (ss[2]) ss[2].textContent = '占比 ' + pct(c.off, c.total) + '%';
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
  var LS_KEY = 'mcMachView';
  var C_OK = '#52c41a', C_WARN = '#faad14', C_BAD = '#ff4d4f';

  var ICON_LIST = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="6" x2="21" y2="6"></line><line x1="8" y1="12" x2="21" y2="12"></line><line x1="8" y1="18" x2="21" y2="18"></line><line x1="3" y1="6" x2="3.01" y2="6"></line><line x1="3" y1="12" x2="3.01" y2="12"></line><line x1="3" y1="18" x2="3.01" y2="18"></line></svg>';
  var ICON_CARDS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1.5"></rect><rect x="14" y="3" width="7" height="7" rx="1.5"></rect><rect x="3" y="14" width="7" height="7" rx="1.5"></rect><rect x="14" y="14" width="7" height="7" rx="1.5"></rect></svg>';
  var ICON_EDIT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"></path><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"></path></svg>';

  function getView() {
    try { return localStorage.getItem(LS_KEY) === 'cards' ? 'cards' : 'list'; } catch (e) { return 'list'; }
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

  function applyView() {
    var mp = page(); if (!mp) return;
    var v = getView();
    var wrap = mp.querySelector('.table-wrap');
    var g = document.getElementById(GRID_ID);
    if (wrap) wrap.style.display = (v === 'cards') ? 'none' : '';
    if (g) g.style.display = (v === 'cards') ? '' : 'none';
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
      '<button class="mc-vs-btn" type="button" data-v="cards" title="卡片视图">' + ICON_CARDS + '</button>';
    sw.addEventListener('click', function (e) {
      var b = (e.target && e.target.closest) ? e.target.closest('.mc-vs-btn') : null;
      if (!b) return;
      var v = b.getAttribute('data-v');
      if (v === getView()) return;
      setView(v); _sig = ''; sync();
    });
    ph.appendChild(sw);
  }

  function sync() {
    var path = location.pathname.replace(/\/$/, '');
    if (path !== '/machines') {
      var sw = document.getElementById(ID); if (sw && sw.parentNode) sw.parentNode.removeChild(sw);
      var g0 = document.getElementById(GRID_ID); if (g0 && g0.parentNode) g0.parentNode.removeChild(g0);
      _sig = '';
      return false;
    }
    if (!page()) return false;
    ensureSwitch();
    var g = ensureGrid();
    if (g && getView() === 'cards') {
      var rs = rows();
      var sig = signature(rs);
      if (sig !== _sig) { _sig = sig; render(g, rs); }
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
    if (ss[0]) ss[0].textContent = '共 ' + c.total + ' 条';
    if (ss[1]) ss[1].textContent = '占比 ' + pct(c.critical, c.total) + '%';
    if (ss[2]) ss[2].textContent = '占比 ' + pct(c.warning, c.total) + '%';
    if (ss[3]) ss[3].textContent = '占比 ' + pct(c.info, c.total) + '%';
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
