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
    /* 监控中心二级子项（需求①：监控详情/告警日志挂监控中心，不挂资源管理） */
    var MC_SUBS = [
      {k:'home', label:'监控总览', route:'/', perm:'dashboard'},
      {k:'dev', label:'设备管理', route:'/machines', perm:'machines'},
      {k:'monitor', label:'监控详情', route:'/monitor', perm:'monitor'},
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
      var injected = !!_active; // 注入页（资源管理等）打开时不点亮，与 clearNavActive 口径一致
      var any = false;
      MC_SUBS.forEach(function (s) {
        var on = !injected && (s.route === path);
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

