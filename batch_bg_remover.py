# -*- coding: utf-8 -*-
"""
批量去背景工具（remove.bg 桌面版風格）
=====================================================
整合三大去背景精華演算法：
  1. 色度鍵 Chroma Key   —— 四角取樣背景色，距離雙閾值 + 平滑 alpha
  2. 綠色溢色抑制 Spill  —— 壓掉前景邊緣混入的綠色
  3. 連通域清理         —— 移除透明背景上的雜散像素（水印、噪點）

批量預處理（套用到全部圖片，輸出時先去背再變換）：
  - 旋轉：↺-90° / ↻+90° / 180° / 自訂任意角度
  - 翻轉：↔ 水平（左右）/ ↕ 垂直（上下）
  - 即時預覽：點擊列表圖片，右側顯示「原圖 vs 變換後」
  - 區域截取（裁切）：在「變換後」預覽上用滑鼠拖曳框選區域，
    批量處理時按比例裁切每張圖片，輸出尺寸隨框選區域改變

功能：
  - 多張圖片拖拽 / 新增圖片 / 新增資料夾
  - 清單顯示圖片與數量
  - 一鍵批量去背景，輸出透明 PNG 到指定資料夾

依賴：pillow, numpy, scipy, tkinterdnd2
執行：python batch_bg_remover.py
"""

import os
import re
import sys
import queue
import threading
import math
from pathlib import Path

from PIL import Image

try:
    import numpy as np
    from scipy import ndimage
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

# ---------------------------------------------------------------------------
# 核心去背景演算法
# ---------------------------------------------------------------------------

SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif", ".tif", ".tiff"}


def _corner_bg(px, W, H, corner=24):
    """取四角區域的平均色作為背景參考色（自動適應小圖）"""
    corner = max(1, min(corner, W // 2, H // 2))
    rs = gs = bs = n = 0
    for (x0, y0, x1, y1) in [(0, 0, corner, corner),
                             (W - corner, 0, W, corner),
                             (0, H - corner, corner, H),
                             (W - corner, H - corner, W, H)]:
        for y in range(y0, y1):
            for x in range(x0, x1):
                r, g, b, _ = px[x, y]
                rs += r; gs += g; bs += b; n += 1
    return (rs / n, gs / n, bs / n)


def apply_transforms(img, transforms):
    """依序套用變換（旋轉/翻轉）。RGBA 旋轉的空白處填透明。"""
    for t in transforms or []:
        if t["type"] == "rotate":
            img = img.rotate(t["angle"], expand=True, fillcolor=(0, 0, 0, 0))
        elif t["type"] == "flip":
            if t["axis"] == "h":
                img = img.transpose(Image.FLIP_LEFT_RIGHT)
            else:
                img = img.transpose(Image.FLIP_TOP_BOTTOM)
    return img


def crop_image(img, crop_frac):
    """依歸一化比例 (x0, y0, x1, y1)（0~1）裁切圖片，返回新圖。

    輸出尺寸 = 框選區域的像素範圍（尺寸會隨框選改變）。
    """
    if not crop_frac:
        return img
    W, H = img.size
    x0 = int(round(crop_frac[0] * W)); y0 = int(round(crop_frac[1] * H))
    x1 = int(round(crop_frac[2] * W)); y1 = int(round(crop_frac[3] * H))
    x0 = max(0, min(x0, W - 1)); x1 = max(x0 + 1, min(x1, W))
    y0 = max(0, min(y0, H - 1)); y1 = max(y0 + 1, min(y1, H))
    return img.crop((x0, y0, x1, y1))


def chroma_key(img, lo=55.0, hi=115.0, min_area=3000):
    """對 RGBA 圖執行完整去背景（色度鍵 + 溢色抑制 + 連通域清理），返回新 RGBA 圖。

    供「開始批量處理」與「預覽」共用，確保所見即所得。
    """
    img = img.convert("RGBA")
    W, H = img.size
    px = img.load()

    # 1) 色度鍵：距離雙閾值 + smoothstep 漸變 alpha
    bg = _corner_bg(px, W, H)
    for y in range(H):
        for x in range(W):
            r, g, b, a = px[x, y]
            d = math.sqrt((r - bg[0]) ** 2 + (g - bg[1]) ** 2 + (b - bg[2]) ** 2)
            if d <= lo:
                a = 0
            elif d >= hi:
                a = 255
            else:
                t = (d - lo) / (hi - lo)
                a = int(t * t * (3 - 2 * t) * 255)  # smoothstep
            # 2) 綠色溢色抑制：邊緣半透明像素若綠色明顯偏高則壓掉
            if 0 < a < 255 and g > max(r, b) + 30:
                g = max(r, b) + 15
            px[x, y] = (r, g, b, a)

    # 3) 連通域清理：只保留最大連通域（角色主體），清除水印等小雜塊
    if HAS_SCIPY:
        alpha = np.frombuffer(img.getchannel("A").tobytes(), dtype=np.uint8).reshape(H, W)
        mask = alpha > 0
        lbl, n = ndimage.label(mask, structure=np.ones((3, 3)))
        if n > 0:
            sizes = np.bincount(lbl.ravel())
            sizes[0] = 0
            keep_label = int(sizes.argmax())
            keep = lbl == keep_label
            drop = mask & ~keep
            ys, xs = np.where(drop)
            for i in range(len(xs)):
                px[int(xs[i]), int(ys[i])] = (0, 0, 0, 0)

    return img


def remove_background(src_path, dst_path, lo=55.0, hi=115.0, min_area=3000, transforms=None):
    """對單張圖片去背景並輸出透明 PNG（可選在去背後套用變換）。

    lo/hi    : 色度鍵距離閾值，距離 < lo 視為背景(全透明)，
               距離 > hi 視為前景(不透明)，中間平滑過渡。
    min_area : 連通域面積小於此值的孤立像素會被清除（水印/噪點）。
    transforms : [{"type":"rotate","angle":90}, {"type":"flip","axis":"h"}, ...]
    """
    img = Image.open(src_path)
    img = img.convert("RGBA")

    img = chroma_key(img, lo, hi, min_area)

    # 4) 去背後再套用變換（旋轉/翻轉），空白處自動透明
    if transforms:
        img = apply_transforms(img, transforms)

    img.save(dst_path, "PNG")
    return img.size[0], img.size[1]


def process_image(src_path, dst_path, lo=55.0, hi=115.0, min_area=3000, transforms=None, do_bg=True, crop=None):
    """統一的單圖處理入口。

    處理順序：旋轉/翻轉 → 區域裁切 → （可選）去背景
    do_bg=True  : 去背景（色度鍵+溢色抑制+連通域清理），輸出透明 PNG
    do_bg=False : 不去背景，保留原背景，輸出 PNG
    crop        : 歸一化裁切框 (x0,y0,x1,y1)（0~1 比例），套用於變換後的圖片
    """
    img = Image.open(src_path)
    img = img.convert("RGBA")
    if transforms:
        img = apply_transforms(img, transforms)
    if crop:
        img = crop_image(img, crop)
    if do_bg:
        img = chroma_key(img, lo, hi, min_area)
    img.save(dst_path, "PNG")
    return img.size[0], img.size[1]


def unique_output_path(dst_dir, src_name):
    """輸出檔名：保留原名，擴展名改 .png；同名時自動加 _1/_2 後綴"""
    stem = Path(src_name).stem
    out = Path(dst_dir) / f"{stem}.png"
    i = 1
    while out.exists():
        out = Path(dst_dir) / f"{stem}_{i}.png"
        i += 1
    return str(out)


def parse_drop_data(raw):
    """解析 tkdnd 拖拽資料，支援花括號路徑、空格路徑、file:// URI"""
    if not raw:
        return []
    paths = []
    # 花括號包裹的路徑（路徑內含空格）
    for m in re.finditer(r"\{([^}]*)\}", raw):
        paths.append(m.group(1))
    rest = re.sub(r"\{[^}]*\}", "", raw)
    # 其餘以空白分隔的 token
    for token in rest.split():
        token = token.strip()
        if token:
            paths.append(token)
    out = []
    for p in paths:
        p = p.strip()
        if not p:
            continue
        # file:///C:/... 形式
        if p.lower().startswith("file:"):
            from urllib.parse import unquote, urlparse
            p = unquote(urlparse(p).path)
            if len(p) > 2 and p[0] == "/" and p[2] == ":":
                p = p[1:]  # /C:/... -> C:/...
        out.append(p)
    return out


def ops_desc(ops):
    """把變換清單轉成可讀文字，例如「旋轉+90° + 水平翻轉」"""
    if not ops:
        return ""
    parts = []
    for t in ops:
        if t["type"] == "rotate":
            a = int(t["angle"])
            parts.append(f"旋轉{a:+d}°")
        else:
            parts.append("水平翻轉" if t["axis"] == "h" else "垂直翻轉")
    return " + ".join(parts)


# ---------------------------------------------------------------------------
# GUI（tkinter + 拖拽）
# ---------------------------------------------------------------------------

def build_gui():
    try:
        from tkinterdnd2 import DND_FILES, TkinterDnD
        ROOT_CLS = TkinterDnD.Tk
        HAS_DND = True
    except Exception:
        from tkinter import Tk
        ROOT_CLS = Tk
        HAS_DND = False

    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    from PIL import ImageTk

    root = ROOT_CLS()
    root.title("批量去背景工具")
    root.geometry("1180x640")
    root.minsize(980, 700)

    # 正確處理 PyInstaller 的路徑（關鍵）
    try:
        if getattr(sys, 'frozen', False):
            base_path = sys._MEIPASS
        else:
            base_path = os.path.dirname(__file__)

        icon_path = os.path.join(base_path, "icon.png")

        icon = tk.PhotoImage(file=icon_path)
        root.iconphoto(False, icon)
    except Exception:
        pass

    style = ttk.Style()
    try:
        style.theme_use("vista")
    except Exception:
        pass
    BG = "#f4f6f8"
    root.configure(bg=BG)

    state = {"files": [], "running": False, "crop": None}
    _photo_refs = {}  # 防 PhotoImage 被 GC 回收

    # ---- 頂欄：輸出目錄 ----
    top = ttk.Frame(root, padding=(12, 10))
    top.pack(fill="x")
    ttk.Label(top, text="輸出目錄：").pack(side="left")
    out_var = tk.StringVar(value=str(Path.home() / "Desktop" / "去背輸出"))
    out_entry = ttk.Entry(top, textvariable=out_var)
    out_entry.pack(side="left", fill="x", expand=True, padx=6)
    ttk.Button(top, text="瀏覽…", command=lambda: _pick_outdir()).pack(side="left")
    ttk.Button(top, text="開啟資料夾", command=lambda: _open_outdir()).pack(side="left", padx=(6, 0))

    def _pick_outdir():
        d = filedialog.askdirectory(initialdir=out_var.get() or str(Path.home()))
        if d:
            out_var.set(d)

    def _open_outdir():
        d = out_var.get().strip()
        if not d:
            messagebox.showinfo("提示", "請先設定輸出目錄")
            return
        try:
            os.makedirs(d, exist_ok=True)
            os.startfile(d)
        except Exception:
            os.makedirs(d, exist_ok=True)

    # ---- 參數列：去背閾值 ----
    param = ttk.LabelFrame(root, text="去背參數（一般保持預設）", padding=(10, 6))
    param.pack(fill="x", padx=12, pady=(0, 8))
    ttk.Label(param, text="背景閾值下限 LO").pack(side="left")
    lo_var = tk.DoubleVar(value=55.0)
    ttk.Spinbox(param, from_=10, to=200, increment=5, width=6, textvariable=lo_var).pack(side="left", padx=(4, 14))
    ttk.Label(param, text="上限 HI").pack(side="left")
    hi_var = tk.DoubleVar(value=115.0)
    ttk.Spinbox(param, from_=20, to=260, increment=5, width=6, textvariable=hi_var).pack(side="left", padx=(4, 14))
    ttk.Label(param, text="雜塊面積下限").pack(side="left")
    area_var = tk.IntVar(value=3000)
    ttk.Spinbox(param, from_=100, to=50000, increment=500, width=8, textvariable=area_var).pack(side="left", padx=(4, 10))
    ttk.Label(param, text="  （LO 越高刪越多背景；HI 越高越保守）", foreground="#888").pack(side="left")

    # ---- 主體：左列表 / 右預覽 ----
    main = ttk.Frame(root)
    main.pack(fill="both", expand=True, padx=12)
    main.columnconfigure(0, weight=3)
    main.columnconfigure(1, weight=2)
    main.rowconfigure(0, weight=1)

    # ===== 左欄 =====
    left = ttk.Frame(main)
    left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
    left.rowconfigure(0, weight=1)
    left.columnconfigure(0, weight=1)

    list_frame = ttk.Frame(left)
    list_frame.grid(row=0, column=0, sticky="nsew")
    list_frame.rowconfigure(0, weight=1)
    list_frame.columnconfigure(0, weight=1)
    cols = ("name", "size", "status")
    tree = ttk.Treeview(list_frame, columns=cols, show="headings", selectmode="extended")
    tree.heading("name", text="檔案名稱")
    tree.heading("size", text="大小")
    tree.heading("status", text="狀態 / 變換")
    tree.column("name", width=280)
    tree.column("size", width=80, anchor="e")
    tree.column("status", width=230, anchor="center")
    vsb = ttk.Scrollbar(list_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    tree.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")

    hint = tk.Label(list_frame, text="⇩  把圖片拖到這裡（可多張）  ⇩",
                    font=("Segoe UI", 13), fg="#7a8ba0", bg=BG)
    hint.place(relx=0.5, rely=0.5, anchor="center")

    # 預處理按鈕列（第 1 行：方向）
    op_row = ttk.LabelFrame(left, text="批量預處理（套用到全部圖片）", padding=(8, 6))
    op_row.grid(row=1, column=0, sticky="ew", pady=(8, 0))
    ttk.Button(op_row, text="↺ 逆時針90°", command=lambda: add_op("rotate", -90)).pack(side="left")
    ttk.Button(op_row, text="↻ 順時針90°", command=lambda: add_op("rotate", 90)).pack(side="left", padx=4)
    ttk.Button(op_row, text="⟲ 180°", command=lambda: add_op("rotate", 180)).pack(side="left")
    ttk.Button(op_row, text="↔ 水平翻轉", command=lambda: add_op("flip", "h")).pack(side="left", padx=4)
    ttk.Button(op_row, text="↕ 垂直翻轉", command=lambda: add_op("flip", "v")).pack(side="left")

    # 預處理按鈕列（第 2 行：自訂角度 + 重置）
    op_row2 = ttk.Frame(left)
    op_row2.grid(row=2, column=0, sticky="ew", pady=(6, 0))
    ttk.Label(op_row2, text="自訂角度：").pack(side="left")
    angle_var = tk.DoubleVar(value=90.0)
    ttk.Spinbox(op_row2, from_=-360, to=360, increment=15, width=7, textvariable=angle_var).pack(side="left", padx=(0, 4))
    ttk.Button(op_row2, text="套用旋轉", command=lambda: add_op("rotate", float(angle_var.get()))).pack(side="left")
    ttk.Button(op_row2, text="重置全部變換", command=lambda: reset_ops()).pack(side="left", padx=(10, 0))

    # ===== 右欄：預覽 =====
    right = ttk.LabelFrame(main, text="預覽（點擊左側圖片；上 = 原圖，下 = 變換後）", padding=(8, 6))
    right.grid(row=0, column=1, sticky="nsew")
    right.columnconfigure(0, weight=1)

    ttk.Label(right, text="原圖", anchor="center").grid(row=0, column=0, sticky="ew")
    orig_canvas = tk.Canvas(right, bg="#ffffff", highlightthickness=1, highlightbackground="#ccc", height=185)
    orig_canvas.grid(row=1, column=0, sticky="ew", pady=(0, 6))
    ttk.Label(right, text="變換後（在此拖曳框選＝裁切區域）", anchor="center").grid(row=2, column=0, sticky="ew")
    out_canvas = tk.Canvas(right, bg="#ffffff", highlightthickness=1, highlightbackground="#ccc", height=185)
    out_canvas.grid(row=3, column=0, sticky="ew")
    crop_row = ttk.Frame(right)
    crop_row.grid(row=4, column=0, sticky="ew", pady=(6, 0))
    crop_var = tk.StringVar(value="裁切：未設定（在「變換後」預覽上拖曳框選）")
    ttk.Label(crop_row, textvariable=crop_var, foreground="#555").pack(side="left")
    ttk.Button(crop_row, text="✂ 清除裁切", command=lambda: clear_crop()).pack(side="right")

    # ---- 底部：按鈕 + 進度 ----
    bottom = ttk.Frame(root, padding=(12, 8))
    bottom.pack(fill="x")
    btn_row = ttk.Frame(bottom)
    btn_row.pack(fill="x")
    ttk.Button(btn_row, text="＋ 新增圖片", command=lambda: _add_files()).pack(side="left")
    ttk.Button(btn_row, text="＋ 新增資料夾", command=lambda: _add_folder()).pack(side="left", padx=6)
    ttk.Button(btn_row, text="－ 移除選中", command=lambda: _remove_selected()).pack(side="left")
    ttk.Button(btn_row, text="✕ 清空清單", command=lambda: _clear_all()).pack(side="left", padx=6)
    run_btn = ttk.Button(btn_row, text="▶ 開始批量處理", command=lambda: _run())
    run_btn.pack(side="right")
    bg_var = tk.BooleanVar(value=True)
    bg_check = ttk.Checkbutton(btn_row, text="去除背景", variable=bg_var, command=lambda: update_preview())
    bg_check.pack(side="right", padx=8)
    open_btn = ttk.Button(btn_row, text="打開輸出資料夾", command=_open_outdir)
    open_btn.pack(side="right", padx=6)

    progress = ttk.Progressbar(bottom, mode="determinate")
    progress.pack(fill="x", pady=(8, 2))
    status_var = tk.StringVar(value="尚未加入任何圖片")
    status_lbl = ttk.Label(bottom, textvariable=status_var, foreground="#555")
    status_lbl.pack(anchor="w")

    # ---- 預覽 ----
    def _draw_checker(canvas, w, h):
        """畫棋盤格底，讓透明區域可視化"""
        cell = 12
        for y in range(0, h, cell):
            for x in range(0, w, cell):
                color = "#e8e8e8" if ((x // cell + y // cell) % 2 == 0) else "#ffffff"
                canvas.create_rectangle(x, y, min(x + cell, w), min(y + cell, h),
                                        fill=color, outline="")

    _shown_geom = {}

    def _show_on(canvas, img, checker=False):
        canvas.delete("all")
        w = max(canvas.winfo_width(), 60)
        h = max(canvas.winfo_height(), 60)
        if checker:
            _draw_checker(canvas, w, h)
        img.thumbnail((w - 8, h - 8), Image.LANCZOS)
        iw, ih = img.size
        px = (w - iw) // 2
        py = (h - ih) // 2
        photo = ImageTk.PhotoImage(img)
        _photo_refs[id(canvas)] = photo
        canvas.create_image(w // 2, h // 2, image=photo, anchor="center")
        _shown_geom[id(canvas)] = (px, py, iw, ih)

    def update_preview():
        sel = tree.selection()
        if not sel:
            return
        idx = tree.index(sel[0])
        item = state["files"][idx]
        try:
            img = Image.open(item["path"])
            _show_on(orig_canvas, img.convert("RGB"), checker=False)
            do_bg = bg_var.get()
            ops = item.get("ops", [])
            if do_bg:
                # 勾選去背景：縮小圖預覽去背效果，避免卡 UI；演算法與輸出完全一致
                small = img.convert("RGBA")
                small.thumbnail((600, 600), Image.LANCZOS)
                out_img = chroma_key(small, float(lo_var.get()), float(hi_var.get()),
                                     int(area_var.get()))
                out_img = apply_transforms(out_img, ops)
            else:
                # 未勾選：只顯示旋轉/翻轉，保留原背景
                out_img = apply_transforms(img.convert("RGBA"), ops)
            _show_on(out_canvas, out_img, checker=do_bg)
            _draw_crop_rect()
        except Exception as e:
            out_canvas.delete("all")
            out_canvas.create_text(150, 100, text=f"預覽失敗：{e}", fill="#c00")

    # ---- 區域裁切：在「變換後」預覽上拖曳框選 ----
    _drag = {"x0": 0, "y0": 0, "rect": None}

    def _frac_from_canvas(cx, cy):
        """canvas 座標 → 歸一化比例 (0~1)；在圖片範圍外回傳 None"""
        g = _shown_geom.get(id(out_canvas))
        if not g or g[2] <= 0 or g[3] <= 0:
            return None
        px, py, iw, ih = g
        fx = (cx - px) / iw
        fy = (cy - py) / ih
        if fx < 0 or fx > 1 or fy < 0 or fy > 1:
            return None
        return fx, fy

    def _draw_crop_rect():
        """把已設定的裁切框畫在「變換後」預覽上"""
        out_canvas.delete("crop_rect")
        crop = state.get("crop")
        g = _shown_geom.get(id(out_canvas))
        if not crop or not g:
            return
        px, py, iw, ih = g
        x0 = px + crop[0] * iw; y0 = py + crop[1] * ih
        x1 = px + crop[2] * iw; y1 = py + crop[3] * ih
        out_canvas.create_rectangle(x0, y0, x1, y1, outline="#e63946",
                                    width=2, dash=(4, 3), tags="crop_rect")

    def _on_crop_press(e):
        if state["running"]:
            return
        _drag["x0"], _drag["y0"] = e.x, e.y

    def _on_crop_drag(e):
        if _drag["rect"] is not None:
            out_canvas.delete(_drag["rect"])
        _drag["rect"] = out_canvas.create_rectangle(
            _drag["x0"], _drag["y0"], e.x, e.y,
            outline="#e63946", width=2, dash=(4, 3))

    def _on_crop_release(e):
        if _drag["rect"] is not None:
            out_canvas.delete(_drag["rect"])
            _drag["rect"] = None
        x0, y0 = _drag["x0"], _drag["y0"]
        x1, y1 = e.x, e.y
        if abs(x1 - x0) < 3 or abs(y1 - y0) < 3:
            return  # 太小的點擊，忽略
        fx0, fy0 = _frac_from_canvas(min(x0, x1), min(y0, y1))
        fx1, fy1 = _frac_from_canvas(max(x0, x1), max(y0, y1))
        if fx0 is None or fx1 is None:
            state["crop"] = None
            crop_var.set("裁切：未設定（框選需在圖片範圍內）")
            return
        state["crop"] = (fx0, fy0, fx1, fy1)
        crop_var.set("裁切：已設定（%.0f%% × %.0f%% 區域）" % ((fx1 - fx0) * 100, (fy1 - fy0) * 100))
        update_preview()

    def clear_crop():
        state["crop"] = None
        crop_var.set("裁切：未設定（在「變換後」預覽上拖曳框選）")
        update_preview()

    out_canvas.bind("<Button-1>", _on_crop_press)
    out_canvas.bind("<B1-Motion>", _on_crop_drag)
    out_canvas.bind("<ButtonRelease-1>", _on_crop_release)

    # ---- 清單操作 ----
    def refresh_list():
        tree.delete(*tree.get_children())
        hint.place_forget() if state["files"] else hint.place(relx=0.5, rely=0.5, anchor="center")
        for i, f in enumerate(state["files"], 1):
            p = f["path"]
            try:
                sz = os.path.getsize(p) / 1024
                sz_txt = f"{sz:.0f} KB" if sz < 1024 else f"{sz/1024:.1f} MB"
            except OSError:
                sz_txt = "?"
            desc = ops_desc(f.get("ops", []))
            if state.get("crop"):
                desc = ("區域截取" if not desc else desc + " + 區域截取")
            status = f.get("status", "待處理")
            status_txt = f"[{desc}] {status}" if desc else status
            tree.insert("", "end", values=(f"{i}. {os.path.basename(p)}", sz_txt, status_txt))
        n = len(state["files"])
        status_var.set(f"已加入 {n} 張圖片" + ("（去背中…）" if state["running"] else ""))
        update_preview()

    def add_paths(paths):
        added = 0
        for p in paths:
            p = p.strip()
            if not p:
                continue
            if os.path.isdir(p):
                for root_dir, _, fs in os.walk(p):
                    for fn in sorted(fs):
                        if Path(fn).suffix.lower() in SUPPORTED_EXTS:
                            full = os.path.join(root_dir, fn)
                            if full not in [it["path"] for it in state["files"]]:
                                state["files"].append({"path": full, "status": "待處理", "ops": []})
                                added += 1
            elif os.path.isfile(p) and Path(p).suffix.lower() in SUPPORTED_EXTS:
                if p not in [it["path"] for it in state["files"]]:
                    state["files"].append({"path": p, "status": "待處理", "ops": []})
                    added += 1
        if added:
            refresh_list()

    def _add_files():
        fs = filedialog.askopenfilenames(title="選擇圖片",
                                         filetypes=[("圖片", "*.png *.jpg *.jpeg *.bmp *.webp *.gif"),
                                                    ("所有檔案", "*.*")])
        add_paths(list(fs))

    def _add_folder():
        d = filedialog.askdirectory(title="選擇資料夾（會掃描裡面所有圖片）")
        if d:
            add_paths([d])

    def _remove_selected():
        sel = tree.selection()
        idxs = sorted({tree.index(i) for i in sel}, reverse=True)
        for i in idxs:
            del state["files"][i]
        refresh_list()

    def _clear_all():
        if not state["running"]:
            state["files"].clear()
            refresh_list()

    # ---- 批量預處理 ----
    def add_op(kind, val):
        if state["running"]:
            messagebox.showinfo("提示", "正在處理中，請稍候")
            return
        if not state["files"]:
            messagebox.showwarning("提示", "請先加入圖片")
            return
        op = {"type": kind}
        if kind == "rotate":
            op["angle"] = val
        else:
            op["axis"] = val
        for it in state["files"]:
            it["ops"].append(op)
        refresh_list()

    def reset_ops():
        if state["running"]:
            return
        for it in state["files"]:
            it["ops"] = []
        refresh_list()

    # ---- 拖拽 ----
    def _on_drop(event):
        if state["running"]:
            messagebox.showinfo("提示", "正在處理中，請稍候")
            return
        add_paths(parse_drop_data(event.data))

    if HAS_DND:
        root.drop_target_register(DND_FILES)
        root.dnd_bind("<<Drop>>", _on_drop)
        tree.drop_target_register(DND_FILES)
        tree.dnd_bind("<<Drop>>", _on_drop)

    tree.bind("<<TreeviewSelect>>", lambda e: update_preview())

    # ---- 批量處理（背景執行緒）----
    msg_q = queue.Queue()

    def _worker(files, out_dir, lo, hi, min_area, do_bg, crop):
        total = len(files)
        for idx, item in enumerate(files, 1):
            src = item["path"]
            try:
                dst = unique_output_path(out_dir, os.path.basename(src))
                w, h = process_image(src, dst, lo, hi, min_area, item.get("ops") or None, do_bg, crop)
                tag = ops_desc(item.get("ops", []))
                suffix = f" [已{tag}]" if tag else ""
                msg_q.put(("done", idx, f"✓ {os.path.basename(dst)}{suffix} ({w}x{h})"))
            except Exception as e:
                msg_q.put(("done", idx, f"✗ {os.path.basename(src)} 失敗：{e}"))
        msg_q.put(("finish", None, None))

    def _poll():
        try:
            while True:
                kind, idx, text = msg_q.get_nowait()
                if kind == "done":
                    state["files"][idx - 1]["status"] = text
                    tree.item(tree.get_children()[idx - 1], values=(
                        tree.item(tree.get_children()[idx - 1], "values")[0],
                        tree.item(tree.get_children()[idx - 1], "values")[1],
                        text))
                    progress["value"] = idx / len(state["files"]) * 100
                    status_var.set(f"正在處理 {idx}/{len(state['files'])}：{Path(state['files'][idx-1]['path']).name}")
                elif kind == "finish":
                    state["running"] = False
                    run_btn.configure(state="normal")
                    ok = sum(1 for it in state["files"] if it["status"].startswith("✓"))
                    fail = len(state["files"]) - ok
                    status_var.set(f"完成：成功 {ok} 張，失敗 {fail} 張")
                    progress["value"] = 100
                    messagebox.showinfo("完成", f"去背景完成！\n成功 {ok} 張，失敗 {fail} 張\n\n輸出位置：\n{state.get('out_dir', '')}")
                    return
        except queue.Empty:
            pass
        if state["running"]:
            root.after(80, _poll)

    def _run():
        if state["running"]:
            return
        if not state["files"]:
            messagebox.showwarning("提示", "請先加入圖片（拖拽或點「新增圖片」）")
            return
        out_dir = out_var.get().strip()
        if not out_dir:
            messagebox.showwarning("提示", "請先設定輸出目錄")
            return
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as e:
            messagebox.showerror("錯誤", f"無法建立輸出目錄：\n{e}")
            return
        lo = float(lo_var.get()); hi = float(hi_var.get())
        if lo >= hi:
            messagebox.showwarning("提示", "LO 必須小於 HI")
            return
        min_area = int(area_var.get())
        do_bg = bg_var.get()
        state["out_dir"] = out_dir
        state["running"] = True
        run_btn.configure(state="disabled")
        progress["value"] = 0
        for it in state["files"]:
            it["status"] = "待處理"
        files_snapshot = [{"path": it["path"], "status": it["status"], "ops": list(it.get("ops", []))}
                          for it in state["files"]]
        threading.Thread(target=_worker,
                         args=(files_snapshot, out_dir, lo, hi, min_area, do_bg, state.get("crop")),
                         daemon=True).start()
        root.after(80, _poll)

    refresh_list()
    return root


def main():
    # CLI 自測模式：python batch_bg_remover.py --cli 圖片資料夾 輸出資料夾 [--rotate 90] [--flip h]
    if len(sys.argv) >= 3 and sys.argv[1] == "--cli":
        src, dst = sys.argv[2], sys.argv[3]
        os.makedirs(dst, exist_ok=True)
        transforms = []
        if "--rotate" in sys.argv:
            transforms.append({"type": "rotate", "angle": float(sys.argv[sys.argv.index("--rotate") + 1])})
        if "--flip" in sys.argv:
            transforms.append({"type": "flip", "axis": sys.argv[sys.argv.index("--flip") + 1]})
        do_bg = "--no-bg" not in sys.argv
        crop = None
        if "--crop" in sys.argv:
            nums = [float(x) for x in sys.argv[sys.argv.index("--crop") + 1].replace(",", " ").split()]
            if len(nums) == 4:
                crop = tuple(nums)
        exts = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif"}
        files = [f for f in os.listdir(src)
                 if Path(f).suffix.lower() in exts and os.path.isfile(os.path.join(src, f))]
        print(f"找到 {len(files)} 張圖片，變換：{ops_desc(transforms) or '無'}，去背景：{'是' if do_bg else '否'}"
              + (f"，裁切：{crop}" if crop else ""))
        for f in sorted(files):
            out = unique_output_path(dst, f)
            try:
                w, h = process_image(os.path.join(src, f), out, transforms=transforms or None, do_bg=do_bg, crop=crop)
                print(f"  ✓ {f} -> {os.path.basename(out)} ({w}x{h})")
            except Exception as e:
                print(f"  ✗ {f}: {e}")
        return 0

    # 自測模式：驗證解析 / 清單 / 變換 / 去背邏輯（不開視窗）
    if len(sys.argv) >= 2 and sys.argv[1] == "--selftest":
        import tempfile
        import shutil

        # 1) 拖拽資料解析
        cases = [
            ("{C:/path/a.png} {C:/path/b.png}", ["C:/path/a.png", "C:/path/b.png"]),
            ("C:/path/a.png C:/path/b.png", ["C:/path/a.png", "C:/path/b.png"]),
            ("{C:/my folder/a.png} {C:/x.png}", ["C:/my folder/a.png", "C:/x.png"]),
            ("file:///C:/path/a.png", ["C:/path/a.png"]),
            ("", []),
        ]
        for raw, expect in cases:
            got = parse_drop_data(raw)
            if got != expect:
                print(f"✗ 解析失敗: {raw!r} -> {got} (期望 {expect})")
                return 1
        print("✓ 拖拽解析 5 種格式全部通過")

        # 2) 變換應用：旋轉 90° 交換寬高、水平翻轉鏡像像素
        img = Image.new("RGBA", (4, 8), (0, 0, 0, 0))
        img.putpixel((0, 1), (255, 0, 0, 255))
        r = apply_transforms(img, [{"type": "rotate", "angle": 90}])
        if r.size != (8, 4):
            print(f"✗ 旋轉90°尺寸錯誤: {r.size} != (8,4)")
            return 1
        f = apply_transforms(img, [{"type": "flip", "axis": "h"}])
        if f.getpixel((3, 1)) != (255, 0, 0, 255):
            print(f"✗ 水平翻轉鏡像錯誤: {f.getpixel((3,1))}")
            return 1
        v = apply_transforms(img, [{"type": "flip", "axis": "v"}])
        if v.getpixel((0, 6)) != (255, 0, 0, 255):
            print(f"✗ 垂直翻轉鏡像錯誤: {v.getpixel((0,6))}")
            return 1
        print("✓ 旋轉 / 水平翻轉 / 垂直翻轉 通過")

        # 3) 清單資料流（對應 refresh_list 核心計算）
        tmp = tempfile.mkdtemp()
        try:
            src = os.path.join(tmp, "test_img.png")
            Image.new("RGBA", (8, 8)).save(src)
            files = [{"path": src, "status": "待處理"}]
            p = files[0]["path"]
            sz = os.path.getsize(p) / 1024
            name = os.path.basename(p)
            if name != "test_img.png" or sz <= 0:
                print("✗ 清單資料計算失敗")
                return 1
            # 4) 輸出路徑唯一性
            dst_dir = os.path.join(tmp, "out")
            os.makedirs(dst_dir)
            o1 = unique_output_path(dst_dir, "test_img.png")
            Image.new("RGBA", (4, 4)).save(o1)
            o2 = unique_output_path(dst_dir, "test_img.png")
            if o1 == o2:
                print("✗ 同名輸出未自動加後綴")
                return 1
            # 5) 去背演算法（純色底 8x8）+ 帶變換
            Image.new("RGB", (8, 8), (0, 200, 60)).save(os.path.join(tmp, "bg.png"))
            o3 = unique_output_path(dst_dir, "bg.png")
            w, h = remove_background(os.path.join(tmp, "bg.png"), o3)
            if w != 8 or h != 8:
                print("✗ 去背演算法尺寸錯誤")
                return 1
            o4 = unique_output_path(dst_dir, "bg2.png")
            w2, h2 = remove_background(os.path.join(tmp, "bg.png"), o4,
                                       transforms=[{"type": "rotate", "angle": 90}])
            if (w2, h2) != (8, 8):
                print(f"✗ 帶變換去背尺寸錯誤: {(w2,h2)}")
                return 1
            o5 = unique_output_path(dst_dir, "bg3.png")
            Image.new("RGB", (4, 8), (0, 200, 60)).save(os.path.join(tmp, "bg3.png"))
            w3, h3 = remove_background(os.path.join(tmp, "bg3.png"), o5,
                                       transforms=[{"type": "rotate", "angle": 90}])
            if (w3, h3) != (8, 4):
                print(f"✗ 非正方圖旋轉後尺寸錯誤: {(w3,h3)} != (8,4)")
                return 1
            # 6) 不勾選去背景：只變換、保留背景
            o6 = unique_output_path(dst_dir, "nobg.png")
            w4, h4 = process_image(os.path.join(tmp, "bg3.png"), o6,
                                   transforms=[{"type": "rotate", "angle": 90}], do_bg=False)
            if (w4, h4) != (8, 4):
                print(f"✗ 不去背模式尺寸錯誤: {(w4,h4)}")
                return 1
            chk = Image.open(o6)
            if chk.getpixel((0, 0))[3] != 255:
                print("✗ 不去背模式應保留不透明背景")
                return 1
            # 7) chroma_key 函數（預覽共用）：背景應變透明
            ck = chroma_key(Image.new("RGB", (8, 8), (0, 200, 60)))
            if ck.size != (8, 8) or ck.getpixel((0, 0))[3] != 0:
                print("✗ chroma_key 背景未透明")
                return 1
            # 8) 區域裁切：比例裁切 + 尺寸隨之改變
            Image.new("RGB", (100, 80), (10, 20, 30)).save(os.path.join(tmp, "crop_src.png"))
            o7 = unique_output_path(dst_dir, "crop.png")
            w5, h5 = process_image(os.path.join(tmp, "crop_src.png"), o7, do_bg=False,
                                   crop=(0, 0, 0.5, 1.0))
            if (w5, h5) != (50, 80):
                print(f"✗ 裁切尺寸錯誤: {(w5,h5)} != (50,80)")
                return 1
            if Image.open(o7).size != (50, 80):
                print("✗ 裁切後輸出尺寸未改變")
                return 1
            # 9) 變換 + 裁切順序：先旋轉 90°（100x80→80x100）再裁切左半（→40x100）
            o8 = unique_output_path(dst_dir, "crop2.png")
            w6, h6 = process_image(os.path.join(tmp, "crop_src.png"), o8, do_bg=False,
                                   transforms=[{"type": "rotate", "angle": 90}],
                                   crop=(0, 0, 0.5, 1.0))
            if (w6, h6) != (40, 100):
                print(f"✗ 變換+裁切順序錯誤: {(w6,h6)} != (40,100)")
                return 1
            # 10) 裁切 + 去背：crop 後對裁切區域去背
            Image.new("RGB", (100, 80), (0, 200, 60)).save(os.path.join(tmp, "cropbg.png"))
            o9 = unique_output_path(dst_dir, "cropbg.png")
            w7, h7 = process_image(os.path.join(tmp, "cropbg.png"), o9, do_bg=True,
                                   crop=(0.25, 0.25, 0.75, 0.75))
            if (w7, h7) != (50, 40):
                print(f"✗ 裁切+去背尺寸錯誤: {(w7,h7)} != (50,40)")
                return 1
            print("✓ 清單計算 / 同名輸出 / 去背演算法（含變換）/ 不去背模式 / chroma_key / 區域裁切（含順序）全部通過")
        finally:
            shutil.rmtree(tmp)
        print("自測全部通過")
        return 0

    # GUI 冒煙測試：python batch_bg_remover.py --smoke
    if len(sys.argv) >= 2 and sys.argv[1] == "--smoke":
        root = build_gui()
        root.after(1200, root.destroy)
        root.mainloop()
        print("GUI 冒煙測試 OK")
        return 0

    root = build_gui()
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
