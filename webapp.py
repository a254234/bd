# -*- coding: utf-8 -*-
'''
本地网页版：上传订单数据表和多张订单图片，自动完成
OCR 识别 -> 字段提取 -> 订单匹配 -> 重命名，最后一键下载 ZIP。

用法：
  双击 启动网站.bat
  或 python webapp.py [--port 5000] [--no-browser]
'''

import argparse
import csv
import hashlib
import io
import json
import re
import shutil
import socket
import sys
import threading
import uuid
import webbrowser
import zipfile
from datetime import datetime
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent
_VENDOR_DIR = _BASE_DIR / 'vendor'
if _VENDOR_DIR.is_dir() and str(_VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(_VENDOR_DIR))

import ocr_extract as ocr
import match_rename as mr
from flask import Flask, jsonify, request, send_file

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024 * 1024  # 上传上限 1GB
TMP_ROOT = _BASE_DIR / 'tmp_uploads'
TMP_LIFE_HOURS = 24

_PAGE_FILE = _BASE_DIR / 'page.html'
INDEX_HTML = (
    _PAGE_FILE.read_text(encoding='utf-8')
    if _PAGE_FILE.exists()
    else '<h1>页面文件 page.html 缺失</h1>'
)


# ================= 工具函数 =================
def safe_stem(name):
    name = Path(str(name or '')).name
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', '_', name)
    return name.strip(' .') or '未命名'


def unique_zip_name(zip_used, name):
    name = safe_stem(name)
    if name not in zip_used:
        zip_used.add(name)
        return name
    stem, dot, ext = name.rpartition('.')
    if not dot:
        stem, ext = name, ''
    n = 2
    while True:
        cand = f'{stem}_{n}.{ext}' if ext else f'{stem}_{n}'
        if cand not in zip_used:
            zip_used.add(cand)
            return cand
        n += 1


def filter_orders_by_platform(orders, platform, other):
    '''按网页选择的平台缩小订单表范围。'''
    platform = (platform or '').strip()
    if not platform:
        return orders
    if platform == '其他':
        key = (other or '').strip()
        if key:
            return [r for r in orders if key in str(r.get('来源平台', '')).strip()]
        return [
            r for r in orders
            if str(r.get('来源平台', '')).strip() not in {'美团外卖', '淘宝闪购'}
        ]
    return [r for r in orders if str(r.get('来源平台', '')).strip() == platform]


def _order_key(rec):
    order_no = mr.norm_serial(rec.get('订单编号', ''))
    if order_no:
        return ('order', order_no)
    phone = mr.phone_base(rec)
    date = str(rec.get('下单日期', '') or '')
    if phone or date:
        return ('phone', phone, date)
    return (
        'row',
        str(rec.get('来源平台', '') or ''),
        mr.norm_serial(rec.get('原流水号', '')) or mr.norm_serial(rec.get('流水号', '')),
    )


def dup_note(r):
    if not r.get('dup_of'):
        return ''
    kind = r.get('dup_kind') or '图片'
    return f'重复{kind}：与 {r["dup_of"]} 相同，已跳过'


def cleanup_tmp():
    try:
        if not TMP_ROOT.exists():
            return
        now = datetime.now().timestamp()
        for d in TMP_ROOT.iterdir():
            if d.is_dir() and now - d.stat().st_mtime > TMP_LIFE_HOURS * 3600:
                shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass


def lan_ips():
    '''返回本机可用的局域网 IPv4 地址列表（供其他设备访问）。'''
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith('127.'):
                ips.append(ip)
        finally:
            s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith('127.'):
                ips.append(ip)
    except Exception:
        pass
    return ips


def _recognition_csv(rows):
    field_names = [f[0] for f in ocr.FIELDS]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['文件'] + field_names + ['识别原文', '备注'])
    for r in rows:
        fields = r.get('fields') or {}
        writer.writerow(
            [r.get('file', '')]
            + [fields.get(n, '') for n in field_names]
            + [r.get('text', ''), r.get('error', '') or dup_note(r)]
        )
    return buf.getvalue()


def _rename_csv(rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        '原文件名', '新文件名', '匹配方式', '命中行数', '手机号', '订单号',
        '下单时间', '来源平台', '原流水号', '备注',
    ])
    for r in rows:
        fields = r.get('fields') or {}
        writer.writerow([
            r.get('file', ''), r.get('new_name', ''), r.get('method', ''),
            r.get('hit_count', 0), fields.get('手机号', ''),
            fields.get('订单号', ''), fields.get('下单时间', ''),
            r.get('platform', ''), r.get('serial', ''),
            r.get('error', '') or dup_note(r),
        ])
    return buf.getvalue()


# ================= 页面与接口 =================
@app.route('/')
def index():
    return INDEX_HTML


@app.route('/api/process', methods=['POST'])
def api_process():
    table_file = request.files.get('table')
    image_files = request.files.getlist('images')
    if table_file is None or not table_file.filename:
        return jsonify({'ok': False, 'error': '请上传订单数据表（.xls / .xlsx）。'})
    if not image_files:
        return jsonify({'ok': False, 'error': '请上传至少一张图片。'})

    table_name = safe_stem(table_file.filename)
    if not table_name.lower().endswith(('.xls', '.xlsx')):
        return jsonify({'ok': False, 'error': '订单数据表只支持 .xls 或 .xlsx 文件。'})

    sid = datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6]
    sdir = TMP_ROOT / sid
    try:
        sdir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return jsonify({'ok': False, 'error': f'无法创建临时目录: {exc}'})

    table_path = sdir / table_name
    try:
        table_file.save(table_path)
        orders = mr.load_orders(table_path)
    except Exception as exc:
        return jsonify({'ok': False, 'error': f'订单数据表读取失败: {exc}'})
    if not orders:
        return jsonify({'ok': False, 'error': '订单数据表里没有数据行。'})

    platform = (request.form.get('platform') or '').strip()
    platform_other = (request.form.get('platform_other') or '').strip()
    orders = filter_orders_by_platform(orders, platform, platform_other)
    if not orders:
        return jsonify({'ok': False, 'error': '按所选平台筛选后，订单表里没有数据。'})

    try:
        ocr.load_engine()
    except BaseException as exc:
        return jsonify({'ok': False, 'error': f'OCR 引擎加载失败: {exc}'})

    used = set()
    seen_hashes = {}
    seen_orders = {}
    rows = []
    for idx, img_file in enumerate(image_files):
        disp = safe_stem(img_file.filename)
        row = {
            'file': disp, 'stored': '', 'new_name': '', 'method': '',
            'hit_count': 0, 'platform': '', 'serial': '', 'order_date': '',
            'error': '', 'dup_of': '', 'dup_kind': '', 'fields': {}, 'text': '',
        }
        if not disp.lower().endswith(tuple(ocr.IMG_EXTS)):
            row['error'] = '不支持的图片格式'
            rows.append(row)
            continue
        stored = f'{idx:03d}_{disp}'
        img_path = sdir / stored
        try:
            img_file.save(img_path)
        except Exception as exc:
            row['error'] = f'保存失败: {exc}'
            rows.append(row)
            continue
        row['stored'] = stored
        try:
            digest = hashlib.md5(img_path.read_bytes()).hexdigest()
        except Exception as exc:
            row['error'] = f'读取图片失败: {exc}'
            rows.append(row)
            continue
        if digest in seen_hashes:
            row['dup_of'] = seen_hashes[digest]
            row['dup_kind'] = '图片'
            rows.append(row)
            continue
        seen_hashes[digest] = disp
        try:
            lines = ocr.ocr_texts(img_path)
        except Exception as exc:
            row['error'] = f'OCR 失败: {exc}'
            rows.append(row)
            continue
        fields = ocr.extract_fields(lines)
        row['fields'] = fields
        row['text'] = '\n'.join(lines)
        try:
            method, hits = mr.match_row(orders, fields)
        except Exception as exc:
            row['error'] = f'匹配出错: {exc}'
            rows.append(row)
            continue
        row['method'] = method
        row['hit_count'] = len(hits)
        if len(hits) == 1:
            hit = hits[0]
            row['platform'] = hit.get('来源平台', '')
            row['serial'] = hit.get('原流水号', '') or hit.get('流水号', '')
            row['order_date'] = hit.get('下单日期', '')
            key = _order_key(hit)
            if key in seen_orders:
                row['dup_of'] = seen_orders[key]
                row['dup_kind'] = '订单'
            else:
                seen_orders[key] = disp
                try:
                    target = mr.safe_new_path(img_path, hit, used)
                    row['new_name'] = target.name
                except Exception as exc:
                    row['error'] = f'生成新文件名失败: {exc}'
        rows.append(row)

    matched = sum(1 for r in rows if r['new_name'])
    dups = sum(1 for r in rows if r.get('dup_of'))
    state = {
        'table': table_name,
        'platform': platform or '不限',
        'platform_other': platform_other,
        'rows': rows,
    }
    try:
        with open(sdir / 'state.json', 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        return jsonify({'ok': False, 'error': f'保存会话失败: {exc}'})
    return jsonify({
        'ok': True, 'session': sid, 'total': len(rows),
        'matched': matched, 'duplicates': dups, 'rows': rows,
    })


@app.route('/api/download/<sid>')
def api_download(sid):
    if not re.fullmatch(r'[0-9A-Za-z_\-]+', sid):
        return jsonify({'ok': False, 'error': '非法会话标识。'}), 404
    state_path = TMP_ROOT / sid / 'state.json'
    if not state_path.exists():
        return jsonify({'ok': False, 'error': '会话不存在或已过期，请重新处理。'}), 404
    try:
        state = json.loads(state_path.read_text(encoding='utf-8'))
    except Exception:
        return jsonify({'ok': False, 'error': '会话数据损坏，请重新处理。'}), 500

    rows = state.get('rows', [])
    buf = io.BytesIO()
    zip_used = set()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for r in rows:
            if r.get('dup_of'):
                continue
            src = TMP_ROOT / sid / r.get('stored', '')
            if not src.exists():
                continue
            name = unique_zip_name(zip_used, r.get('new_name') or r.get('file') or src.name)
            folder = '未匹配'
            if r.get('new_name'):
                t = mr.parse_time(r.get('order_date', ''))
                folder = (
                    f'{t[0]}-{t[1]:02d}-{t[2]:02d}'
                    if t and t[1] and t[2]
                    else '未知日期'
                )
            zf.write(src, f'{folder}/{name}')
        zf.writestr('识别结果.csv', _recognition_csv(rows).encode('utf-8-sig'))
        zf.writestr('重命名记录.csv', _rename_csv(rows).encode('utf-8-sig'))
    buf.seek(0)
    return send_file(
        buf,
        mimetype='application/zip',
        as_attachment=True,
        download_name=f'处理结果_{sid}.zip',
    )


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    ap = argparse.ArgumentParser(description='订单图片识别与重命名 本地网站')
    ap.add_argument('--port', type=int, default=5000, help='网站端口，默认 5000')
    ap.add_argument('--no-browser', action='store_true', help='启动时不自动打开浏览器')
    args = ap.parse_args()

    cleanup_tmp()
    url = f'http://127.0.0.1:{args.port}/'
    print('=' * 56)
    print('订单图片识别与重命名 - 本地网站')
    print(f'本机浏览器访问: {url}')
    for ip in lan_ips():
        print(f'局域网其他设备访问: http://{ip}:{args.port}/')
    print('若其他设备无法访问，请在防火墙中允许 Python（或放行该端口）。')
    print('处理完成后关闭本窗口即可停止网站。')
    print('=' * 56)
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host='0.0.0.0', port=args.port, debug=False, threaded=True)


if __name__ == '__main__':
    main()
