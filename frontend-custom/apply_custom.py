#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能监控平台 · 前端定制一键注入器
================================================================
用途：当前端源码全量重建产生新的 dist 后，用它把所有-custom 定制「缝回」
      index.html，使热修不丢。**幂等**，可反复运行。

用法:
    python3 apply_custom.py --dist /path/to/dist            # 实际注入
    python3 apply_custom.py --dist /path/to/dist --dry-run  # 只看会做什么
    python3 apply_custom.py --dist /path/to/dist --force    # 已注入也重写

约定:
    定制资产（必须与本脚本同级或在 --src 指定）:
        mc-custom.css   所有自定义样式
        mc-custom.js    所有自定义脚本

Created: 2026-09-09
"""
import argparse
import hashlib
import os
import re
import shutil
import sys
import time

CSS_NAME = 'mc-custom.css'
JS_NAME = 'mc-custom.js'
CUSTOM_DIR = 'custom'
MARKER = '/custom/mc-custom'

VENDOR_SIZE_THRESHOLD = 500 * 1024  # 大于此值视为 echarts vendor chunk


def md5_of(path):
    with open(path, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()[:8]


def find_entry_assets(assets_dir):
    """扫描 dist/assets，找出需要 modulepreload 的 chunk。
    判据（兼顾可读与稳定）：
      - echarts vendor: 名为 index-*.js 且体积 > 500KB
      - Dashboard 路由 chunk: Dashboard-*.js / Dashboard-*.css
    """
    if not os.path.isdir(assets_dir):
        return [], [], []
    vendor, dash_js, dash_css = [], [], []
    for name in sorted(os.listdir(assets_dir)):
        p = os.path.join(assets_dir, name)
        if not os.path.isfile(p):
            continue
        size = os.path.getsize(p)
        if re.match(r'^index-.*\.js$', name) and size > VENDOR_SIZE_THRESHOLD:
            vendor.append((name, size))
        elif re.match(r'^Dashboard-.*\.js$', name):
            dash_js.append((name, size))
        elif re.match(r'^Dashboard-.*\.css$', name):
            dash_css.append((name, size))
    return vendor, dash_js, dash_css


def build_injection(vendor, dash_js, dash_css):
    """拼出要插入 index.html 的两段标签（head 的 preload + body 的引用）"""
    head_lines = []
    for name, size in vendor:
        head_lines.append('  <link rel="modulepreload" crossorigin href="/assets/%s">  <!-- vendor %.0fKB -->'
                          % (name, size / 1024))
    for name, _ in dash_js:
        head_lines.append('  <link rel="modulepreload" crossorigin href="/assets/%s">' % name)
    for name, _ in dash_css:
        head_lines.append('  <link rel="preload" as="style" crossorigin href="/assets/%s">' % name)
    head_lines.append('  <link rel="stylesheet" crossorigin href="/custom/%s">' % CSS_NAME)

    body_lines = ['  <script src="/custom/%s"></script>' % JS_NAME]
    return head_lines, body_lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dist', required=True, help='前端 dist 根目录（含 index.html 与 assets/）')
    ap.add_argument('--src', default=os.path.dirname(os.path.abspath(__file__)),
                    help='定制资产所在目录，默认与本脚本同级')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args()

    dist = os.path.abspath(a.dist)
    index_path = os.path.join(dist, 'index.html')
    css_src = os.path.join(a.src, CSS_NAME)
    js_src = os.path.join(a.src, JS_NAME)

    print('=' * 68)
    print(' 前端定制注入器 · %s' % time.strftime('%Y-%m-%d %H:%M:%S'))
    print('=' * 68)
    print(' dist   : %s' % dist)
    print(' assets : %s' % a.src)
    print(' 模式   : %s' % ('DRY-RUN（不写盘）' if a.dry_run else '实际注入'))
    print()

    # ---- 前置校验 ----
    for label, p in (('index.html', index_path), (CSS_NAME, css_src), (JS_NAME, js_src)):
        if not os.path.isfile(p):
            print(' ✗ 缺少 %s : %s' % (label, p))
            return 2
    print(' ✓ 输入齐全  index.html(%d B) %s(%d B) %s(%d B)'
          % (os.path.getsize(index_path), CSS_NAME, os.path.getsize(css_src),
             JS_NAME, os.path.getsize(js_src)))

    html = open(index_path, encoding='utf-8').read()

    # ---- 幂等 ----
    if MARKER in html and not a.force:
        print(' ⚠ index.html 已注入过 /custom/ 引用（md5=%s）。' % md5_of(index_path))
        print('   如需重写请加 --force。未做任何改动。')
        return 0

    vendor, dash_js, dash_css = find_entry_assets(os.path.join(dist, 'assets'))
    print(' ✓ 扫描 assets: echarts vendor=%d Dashboard-js=%d Dashboard-css=%d'
          % (len(vendor), len(dash_js), len(dash_css)))
    for name, size in vendor:
        print('     vendor  %s  %.0f KB' % (name, size / 1024))
    for name, size in dash_js + dash_css:
        print('     chunk   %s  %.0f KB' % (name, size / 1024))
    if not vendor:
        print('     ⚠ 未找到 >500KB 的 vendor chunk，modulepreload 将只覆盖 Dashboard')

    head_lines, body_lines = build_injection(vendor, dash_js, dash_css)

    # ---- 锚点 ----
    head_anchor = '</head>'
    app_anchor = '<div id="app"></div>'
    if head_anchor not in html:
        print(' ✗ index.html 找不到 </head>，无法确定插入点')
        return 2
    body_insert = app_anchor if app_anchor in html else '<body>'

    new_html = html
    to_remove = [
        (r'<script\s+type="importmap"[^>]*>.*?</script>', '死 importmap'),
        (r'<link rel="modulepreload"[^>]*>(?:[ \t]*<!--.*?-->)?', '过期 modulepreload'),
        (r'<link rel="preload"[^>]*>(?:[ \t]*<!--.*?-->)?', '过期 preload'),
        (r'<link rel="stylesheet"[^>]*href="/custom/mc-custom\.css"[^>]*>', '旧 custom css'),
        (r'<script src="/custom/mc-custom\.js"[^>]*>\s*</script>', '旧 custom js'),
    ]
    removed = 0
    for pat, label in to_remove:
        new_html, n = re.subn(r'\s*' + pat, '', new_html, flags=re.S)
        if n:
            print('   · 清除 %s × %d' % (label, n))
        removed += n
    print(' ✓ 清理旧引用 %d 处' % removed)

    # ③ 注入 head（preload + custom css）
    new_html = new_html.replace(head_anchor, '\n'.join(head_lines) + '\n' + head_anchor, 1)
    # ④ 注入 body（custom js，紧跟 app 挂载点之后，与原热修位置一致）
    new_html = new_html.replace(body_insert, body_insert + '\n' + '\n'.join(body_lines), 1)
    print(' ✓ 注入 head %d 行、body %d 行' % (len(head_lines), len(body_lines)))

    # ---- 写盘 ----
    if not a.dry_run:
        bak = index_path + '.orig-' + time.strftime('%Y%m%d-%H%M%S')
        shutil.copy2(index_path, bak)
        print(' ✓ 原文件已备份 → %s' % os.path.basename(bak))

        custom_dst = os.path.join(dist, CUSTOM_DIR)
        os.makedirs(custom_dst, exist_ok=True)
        for name in (CSS_NAME, JS_NAME):
            dst = os.path.join(custom_dst, name)
            shutil.copy2(os.path.join(a.src, name), dst)
            print(' ✓ 部署 %s → %s/%s  (md5=%s)' % (name, CUSTOM_DIR, name, md5_of(dst)))

        open(index_path, 'w', encoding='utf-8', newline='').write(new_html)
        print(' ✓ 已写入 index.html  (md5=%s, %d B)' % (md5_of(index_path), os.path.getsize(index_path)))
    else:
        print(' · DRY-RUN：以上改动未落盘')

    print()
    if a.dry_run:
        print(' DRY-RUN：以上改动未落盘。确认无误后去掉 --dry-run 再跑一次。')
    else:
        print(' 完成。请用浏览器验证三个入口：侧栏导航 / 账号管理 / 资源管理。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
