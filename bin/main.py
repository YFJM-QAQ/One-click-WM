import argparse
import os
import queue
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps
import traceback

os.system('chcp 65001 >nul')

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".jpe", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif"} #类型选择
FONT_CANDIDATES = [r"C:\Windows\Fonts\simhei.ttf"] #黑体

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
    quality:int
    subsampling:int

def parse_color(s: str) -> tuple:  #解析颜色
    vals = [int(round(float(p.strip()))) for p in s.replace(";", ",").split(",") if p.strip()] #分割加切割
    if len(vals) == 3: vals.append(255) #默认不透明(透明度255)
    return tuple(max(0, min(255, v)) for v in vals) #将颜色限制在0~255，返回数组

def find_cjk_font() -> str: #查找字体
    for p in FONT_CANDIDATES: #遍历所有路径
        if os.path.isfile(p): return p #如果有，返回
    return "" #都没找到为空

"""
ANCHOR_MAP = {
    "0": "top-left",     "1": "top-center",     "2": "top-right",
    "10": "middle-left",  "11": "center",         "21": "middle-right",
    "20": "bottom-left",  "12": "bottom-center",  "22": "bottom-right"
}#映射表
"""

def compute_pos(anchor: str, w: float, h: float, W: int, H: int, off_x: float, off_y: float) -> tuple:
    anchor = str(anchor).strip()
    if len(anchor) != 2 or anchor[0] not in '012' or anchor[1] not in '012':
        print(f"警告: 位置点 '{anchor}' 格式错误，应为两位数字(0,1,2)，如 '20'。已默认右下处理。")
        anchor = "22" 
    if anchor[1]=='0':
        x = W * off_x
    elif anchor[1]=='1':
        x = W * (1.0 - off_x) - w
    else:
        x = (W - w) / 2.0 + W * off_x
    if anchor[0]=='0':
        y = H * off_y
    elif anchor[0]=='1':
        y = H * (1.0 - off_y) - h
    else: 
        y = (H - h) / 2.0 + H * off_y
    return x, y

def extract_exif_without_gps(im: Image.Image) -> bytes | None: #处理EXIF
    try:
        ex = im.getexif()
        if not ex: return b''
        ex.pop(0x8825, None) # GPS
        ex.pop(0x0112, None) # Orientation
        return ex.tobytes()
    except Exception: return b'' 

def draw_watermark(img: Image.Image, st: Settings) -> Image.Image:
    W, H = img.size
    img = img.convert("RGBA")
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    # 1. 遮罩
    if st.mask_enabled:
        mx0, my0, mx1, my1 = st.mask_rect
        d.rectangle([W * mx0, H * my0, W * mx1, H * my1], fill=st.mask_color)

    # 2. 量文字
    font = None
    text_w = text_h = 0
    stroke_px = 0
    text_bbox = (0, 0, 0, 0)
    if st.text:
        font_px = max(8, int(round(H * st.font_size_ratio)))
        font = None
        if st.font_path:
            try:
                font = ImageFont.truetype(st.font_path, font_px)
            except Exception:
                pass

        # 如果指定字体加载失败，回退默认字体
        if font is None:
            font = ImageFont.load_default()

        # 【重点修复】无论字体加载成功还是失败，都需要统一计算文字尺寸！
        # 这些代码必须和上面的 if font is None 平级，不能缩进在它内部
        stroke_px = int(round(H * st.stroke_width_ratio))
        scratch = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
        text_bbox = scratch.textbbox((0, 0), st.text, font=font, stroke_width=stroke_px)
        text_w = text_bbox[2] - text_bbox[0]
        text_h = text_bbox[3] - text_bbox[1]

    # 3. Logo
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

    # 4. 算框
    pad = int(round(H * st.box_padding_ratio))
    gap = int(H * 0.01)
    total_content_w = logo_w + (gap if logo_w > 0 and text_w > 0 else 0) + text_w
    max_content_h = max(logo_h, text_h)
    box_w = total_content_w + 2 * pad
    box_h = max_content_h + 2 * pad

    bx, by = compute_pos(st.anchor, box_w, box_h, W, H, st.offset[0], st.offset[1])

    # 5. 画框
    if st.box_color[3] > 0:
        radius = max(0, min(int(H * st.box_radius_ratio), int(box_h // 2)))
        rect = [bx, by, bx + box_w, by + box_h]
        try:
            d.rounded_rectangle(rect, radius=radius, fill=st.box_color)
        except TypeError:
            d.rectangle(rect, fill=st.box_color)

    # 6. 画 Logo
    current_x = bx + pad
    if logo_img and logo_h > 0:
        logo_y = by + pad + (max_content_h - logo_h) // 2
        layer.alpha_composite(logo_img, (int(current_x), int(logo_y)))
        current_x += logo_w + gap

    # 7. 画文字
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

def process_one(src: Path, dst: Path, st: Settings, inplace: bool) -> tuple[str, str]:
    # 1. 尝试打开图片（致命错误，直接返回）
    try:
        im0 = Image.open(src)
    except Exception as e:
        err_msg = str(e)
        if "cannot identify image file" in err_msg:
            return "error", f"无法识别文件详情：{err_msg}"
        elif "Truncated File Read" in err_msg or "image file is truncated" in err_msg:
            return "error", f"图片数据不完整。详情：{err_msg}"
        else:
            return "error", f"打开图片失败。详情：{err_msg}"

    # 2. 图片已成功打开，进入处理流程
    warnings = []
    
    try:
        with im0:
            # 尝试获取 EXIF
            try:
                exif = extract_exif_without_gps(im0)
            except Exception as e:
                exif = b''
                warnings.append(f"图片EXIF读取异常({e})")

            # 尝试处理方向
            try:
                im = ImageOps.exif_transpose(im0)
            except Exception as e:
                im = im0.convert("RGBA")
                warnings.append(f"图片方向信息损坏，已按原方向处理({e})")

            # 绘制水印
            im = draw_watermark(im, st)

            # 3. 保存环节单独捕获，以便区分是打开错误还是写入错误
            try:
                if inplace:
                    tmp = src.parent / f".{src.stem}.wmtmp{src.suffix}"
                    im.save(tmp, quality=st.quality, subsampling=st.subsampling, exif=exif)
                    os.replace(tmp, src)
                else:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    im.save(dst, quality=st.quality, subsampling=st.subsampling, exif=exif)
            except PermissionError:
                return "error", f"保存失败：文件被占用或没有写入权限。详情：{e}"
            except OSError as e:
                return "error", f"保存失败：磁盘空间可能已满，或路径无效。详情：{e}"
            except Exception as e:
                return "error", f"保存时发生未知错误。详情：{e}"

            # 4. 统一处理警告返回
            if warnings:
                # 将列表中的多个警告用分号连接，方便阅读
                warning_msg = "； ".join(warnings)
                return "warning", warning_msg
            
            return "success", ""

    except Exception as e:
        return "error", f"处理图片时发生未知异常。详情：{e}"
    
def worker(q, st, inplace, output_dir, results, cond):
    while True:
        try:
            idx, src = q.get_nowait()
        except queue.Empty:
            return
        try:
            if inplace:
                dst = src
            elif output_dir:
                dst = Path(output_dir) / src.name
            else:
                dst = src.parent / f"{src.stem}_watermarked{src.suffix}"

            status, msg = process_one(src, dst, st, inplace)

            with cond:
                results[idx] = (status, msg, str(dst.parent))
                cond.notify_all()

        except Exception as e:
            with cond:
                results[idx] = ("error", f"worker内部异常: {e}", str(src.parent))
                cond.notify_all()
        finally:
            q.task_done()


def worker_wrapper(q, st, inplace, output_dir, results, cond, worker_excs):
    name = threading.current_thread().name
    try:
        worker(q, st, inplace, output_dir, results, cond)
    except Exception:
        with cond:
            worker_excs.append((name, traceback.format_exc()))
            cond.notify_all()


def print_worker(total, results, cond, workers_ref, main_exc, worker_excs):
    succ = warn = err = 0
    i = 1
    while i <= total:
        with cond:
            while i not in results:
                cond.wait(timeout=1.0)

                if main_exc:
                    for j in range(i, total + 1):
                        if j not in results:
                            results[j] = ("error", "主线程异常，任务未完成", "")
                    break

                if workers_ref and not any(t.is_alive() for t in workers_ref):
                    for j in range(i, total + 1):
                        if j not in results:
                            results[j] = ("error", "worker异常终止，未返回结果", "")
                    break

            if i not in results:
                break
            status, msg, parent = results[i]

        if status == "success":
            succ += 1
            print(f"第{i}张：成功：已导出到 {parent}")
        elif status == "warning":
            warn += 1
            print(f"第{i}张：警告：{msg}")
        else:
            err += 1
            print(f"第{i}张：错误：{msg}")
        i += 1

    print(f"\n成功：{succ}|警告：{warn}|失败：{err}")

    if main_exc:
        print("\n===== 主线程异常信息 =====")
        for tb in main_exc:
            print(tb)

    if worker_excs:
        print("\n===== worker 线程异常信息 =====")
        for name, tb in worker_excs:
            print(f"[{name}]")
            print(tb)


# ---------- 7.4 主流程 ----------
def main():
    results = {}
    cond = threading.Condition()
    workers_ref = []
    main_exc = []
    worker_excs = []
    printer = None

    try:
        p = argparse.ArgumentParser()
        p.add_argument("input")
        p.add_argument("-o", "--output", default=None)
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
        p.add_argument("--anchor", default="22")
        p.add_argument("--offset", default="0.03,0.03")
        p.add_argument("--mask", default="0.0,0.862,0.66,0.982")
        p.add_argument("--mask-color", default="0,0,0,255")
        p.add_argument("--logo", default=None)
        p.add_argument("--logo-scale", type=float, default=0.04)
        p.add_argument("--logo-opacity", type=int, default=200)
        p.add_argument("--quality",tpye=int,default=95)
        p.add_argument("--subsampling",tpye=int,default=0)

        args = p.parse_args()

        off = [float(x) for x in args.offset.split(",")]
        mask_vals = [float(x) for x in args.mask.split(",")]

        st = Settings(
            text=args.text, font_path=args.font, font_size_ratio=args.font_size,
            text_color=parse_color(args.text_color), stroke_color=parse_color(args.stroke_color),
            stroke_width_ratio=args.stroke_width, box_color=parse_color(args.box_color),
            box_padding_ratio=args.box_padding, box_radius_ratio=args.box_radius,
            anchor=args.anchor, offset=(off[0], off[1]),
            mask_enabled=True,
            mask_rect=(mask_vals[0], mask_vals[1], mask_vals[2], mask_vals[3]),
            mask_color=parse_color(args.mask_color),
            logo_path=args.logo, logo_scale_ratio=args.logo_scale,
            logo_opacity=args.logo_opacity
        )

        if st.text:  # 如果需要写文字
            try:
                ImageFont.truetype(st.font_path, 20)
            except Exception:
                print(f"警告：找不到指定的字体 '{st.font_path}'")
                fallback_font = find_cjk_font()  # 直接调用你写好的查找函数
                if fallback_font:
                    print(f"已自动切换为系统默认中文字体: {fallback_font}")
                    st.font_path = fallback_font  # 永久修改全局配置，后续图片都不会再报错
                else:
                    print("警告：系统也没有找到中文字体，将使用 Pillow 内置字体（中文可能显示为方块）")
                    st.font_path = ""  # 留空，让后面的绘图逻辑走默认兜底

        input_root = Path(args.input).resolve()
        files = [f for f in input_root.rglob("*")
                 if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS]
        if not files:
            print("错误:未找到图像!")
            return

        q = queue.Queue()
        for idx, f in enumerate(files, start=1):
            q.put((idx, f))

        printer = threading.Thread(
            target=print_worker,
            args=(len(files), results, cond, workers_ref, main_exc, worker_excs),
            daemon=True, name="Printer"
        )
        printer.start()

        threads = [
            threading.Thread(
                target=worker_wrapper,
                args=(q, st, args.inplace, args.output, results, cond, worker_excs),
                daemon=True, name=f"Worker-{i+1}"
            )
            for i in range(min(args.threads, len(files)))
        ]
        workers_ref.extend(threads)
        for t in threads:
            t.start()

        q.join()
        for t in threads:
            t.join()

    except Exception:
        with cond:
            main_exc.append(traceback.format_exc())
            cond.notify_all()

    finally:
        if printer is not None:
            printer.join()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        input("按回车退出...")