import argparse
import os
import queue
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps

os.system('chcp 65001 >nul')

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".jpe", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif"}
FONT_CANDIDATES = [r"C:\Windows\Fonts\simhei.ttf", "/System/Library/Fonts/STHeiti Medium.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"]

ANCHOR_MAP = {
    "0": "top-left",     "1": "top-center",     "2": "top-right",
    "3": "middle-left",  "4": "center",         "5": "middle-right",
    "6": "bottom-left",  "7": "bottom-center",  "8": "bottom-right"
}

@dataclass
class Settings:
    text: str
    font_path: str
    font_size_ratio: float
    text_color: tuple
    stroke_color: tuple
    stroke_width_ratio: float
    box_color: tuple
    box_padding_ratio: float
    box_radius_ratio: float
    anchor: str
    offset: tuple
    mask_enabled: bool
    mask_rect: tuple
    mask_color: tuple
    logo_path: str | None
    logo_scale_ratio: float
    logo_opacity: int

def parse_color(s: str) -> tuple:
    vals = [int(round(float(p.strip()))) for p in s.replace(";", ",").split(",") if p.strip()]
    if len(vals) == 3: vals.append(255)
    return tuple(max(0, min(255, v)) for v in vals)

def find_cjk_font() -> str:
    for p in FONT_CANDIDATES:
        if os.path.isfile(p): return p
    return ""


def parse_anchor(anchor: str) -> tuple:
    anchor_str = str(anchor).strip()
    # 如果传入的是 0~8 的数字，直接转换
    if anchor_str in ANCHOR_MAP:
        anchor_str = ANCHOR_MAP[anchor_str]
    else:
        print("错误:位置信息不是有效数字!")
        sys.exit(1)
    
    # 原来的字符串解析逻辑（兼容之前的写法）
    parts = anchor_str.replace("_", "-").lower().split("-")
    h = "left" if "left" in parts else "right" if "right" in parts else "center"
    v = "top" if "top" in parts else "bottom" if "bottom" in parts else "middle"
    return h, v

def compute_pos(anchor: str, w: float, h: float, W: int, H: int, off_x: float, off_y: float) -> tuple:
    h_align, v_align = parse_anchor(anchor)
    x = W * off_x if h_align == "left" else (W * (1.0 - off_x) - w) if h_align == "right" else (W - w) / 2.0 + W * off_x
    y = H * off_y if v_align == "top" else (H * (1.0 - off_y) - h) if v_align == "bottom" else (H - h) / 2.0 + H * off_y
    return x, y

def extract_exif_without_gps(im: Image.Image) -> bytes | None:
    try:
        ex = im.getexif()
        if not ex: return None
        ex.pop(0x8825, None) # GPS
        ex.pop(0x0112, None) # Orientation
        return ex.tobytes()
    except Exception: return None

def draw_watermark(img: Image.Image, st: Settings) -> Image.Image:
    W, H = img.size
    img = img.convert("RGBA")
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    # 1. Mask Leica address area
    if st.mask_enabled:
        mx0, my0, mx1, my1 = st.mask_rect
        d.rectangle([W * mx0, H * my0, W * mx1, H * my1], fill=st.mask_color)

    # 2. Measure text
    font = None
    text_w = text_h = 0
    stroke_px = 0
    text_bbox = (0, 0, 0, 0)
    if st.text:
        font_px = max(8, int(round(H * st.font_size_ratio)))
        try: font = ImageFont.truetype(st.font_path, font_px)
        except Exception as e: raise RuntimeError(f"Font load fail: {st.font_path} ({e})")
        stroke_px = int(round(H * st.stroke_width_ratio))
        scratch = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
        text_bbox = scratch.textbbox((0, 0), st.text, font=font, stroke_width=stroke_px)
        text_w = text_bbox[2] - text_bbox[0]
        text_h = text_bbox[3] - text_bbox[1]

    # 3. Load Logo
    logo_img = None
    logo_w = logo_h = 0
    if st.logo_path and os.path.isfile(st.logo_path):
        logo_img = Image.open(st.logo_path).convert("RGBA")
        target_h = int(H * st.logo_scale_ratio)
        ratio = target_h / logo_img.height
        logo_w, logo_h = int(logo_img.width * ratio), target_h
        logo_img = logo_img.resize((logo_w, logo_h), Image.Resampling.LANCZOS)
        alpha = logo_img.split()[3]
        alpha = alpha.point(lambda p: p * (st.logo_opacity / 255.0))
        logo_img.putalpha(alpha)

    # 4. Compute box dimensions
    pad = int(round(H * st.box_padding_ratio))
    gap = int(H * 0.01)
    total_content_w = logo_w + (gap if logo_w > 0 and text_w > 0 else 0) + text_w
    max_content_h = max(logo_h, text_h)
    box_w = total_content_w + 2 * pad
    box_h = max_content_h + 2 * pad

    bx, by = compute_pos(st.anchor, box_w, box_h, W, H, st.offset[0], st.offset[1])

    # 5. Draw box
    if st.box_color[3] > 0:
        radius = max(0, min(int(H * st.box_radius_ratio), int(box_h // 2)))
        rect = [bx, by, bx + box_w, by + box_h]
        try: d.rounded_rectangle(rect, radius=radius, fill=st.box_color)
        except TypeError: d.rectangle(rect, fill=st.box_color)

    # 6. Draw Logo
    current_x = bx + pad
    if logo_img and logo_h > 0:
        logo_y = by + pad + (max_content_h - logo_h) // 2
        layer.alpha_composite(logo_img, (int(current_x), int(logo_y)))
        current_x += logo_w + gap

    # 7. Draw Text
    if st.text and font:
        text_y = by + pad + (max_content_h - text_h) // 2
        d.text(
            (current_x - text_bbox[0], text_y - text_bbox[1]),
            st.text, font=font,
            fill=st.text_color,
            stroke_width=stroke_px,
            stroke_fill=st.stroke_color if stroke_px > 0 else None
        )

    return Image.alpha_composite(img, layer).convert("RGB")

def process_one(src: Path, dst: Path, st: Settings, inplace: bool):
    with Image.open(src) as im0:
        exif = extract_exif_without_gps(im0)
        im = ImageOps.exif_transpose(im0)
        im = draw_watermark(im, st)
        if inplace:
            tmp = src.parent / f".{src.stem}.wmtmp{src.suffix}"
            im.save(tmp, quality=100, subsampling=0, exif=exif)
            os.replace(tmp, src)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            im.save(dst, quality=100, subsampling=0, exif=exif)

def worker(q, st, inplace, output_dir, prog, errors):
    while True:
        try: src = q.get_nowait()
        except queue.Empty: return
        try:
            if inplace:
                dst = src
            elif output_dir:
                dst = Path(output_dir) / src.name
            else:
                dst = src.parent / f"{src.stem}_watermarked{src.suffix}"
            
            process_one(src, dst, st, inplace)
            prog[0] += 1
            sys.stderr.write(f"\r正在运行: {prog[0]}/{prog[1]}"); sys.stderr.flush()
        except Exception as e:
            errors.append((str(src), str(e)))
        finally:
            q.task_done()

def main():
    p = argparse.ArgumentParser()
    p.add_argument("input")
    p.add_argument("-o", "--output", default=None, help="Output directory")
    p.add_argument("--inplace", action="store_true")
    p.add_argument("-j", "--threads", type=int, default=4)
    p.add_argument("-t", "--text", default="")
    p.add_argument("--font", default=find_cjk_font())
    p.add_argument("--font-size", type=float, default=0.025)
    p.add_argument("--text-color", default="255,255,255,255")
    p.add_argument("--stroke-color", default="0,110,220,255")
    p.add_argument("--stroke-width", type=float, default=0.0015)
    p.add_argument("--box-color", default="0,0,0,110")
    p.add_argument("--box-padding", type=float, default=0.008)
    p.add_argument("--box-radius", type=float, default=0.005)
    p.add_argument("--anchor", default="bottom-left")
    p.add_argument("--offset", default="0.03,0.03")
    p.add_argument("--mask", default="0.0,0.862,0.66,0.982")
    p.add_argument("--mask-color", default="0,0,0,255")
    p.add_argument("--logo", default=None, help="Logo image path")
    p.add_argument("--logo-scale", type=float, default=0.04, help="Logo height ratio")
    p.add_argument("--logo-opacity", type=int, default=200, help="Logo opacity 0-255")
    
    args = p.parse_args()

    off = [float(x) for x in args.offset.split(",")]
    mask_vals = [float(x) for x in args.mask.split(",")]

    st = Settings(
        text=args.text, font_path=args.font, font_size_ratio=args.font_size,
        text_color=parse_color(args.text_color), stroke_color=parse_color(args.stroke_color),
        stroke_width_ratio=args.stroke_width, box_color=parse_color(args.box_color),
        box_padding_ratio=args.box_padding, box_radius_ratio=args.box_radius,
        anchor=args.anchor, offset=(off[0], off[1]),
        mask_enabled=True, mask_rect=(mask_vals[0], mask_vals[1], mask_vals[2], mask_vals[3]),
        mask_color=parse_color(args.mask_color),
        logo_path=args.logo, logo_scale_ratio=args.logo_scale,
        logo_opacity=args.logo_opacity
    )

    input_root = Path(args.input).resolve()
    files = [f for f in input_root.rglob("*") if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS]
    if not files: print("错误:未找到图像!"); return

    q = queue.Queue()
    for f in files: q.put(f)
    errors = []
    prog = [0, len(files)]
    
    threads = [threading.Thread(target=worker, args=(q, st, args.inplace, args.output, prog, errors), daemon=True) for _ in range(min(args.threads, len(files)))]
    for t in threads: t.start()
    q.join()
    for t in threads: t.join()
    sys.stderr.write("\n")
    print(f"完成! 成功: {len(files) - len(errors)}, 失败: {len(errors)}")
    for e in errors: print(f"错误: {e[0]} -> {e[1]}")

if __name__ == "__main__":
    main()
