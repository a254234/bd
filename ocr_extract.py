# -*- coding: utf-8 -*-
"""
图片信息 OCR 识别与字段提取脚本

功能：
  1. OCR 识别图片中的文字（支持中文 / 英文 / 数字）
  2. 提取常见字段：手机号（支持 138****5678 掩码）、订单号、下单时间、
     收货人、订单金额等；图片中没有的字段输出为空字符串
  3. 支持单张图片或整个文件夹批量识别，结果可输出 CSV / JSON

依赖安装（清华镜像）：
  python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

用法：
  python ocr_extract.py D:/pics/order.png
  python ocr_extract.py D:/pics -o result.csv --json result.json
"""

import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

# 优先使用同目录 vendor 中的依赖（受限环境下可用 --target 安装到此处）
_VENDOR_DIR = Path(__file__).resolve().parent / "vendor"
if _VENDOR_DIR.is_dir() and str(_VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(_VENDOR_DIR))

IMG_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}

# ================= 字段配置（按需增删改） =================
# 每个字段: (字段名, 关键词列表, [取值正则, ...], 无关键词时全文兜底的正则列表)
# 取值正则按顺序尝试，建议更精确的放前面；兜底列表为 None 表示不兜底。
PHONE_PATTERNS = [
    r"(?<!\d)1[3-9]\d\s*[\*＊xX]{2,6}\s*\d{2,4}(?!\d)",  # 138****5678 / 137****262
    r"(?<!\d)1[3-9]\d[\s-]?\d{4}[\s-]?\d{4}(?!\d)",  # 13812345678
]
TIME_PATTERNS = [
    r"\d{4}[-/年.]\d{1,2}[-/月.]\d{1,2}日?\s*[T]?\s*\d{1,2}:\d{2}(?::\d{2})?",
    r"\d{4}[-/年.]\d{1,2}[-/月.]\d{1,2}日?",
]
PAYMENT_PATTERNS = [
    r"(?:在线支付|货到付款|微信支付|支付宝|余额支付|云闪付|银行卡支付|美团支付|"
    r"数字人民币|花呗|白条|他人代付|到店支付|现金支付|扫码支付|银联支付|分期付款|"
    r"美团月付|微信|支付宝|银行卡|现金|到付)",
]

FIELDS = [
    ("手机号", ["手机号码", "手机号", "联系电话", "联系方式", "电话", "手机"],
     PHONE_PATTERNS, PHONE_PATTERNS),
    ("订单号", ["订单编号", "订单号码", "订单号", "单号", "order no", "order id"],
     [r"[A-Za-z0-9][A-Za-z0-9\-_]{5,40}"], None),
    ("下单时间", ["下单时间", "订单时间", "创建时间", "支付时间", "交易时间", "时间"],
     TIME_PATTERNS, TIME_PATTERNS),
    (
        "收货人",
        ["收货人", "收件人", "联系人"],
        [
            r"[\u4e00-\u9fa5A-Za-z·]{2,20}",
            r"[\u4e00-\u9fa5A-Za-z·*]{2,20}",
            r"[\u4e00-\u9fa5·*]{2,8}(?=\((先生|女士|男|女)\))",
        ],
        [r"[\u4e00-\u9fa5·*]{2,8}(?=\((先生|女士|男|女)\))"],
    ),
    (
        "订单金额",
        ["订单金额", "实付金额", "实付款", "支付金额", "合计", "总价", "实付"],
        [r"(?:¥|￥|CNY|RMB)?\s*\d{1,10}(?:\.\d{1,2})?"],
        [r"(?:¥|￥)\s*\d{1,10}(?:\.\d{1,2})?"],
    ),
    (
        "配送地址",
        ["配送地址", "收货地址", "地址"],
        [r"[\u4e00-\u9fa5A-Za-z0-9\-_.·（）()#]{2,60}"],
        None,
    ),
    ("商品件数", ["共"], [r"\d+\s*件"], None),
    ("支付方式", ["支付方式"], PAYMENT_PATTERNS, None),
    (
        "预计送达",
        ["预计送达"],
        [r"\d{1,2}:\d{2}[-~—至]\d{1,2}:\d{2}", r"\d{1,2}:\d{2}"],
        [r"\d{1,2}:\d{2}[-~—至]\d{1,2}:\d{2}"],
    ),
]

ALL_KEYWORDS = [kw for f in FIELDS for kw in f[1]]

# ================= OCR 引擎 =================
_ENGINE = None
_ENGINE_NAME = None


def load_engine():
    global _ENGINE, _ENGINE_NAME
    if _ENGINE is not None:
        return _ENGINE
    try:
        from rapidocr import RapidOCR

        _ENGINE = RapidOCR(params={"Global.log_level": "error"})
        _ENGINE_NAME = "rapidocr"
    except Exception:
        try:
            from rapidocr_onnxruntime import RapidOCR

            _ENGINE = RapidOCR()
            _ENGINE_NAME = "rapidocr"
        except Exception:
            try:
                from paddleocr import PaddleOCR

                _ENGINE = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
                _ENGINE_NAME = "paddleocr"
            except Exception:
                sys.exit(
                    "未找到 OCR 引擎。请先安装依赖（清华镜像）：\n"
                    "  python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple rapidocr"
                )
    return _ENGINE


def _parse_rapid(raw):
    if hasattr(raw, "txts"):
        return [str(t).strip() for t in raw.txts if t and str(t).strip()]
    if isinstance(raw, (list, tuple)) and raw and isinstance(raw[0], (list, tuple)):
        raw = raw[0]
    lines = []
    for item in raw or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2 and isinstance(item[1], str):
            lines.append(item[1].strip())
    return [ln for ln in lines if ln]


def _parse_paddle(raw):
    pages = raw if isinstance(raw, list) else [raw]
    for page in pages:
        if hasattr(page, "get"):
            return [str(t).strip() for t in page.get("rec_texts", []) if t and str(t).strip()]
    page = pages[0] if pages else None
    lines = []
    if isinstance(page, list):
        for item in page:
            if (
                isinstance(item, (list, tuple))
                and len(item) == 2
                and isinstance(item[1], (list, tuple))
                and item[1]
            ):
                lines.append(str(item[1][0]).strip())
    return [ln for ln in lines if ln]


def ocr_texts(image_path):
    engine = load_engine()
    try:
        raw = engine(str(image_path))
    except Exception as exc:
        raise RuntimeError(
            f"OCR 识别失败: {exc}\n"
            "若是模型下载失败，请检查网络，或改用 PaddleOCR（见 README.md）。"
        ) from exc
    if _ENGINE_NAME == "paddleocr":
        return _parse_paddle(raw)
    return _parse_rapid(raw)


# ================= 字段提取 =================
def _norm(text):
    return re.sub(r"[\s:：]", "", text)


def keyword_parts(line, keyword):
    """返回 (关键词之后内容, 关键词之前内容)；未命中返回 (None, None)。"""
    m = re.search(re.escape(keyword) + r"[\s:：\-—]*", line, flags=re.I)
    if m:
        return line[m.end():].strip(), line[:m.start()].strip()
    nk = _norm(keyword)
    nl = _norm(line)
    idx = nl.find(nk)
    if idx >= 0:
        return nl[idx + len(nk):], nl[:idx]
    return None, None


def first_match(patterns, text):
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m
    return None


def is_label_line(line):
    return any(keyword_parts(line, kw) != (None, None) for kw in ALL_KEYWORDS)


ADDR_MARKERS = (
    "省", "市", "区", "县", "镇", "乡", "路", "街", "道", "巷", "弄", "号",
    "楼", "栋", "室", "村", "组", "中心", "大厦", "广场", "园区", "公司",
    "集团", "酒店", "学校", "医院",
)
ADDR_NOISE = re.compile(
    r"号码|保护|隐私|订单|时间|支付|送达|配送|费用|收藏|收起|取消|权益|基金|公益|服务"
)


def guess_address(lines):
    """无“地址”标签时，按地址特征词猜测地址（最多合并 3 行）。"""
    for i, line in enumerate(lines):
        if is_label_line(line) or ADDR_NOISE.search(line) or len(line) < 6:
            continue
        if not any(m in line for m in ADDR_MARKERS):
            continue
        parts = [line.strip("<>…· ")]
        j = i + 1
        while j < len(lines) and len(parts) < 3:
            nxt = lines[j].strip()
            if (
                is_label_line(nxt)
                or ADDR_NOISE.search(nxt)
                or len(nxt) < 3
                or not any(m in nxt for m in ADDR_MARKERS)
            ):
                break
            parts.append(nxt.strip("<>…· "))
            j += 1
        return "".join(parts)
    return ""


def extract_fields(lines):
    lines = [ln.strip() for ln in lines if ln and ln.strip()]
    full_text = "\n".join(lines)
    result = {}
    for name, keywords, patterns, global_patterns in FIELDS:
        value = ""
        for i, line in enumerate(lines):
            for kw in keywords:
                rest, prefix = keyword_parts(line, kw)
                if rest is None:
                    continue
                candidates = [rest]
                if name == "预计送达":
                    candidates.append(prefix)
                if not first_match(patterns, rest) and i + 1 < len(lines):
                    nxt = lines[i + 1].strip()
                    if not is_label_line(nxt):
                        candidates.append(nxt)
                for cand in candidates:
                    if name == "订单号":
                        cand = re.sub(r"\s+", "", cand)
                    m = first_match(patterns, cand)
                    if m:
                        value = m.group(0)
                        break
                if value:
                    break
            if value:
                break
        if not value and global_patterns:
            for pat in global_patterns:
                m = re.search(pat, full_text)
                if m:
                    value = m.group(0)
                    break
        if name == "配送地址" and not value:
            value = guess_address(lines)
        if name == "手机号":
            value = re.sub(r"\s+", "", value)
        if name == "订单号":
            value = re.sub(r"\s+", "", value)
        if name == "下单时间" and value:
            value = re.sub(r"(\d{1,2}日?)(\d{1,2}:\d{2})", r"\1 \2", value)
            if not re.search(r"\d{1,2}:\d{2}", value):
                for i, line in enumerate(lines):
                    if value in line and i + 1 < len(lines):
                        nxt = lines[i + 1].strip()
                        if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", nxt):
                            value = f"{value} {nxt}"
                        break
        result[name] = value.strip()
    return result


# ================= 输入输出 =================
def collect_images(paths):
    images = []
    for p in paths:
        p = Path(p)
        if p.is_file():
            if p.suffix.lower() in IMG_EXTS:
                images.append(p)
        elif p.is_dir():
            for f in sorted(p.rglob("*")):
                if any(part in {"vendor", "tmp", "node_modules", ".git"} for part in f.parts):
                    continue
                if f.suffix.lower() in IMG_EXTS:
                    images.append(f)
        else:
            print(f"跳过不存在的路径: {p}", file=sys.stderr)
    return sorted(set(images))


def _print_item(item, field_names, verbose):
    print("-" * 60)
    print(f"图片: {item['file']}")
    missing = []
    for name in field_names:
        value = item["fields"].get(name, "")
        print(f"  {name:<4}: {value if value else '(空)'}")
        if not value:
            missing.append(name)
    if (missing or verbose) and item["text"]:
        print("  [OCR 识别原文]")
        for ln in item["text"].splitlines():
            print(f"    {ln}")


def write_csv(path, results, field_names):
    header = ["文件"] + field_names + ["识别原文"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for item in results:
            writer.writerow(
                [item["file"]]
                + [item["fields"].get(n, "") for n in field_names]
                + [item["text"]]
            )
    print(f"CSV 已保存: {path}")


def write_json(path, results):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"JSON 已保存: {path}")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="图片 OCR 识别并提取订单类字段")
    ap.add_argument("paths", nargs="+", help="图片文件或文件夹路径")
    ap.add_argument("-o", "--csv", help="输出 CSV 文件路径")
    ap.add_argument("--json", help="输出 JSON 文件路径")
    ap.add_argument("-v", "--verbose", action="store_true", help="总是打印 OCR 原文")
    args = ap.parse_args()

    images = collect_images(args.paths)
    if not images:
        sys.exit("未找到可识别的图片（支持: " + ", ".join(sorted(IMG_EXTS)) + "）")

    load_engine()
    field_names = [f[0] for f in FIELDS]
    results = []
    for img in images:
        try:
            lines = ocr_texts(img)
        except Exception as exc:
            print(f"!! {img} 识别失败: {exc}", file=sys.stderr)
            continue
        if not lines:
            print(f"!! {img} 未识别到文字", file=sys.stderr)
        fields = extract_fields(lines)
        item = {"file": str(img), "fields": fields, "text": "\n".join(lines)}
        results.append(item)
        _print_item(item, field_names, args.verbose)

    if results:
        if args.csv:
            write_csv(Path(args.csv), results, field_names)
        if args.json:
            write_json(Path(args.json), results)
        if not args.csv and not args.json:
            out_dir = Path(__file__).resolve().parent / "results"
            out_dir.mkdir(exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            write_csv(out_dir / f"识别结果_{stamp}.csv", results, field_names)
            write_json(out_dir / f"识别结果_{stamp}.json", results)
    print(f"\n共处理 {len(results)} 张图片。")


if __name__ == "__main__":
    main()
