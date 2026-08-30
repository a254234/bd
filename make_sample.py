# -*- coding: utf-8 -*-
"""生成一张带订单字段的测试图片，用于验证 ocr_extract.py"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
]


def load_font(size):
    for fp in FONT_CANDIDATES:
        if Path(fp).exists():
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                continue
    return ImageFont.load_default()


def render(lines, out):
    img = Image.new("RGB", (1100, 640), "white")
    draw = ImageDraw.Draw(img)
    font = load_font(52)
    small = load_font(36)
    draw.text((40, 30), "订单详情", fill="black", font=small)
    y = 110
    for ln in lines:
        draw.text((60, y), ln, fill="black", font=font)
        y += 105
    img.save(out)
    print(f"已生成测试图片: {out}")


def main():
    complete = [
        "订单编号：DD20260816123456",
        "手机号：138****5678",
        "下单时间：2026-08-16 10:30:25",
        "收货人：张三",
        "订单金额：￥299.00",
    ]
    partial = [
        "订单号：P20260816009999",
        "手机号：13912345678",
        "下单时间：2026年8月16日 10:30",
    ]
    base = Path(__file__).parent
    render(complete, base / "sample_order.png")
    render(partial, base / "sample_order_partial.png")


if __name__ == "__main__":
    main()
