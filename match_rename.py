# -*- coding: utf-8 -*-
"""
图片识别结果与订单数据表匹配，并把图片重命名为「来源平台+原流水号」。

匹配规则：
  1. 用识别结果中的订单号码（自动去空格）精确匹配表中的「订单编号」；
  2. 匹配不到时改用手机号匹配（查看表中的「收货人电话」和「备注」，
     备注里通常含有 138****5678 形式的掩码手机号）；
  3. 命中多行时，依次用下单时间、预计送达时间段缩小范围；
  4. 仍有多行时，默认选择下单时间最晚的一条；
  5. 匹配成功后，把图片重命名为 月日_平台前两字_原流水号（如 8.16_美团_16.jpg）。

用法：
  python match_rename.py <订单数据表.xls|xlsx> <图片/结果JSON/文件夹...> [--dry-run]

说明：
  - 传入「识别结果_*.json」可复用已有的 OCR 结果，不必重新识别；
  - 参数顺序不限，脚本会自动区分订单表和图片/JSON；
  - --dry-run 只打印匹配和重命名计划，不真正改名；
  - 目标文件名已存在时自动追加 _2、_3…，不会覆盖；
  - 处理完会生成 results/重命名记录_时间戳.csv。
"""

import argparse
import csv
import html
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import ocr_extract as ocr  # 会自动加载 vendor 目录中的依赖

SS_NS = "urn:schemas-microsoft-com:office:spreadsheet"
TABLE_EXTS = {".xls", ".xlsx"}


# ================= 读取订单表 =================
def _fix_xml_entities(text):
    return re.sub(
        r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9a-fA-F]+;)[a-zA-Z]+;",
        lambda m: html.unescape(m.group(0)),
        text,
    )


def _spreadsheetml_rows(root):
    rows = []
    ws = root.find(f"{{{SS_NS}}}Worksheet")
    table = ws.find(f"{{{SS_NS}}}Table")
    for row in table.findall(f"{{{SS_NS}}}Row"):
        vals = []
        idx = 0
        for cell in row.findall(f"{{{SS_NS}}}Cell"):
            i = int(cell.get(f"{{{SS_NS}}}Index", str(idx + 1)))
            while idx + 1 < i:
                vals.append("")
                idx += 1
            data = cell.find(f"{{{SS_NS}}}Data")
            vals.append(data.text if data is not None and data.text is not None else "")
            idx += 1
        rows.append(vals)
    return rows


def load_orders(path):
    """读取订单表，返回 [{列名: 值}, ...]。支持 .xls/.xlsx/XML 型 xls。"""
    path = Path(path)
    raw = path.read_bytes()
    if raw.lstrip().startswith(b"<?xml"):
        text = _fix_xml_entities(raw.decode("utf-8", errors="replace"))
        matrix = _spreadsheetml_rows(ET.fromstring(text))
    else:
        import pandas as pd

        engine = "xlrd" if path.suffix.lower() == ".xls" else "openpyxl"
        df = pd.read_excel(path, dtype=object, engine=engine)
        matrix = [list(df.columns)] + df.astype(str).values.tolist()
    header = [str(h).strip() for h in matrix[0]]
    orders = []
    for row in matrix[1:]:
        rec = {
            h: (str(row[i]).strip() if i < len(row) and row[i] else "")
            for i, h in enumerate(header)
        }
        if any(rec.values()):
            orders.append(rec)
    return orders


# ================= 匹配规则 =================
def norm_serial(s):
    return re.sub(r"\s+", "", str(s or ""))


def order_number_match(rec, img_order):
    o = norm_serial(img_order)
    if not o:
        return False
    return norm_serial(rec.get("订单编号", "")) == o


def phone_base(rec):
    t = re.sub(r"\s+", "", str(rec.get("收货人电话", "") or ""))
    return t.split("_")[0]


def phone_match(rec, img_phone):
    p = re.sub(r"\s+", "", str(img_phone or ""))
    if not p:
        return False
    remark = str(rec.get("备注", "") or "")
    base = phone_base(rec)
    if re.search(r"[*＊xX]", p):  # 掩码手机号：先按备注原文，再按前3后2-4位
        if re.search(rf"(?<!\d){re.escape(p)}(?!\d)", remark):
            return True
        m = re.fullmatch(r"(1[3-9]\d)[*＊xX]{2,6}(\d{2,4})", p)
        if m:
            prefix, suffix = m.group(1), m.group(2)
            if len(base) >= 11 and base.startswith(prefix) and base.endswith(suffix):
                return True
        return False
    # 完整手机号
    if base == p:
        return True
    return bool(re.search(rf"(?<!\d){re.escape(p)}(?!\d)", remark))


def parse_time(s):
    m = re.search(
        r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})日?(?:[ T](\d{1,2}))?(?::(\d{2}))?",
        str(s or ""),
    )
    if not m:
        return None
    return tuple(int(x) if x is not None else 0 for x in m.groups())


def time_match(row_time, img_time):
    rt = parse_time(row_time)
    it = parse_time(img_time)
    if not rt or not it:
        return False
    for a, b in zip(rt, it):
        if b != 0 and a != b:
            return False
    return True


def parse_clock_range(s):
    times = re.findall(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)", str(s or ""))
    if not times:
        return None
    values = [int(h) * 60 + int(m) for h, m in times[-2:]]
    start = values[0]
    end = values[-1]
    if end < start:
        end += 24 * 60
    return start, end


def delivery_range_score(row_delivery, img_delivery):
    """返回两个送达时间段的 (间隔分钟, 中点差分钟)，越小越接近。"""
    row_range = parse_clock_range(row_delivery)
    img_range = parse_clock_range(img_delivery)
    if not row_range or not img_range:
        return None
    best = None
    for shift in (-24 * 60, 0, 24 * 60):
        rs, re_ = row_range[0] + shift, row_range[1] + shift
        is_, ie = img_range
        gap = max(is_ - re_, rs - ie, 0)
        center_gap = abs((rs + re_) - (is_ + ie)) / 2
        score = (gap, center_gap)
        if best is None or score < best:
            best = score
    return best


def latest_order_hit(hits):
    dated = [(parse_time(r.get("下单日期", "")), i, r) for i, r in enumerate(hits)]
    valid = [item for item in dated if item[0]]
    if not valid:
        return hits[:1]
    return [max(valid, key=lambda item: (item[0], -item[1]))[2]]


def resolve_multiple_hits(hits, fields, method):
    """按下单时间、预计送达、最晚下单时间依次消除多行命中。"""
    candidates = hits
    img_order_time = fields.get("下单时间", "")
    if img_order_time:
        time_hits = [
            r for r in candidates
            if time_match(r.get("下单日期", ""), img_order_time)
        ]
        if time_hits:
            candidates = time_hits
            method += "+下单时间"
            if len(candidates) == 1:
                return method, candidates

    img_delivery = fields.get("预计送达", "")
    if img_delivery and len(candidates) > 1:
        scored = []
        for row in candidates:
            score = delivery_range_score(row.get("期望送达", ""), img_delivery)
            if score is not None:
                scored.append((score, row))
        if scored:
            best_gap = min(score[0] for score, _ in scored)
            best_center = min(
                score[1] for score, _ in scored if score[0] <= best_gap + 10
            )
            if best_gap <= 30:
                delivery_hits = [
                    row for score, row in scored
                    if score[0] <= best_gap + 10 and score[1] <= best_center + 15
                ]
                if delivery_hits:
                    candidates = delivery_hits
                    method += "+预计送达"
                    if len(candidates) == 1:
                        return method, candidates

    if len(candidates) > 1:
        candidates = latest_order_hit(candidates)
        method += "+最晚下单时间"
    return method, candidates


def match_row(orders, fields):
    """返回 (匹配方式, [命中行...])。命中唯一即匹配成功。"""
    img_order = norm_serial(fields.get("订单号", ""))
    if img_order:
        hits = [r for r in orders if order_number_match(r, img_order)]
        if len(hits) == 1:
            return "订单号", hits
        if len(hits) > 1:
            return resolve_multiple_hits(hits, fields, "订单号")
    phone = re.sub(r"\s+", "", str(fields.get("手机号", "") or ""))
    if phone:
        hits = [r for r in orders if phone_match(r, phone)]
        if len(hits) == 1:
            return "手机号", hits
        if len(hits) > 1:
            return resolve_multiple_hits(hits, fields, "手机号")
    return "未匹配", []


# ================= 重命名 =================
def safe_new_path(path, row, used):
    plat = re.sub(r'[\\/:*?"<>|]', "", str(row.get("来源平台", "") or "未知平台"))
    plat = plat.strip() or "未知平台"
    serial = norm_serial(row.get("原流水号", "")) or norm_serial(row.get("流水号", ""))
    parts = []
    t = parse_time(row.get("下单日期", ""))
    if t and t[1] and t[2]:
        parts.append(f"{t[1]}.{t[2]}")
    parts.append(plat[:2])
    if serial:
        parts.append(serial)
    base = "_".join(parts)
    ext = path.suffix or ".jpg"
    target = path.with_name(base + ext)
    n = 2
    while target.exists() or target in used:
        target = path.with_name(f"{base}_{n}{ext}")
        n += 1
    used.add(target)
    return target


def collect_items(inputs):
    items = []
    seen = set()
    for p in inputs:
        p = Path(p)
        if p.suffix.lower() == ".json":
            try:
                data = json.loads(Path(p).read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"!! 无法读取结果JSON {p}: {exc}", file=sys.stderr)
                continue
            for item in data:
                img = Path(item.get("file", ""))
                if not img.exists():
                    img = p.parent / img
                item = dict(item)
                item["file"] = str(img)
                item.setdefault("fields", {})
                item.setdefault("text", "")
                key = str(img.resolve())
                if key not in seen:
                    seen.add(key)
                    items.append(item)
        elif p.suffix.lower() in ocr.IMG_EXTS:
            key = str(p.resolve())
            if key in seen:
                continue
            seen.add(key)
            items.append({"file": str(p), "fields": {}, "text": ""})
        elif p.is_dir():
            for img in ocr.collect_images([p]):
                key = str(img.resolve())
                if key in seen:
                    continue
                seen.add(key)
                items.append({"file": str(img), "fields": {}, "text": ""})
        else:
            print(f"!! 跳过无法识别的输入: {p}", file=sys.stderr)
    return items


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="图片识别结果匹配订单表并重命名")
    ap.add_argument(
        "inputs", nargs="+",
        help="订单数据表(.xls/.xlsx) + 图片/结果JSON/文件夹（顺序不限）",
    )
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不真正重命名")
    args = ap.parse_args()

    tables = [Path(p) for p in args.inputs if Path(p).suffix.lower() in TABLE_EXTS]
    if not tables:
        sys.exit("请至少提供一个订单数据表（.xls 或 .xlsx）。")
    table_path = tables[0]
    others = [p for p in args.inputs if Path(p).suffix.lower() not in TABLE_EXTS]
    if not others:
        sys.exit("请同时提供图片、结果JSON或文件夹。")

    print(f"读取订单表: {table_path}")
    orders = load_orders(table_path)
    print(f"订单表共 {len(orders)} 行。")

    items = collect_items(others)
    if not items:
        sys.exit("没有可处理的图片或结果。")

    used = set()
    log_rows = []
    for item in items:
        img = Path(item["file"])
        if not item["fields"].get("订单号") and not item["text"]:
            try:
                lines = ocr.ocr_texts(img)
            except Exception as exc:
                print(f"!! {img} OCR 失败: {exc}", file=sys.stderr)
                continue
            item["fields"] = ocr.extract_fields(lines)
            item["text"] = "\n".join(lines)
        fields = item["fields"]
        method, hits = match_row(orders, fields)
        print("-" * 60)
        print(f"图片: {img.name}")
        print(
            f"  订单号={fields.get('订单号','') or '(空)'} "
            f"手机号={fields.get('手机号','') or '(空)'} "
            f"下单时间={fields.get('下单时间','') or '(空)'}"
        )
        if len(hits) != 1:
            print(f"  匹配失败[{method}]，命中 {len(hits)} 行，跳过重命名。")
            for h in hits[:5]:
                print(
                    f"    - 平台={h.get('来源平台','')} 原流水号={h.get('原流水号','')} "
                    f"订单编号={h.get('订单编号','')} 下单日期={h.get('下单日期','')}"
                )
            log_rows.append({
                "原路径": str(img), "新路径": "", "匹配方式": method,
                "订单号": fields.get("订单号", ""), "手机号": fields.get("手机号", ""),
                "下单时间": fields.get("下单时间", ""),
                "来源平台": "", "原流水号": "",
            })
            continue
        row = hits[0]
        target = safe_new_path(img, row, used)
        log_rows.append({
            "原路径": str(img), "新路径": str(target), "匹配方式": method,
            "订单号": fields.get("订单号", ""), "手机号": fields.get("手机号", ""),
            "下单时间": fields.get("下单时间", ""),
            "来源平台": row.get("来源平台", ""), "原流水号": row.get("原流水号", ""),
        })
        if args.dry_run:
            print(f"  [计划] {img.name} -> {target.name}  (按{method}匹配)")
        else:
            try:
                img.rename(target)
                print(f"  已重命名: {img.name} -> {target.name}  (按{method}匹配)")
            except Exception as exc:
                print(f"  !! 重命名失败: {exc}", file=sys.stderr)

    done = [r for r in log_rows if r["新路径"]]
    print(f"\n匹配成功 {len(done)} / 共 {len(items)} 张。")
    if log_rows and not args.dry_run:
        out_dir = Path(__file__).resolve().parent / "results"
        out_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = out_dir / f"重命名记录_{stamp}.csv"
        with open(log_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "原路径", "新路径", "匹配方式", "订单号", "手机号",
                    "下单时间", "来源平台", "原流水号",
                ],
            )
            writer.writeheader()
            writer.writerows(log_rows)
        print(f"重命名记录已保存: {log_path}")


if __name__ == "__main__":
    main()
