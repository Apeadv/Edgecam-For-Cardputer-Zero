#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EDGECAM / DV-2  ——  Cardputer Zero 摄像机
================================================================
取景器 UI 参考动画里的老式 DV 取景器：
磁带机 OSD（REC / 时间码 / 电量 / SP）+ 夜之城霓虹科技感（黄黑配色、
细线刻度、曝光表、扫描线）。

功能
  * 实况取景（Picamera2 / IMX219，320x170 RGB565 直接写 /dev/fb0）
  * 两档滤镜：01 NORMAL（干净） / 02 KARAX（动画照片的胶片感）
  * H / TAB 一键隐藏 UI
  * ENTER / SPACE 拍照（1640x1232，套用当前滤镜）
  * R 录像（带滤镜的 320x170 @20fps MP4）
  * F 切换滤镜，I 帮助，ESC 退出
"""

from __future__ import annotations

import argparse
import faulthandler
import fcntl
import math
import mmap
import os
import queue
import select
import signal
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageStat

# ----------------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------------
W, H = 320, 170
FB_PATH = "/dev/fb0"
PHOTO_DIR = Path.home() / "Pictures" / "Camera"
VIDEO_DIR = Path.home() / "Videos"
VIDEO_FPS = 20

DEJAVU = "/usr/share/fonts/truetype/dejavu/"
CJK_FONT = "/usr/local/share/cardputer-zh/fonts/NotoSansCJKsc-Bold.otf"

# 夜之城黄 + 警戒红 + 冷青
YELLOW = (245, 198, 25, 255)
YELLOW_DIM = (245, 198, 25, 150)
RED = (255, 58, 48, 255)
RED_DIM = (255, 58, 48, 110)
CYAN = (64, 232, 224, 255)
WHITE = (238, 238, 238, 255)
GREY = (150, 152, 158, 255)
GREY_DIM = (150, 152, 158, 120)
BLACK = (5, 6, 8, 255)

KEY_ESC, KEY_ENTER, KEY_SPACE, KEY_TAB = 1, 28, 57, 15
KEY_F, KEY_H, KEY_I, KEY_R, KEY_L = 33, 35, 23, 19, 38
KEY_0, KEY_9 = 11, 10
KEY_KPENTER, KEY_F1 = 96, 59

ZOOM_STEPS = [1.0, 1.5, 2.0, 2.5, 3.0]


def zoom_step(cur: float, delta: int) -> float:
    idx = min(range(len(ZOOM_STEPS)), key=lambda i: abs(ZOOM_STEPS[i] - cur))
    return ZOOM_STEPS[max(0, min(len(ZOOM_STEPS) - 1, idx + delta))]

LANG_PATH = Path.home() / ".config" / "edgecam" / "lang.txt"

TR = {
    "zh": {
        "help_sub": "DV-2 · NIGHT CITY 取景器",
        "help_snap": "拍照  SNAP",
        "help_rec": "录像  REC",
        "help_filter": "切换滤镜  FILTER",
        "help_zoom": "缩小 / 放大（长按连变）",
        "help_help": "帮助  HELP",
        "help_hud": "隐藏界面  HUD",
        "help_lang": "语言  LANGUAGE",
        "help_exit": "退出  EXIT",
        "help_paths": "照片 ~/Pictures/Camera · 录像 ~/Videos",
        "hud_on": "界面已显示",
        "hud_off": "界面已隐藏",
        "saved": "已保存 {name}",
        "rec_start": "录像中 REC",
        "rec_stopped": "录像已停止",
        "rec_wait": "录像中，先按 R 停止",
        "photo_fail": "拍照失败：{err}",
        "rec_fail": "录像失败：{err}",
        "capturing": "正在采集 / CAPTURING",
        "processing": "正在冲洗 / PROCESSING",
        "cam_offline": "CAMERA OFFLINE",
        "csi": "检查 CSI 排线 / 摄像头模块，ESC 退出",
        "lang_name": "中文",
        "lang_toast": "语言：中文",
        "boot_tip": "回车拍照 · R 录像 · I 隐藏 UI · H 帮助",
    },
    "en": {
        "help_sub": "DV-2 · NIGHT CITY VIEWFINDER",
        "help_snap": "SNAP",
        "help_rec": "RECORD",
        "help_filter": "FILTER",
        "help_zoom": "ZOOM OUT / IN (HOLD)",
        "help_help": "HELP",
        "help_hud": "HIDE HUD",
        "help_lang": "LANGUAGE",
        "help_exit": "EXIT",
        "help_paths": "Photos ~/Pictures/Camera · Video ~/Videos",
        "hud_on": "HUD ON",
        "hud_off": "HUD OFF",
        "saved": "Saved {name}",
        "rec_start": "RECORDING",
        "rec_stopped": "Recording stopped",
        "rec_wait": "Stop recording first (R)",
        "photo_fail": "Capture failed: {err}",
        "rec_fail": "Record failed: {err}",
        "capturing": "CAPTURING",
        "processing": "PROCESSING",
        "cam_offline": "CAMERA OFFLINE",
        "csi": "Check CSI cable / camera module, ESC to exit",
        "lang_name": "English",
        "lang_toast": "Language: English",
        "boot_tip": "ENTER SNAP · R REC · I HUD · H HELP",
    },
}


def load_lang() -> str:
    try:
        v = LANG_PATH.read_text().strip()
        return v if v in ("zh", "en") else "zh"
    except Exception:
        return "zh"


def save_lang(lang: str) -> None:
    try:
        LANG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LANG_PATH.write_text(lang)
    except Exception:
        pass


def T(st, key: str, **kw) -> str:
    table = TR.get(st.lang, TR["zh"])
    return table.get(key, key).format(**kw)


# ----------------------------------------------------------------------------
# 字体
# ----------------------------------------------------------------------------
_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def font(kind: str, size: int):
    key = (kind, size)
    if key in _font_cache:
        return _font_cache[key]
    paths = {
        "mono": DEJAVU + "DejaVuSansMono-Bold.ttf",
        "sans": DEJAVU + "DejaVuSans-Bold.ttf",
        "sans_r": DEJAVU + "DejaVuSans.ttf",
        "cjk": CJK_FONT,
    }
    path = paths.get(kind, paths["sans"])
    try:
        f = ImageFont.truetype(path, size)
    except OSError:
        f = ImageFont.load_default()
    _font_cache[key] = f
    return f


def tlen(draw, s, f):
    return draw.textlength(s, font=f)


def rtext(draw, xy, s, f, fill):
    draw.text(xy, s, font=f, fill=fill, anchor="ra")


# ----------------------------------------------------------------------------
# 帧缓冲
# ----------------------------------------------------------------------------
FBIOGET_VSCREENINFO = 0x4600
FBIOGET_FSCREENINFO = 0x4602


class Framebuffer:
    def __init__(self):
        self.fd = os.open(FB_PATH, os.O_RDWR)
        var = bytearray(160)
        fix = bytearray(80)
        fcntl.ioctl(self.fd, FBIOGET_VSCREENINFO, var)
        fcntl.ioctl(self.fd, FBIOGET_FSCREENINFO, fix)
        self.width, self.height = struct.unpack_from("<2I", var, 0)
        self.bpp = struct.unpack_from("<I", var, 24)[0]
        self.stride = struct.unpack_from("<I", fix, 48)[0]
        self.size = self.stride * self.height
        self.mm = mmap.mmap(self.fd, self.size, mmap.MAP_SHARED,
                            mmap.PROT_READ | mmap.PROT_WRITE)

    def write(self, img: Image.Image) -> None:
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        if img.size != (self.width, self.height):
            img = img.resize((self.width, self.height), Image.Resampling.BILINEAR)
        raw = img.tobytes()
        x = np.frombuffer(raw, dtype=np.uint32)
        if self.bpp == 16:
            packed = (((x & 0xF8) << 8)
                      | ((x & 0xFC00) >> 5)
                      | ((x & 0xF80000) >> 19)).astype("<u2").tobytes()
        else:
            packed = ((x & 0xFF) << 16 | (x & 0xFF00) | ((x >> 16) & 0xFF)).astype("<u4").tobytes()
        self.mm.seek(0)
        self.mm.write(packed)

    def close(self):
        try:
            self.mm.close()
            os.close(self.fd)
        except Exception:
            pass


# ----------------------------------------------------------------------------
# 滤镜（纯 PIL，避免 OpenCV 13 秒的导入开销）
# ----------------------------------------------------------------------------
def _smooth(x):
    return x * x * (3.0 - 2.0 * x)


def _lut_curve(idx: int) -> np.ndarray:
    x = np.linspace(0.0, 1.0, 256)
    if idx == 0:
        # NORMAL：冷色调 —— 蓝青高光、冷阴影，保留可辨识的物体颜色
        y = np.clip(0.5 + (x - 0.5) * 1.06, 0, 1)
        y = x * 0.72 + y * 0.28
        r = np.clip(y - 0.040 + 0.012 * x, 0, 1)
        g = np.clip(y + 0.004 * x, 0, 1)
        b = np.clip(y + 0.070 - 0.035 * x, 0, 1)
    else:
        # KARAX：胶片对比 + 柔和高光肩部；只保留极轻的暖调，不把肤色染蓝
        y = np.clip(0.5 + (x - 0.47) * 1.16, 0, 1)
        y = _smooth(y) * 0.50 + y * 0.50
        y = np.where(y > 0.80, 0.80 + (y - 0.80) * 0.58, y)
        y = np.clip(y * 0.95 + 0.028, 0, 1)
        r = np.clip(y + 0.040 * x ** 2.0, 0, 1)
        g = np.clip(y + 0.010 * x ** 2.0, 0, 1)
        b = np.clip(y - 0.012 * x ** 2.0 + 0.010 * (1 - x) ** 2, 0, 1)
    lut = np.stack([r, g, b], axis=1) * 255.0
    return np.clip(lut, 0, 255).astype(np.uint8)


LUTS = [_lut_curve(0), _lut_curve(1)]
LUT_BYTES = [np.concatenate([lut[:, 0], lut[:, 1], lut[:, 2]]).tobytes()
             for lut in LUTS]
# 泛光缩放表（RGB 三通道各 256 项）
_glow_tab = np.clip(np.arange(256) * 0.42, 0, 255).astype(np.uint8)
GLOW_LUT = np.tile(_glow_tab, 3).tobytes()


def _vignette_image(w: int, h: int, strength: float, power: float = 2.0) -> Image.Image:
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    d = np.sqrt(((xx - cx) / cx) ** 2 + ((yy - cy) / cy) ** 2 * 0.62)
    v = 1.0 - strength * np.clip(d / 1.35, 0, 1) ** power
    v8 = np.clip(v * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(np.dstack([v8, v8, v8]), "RGB")


_VIG_PREVIEW = [None, None]
_VIG_STILL: dict[tuple[int, int, int], Image.Image] = {}

_SMALL_CONST: dict[tuple[int, int], Image.Image] = {}
_BIG_CONST: dict[tuple[int, int], Image.Image] = {}


def vignette_preview(idx: int) -> Image.Image:
    if _VIG_PREVIEW[idx] is None:
        _VIG_PREVIEW[idx] = _vignette_image(
            W, H, 0.16 if idx == 0 else 0.42, 2.0 if idx == 0 else 1.7)
    return _VIG_PREVIEW[idx]


def vignette_still(w: int, h: int, idx: int) -> Image.Image:
    key = (w, h, idx)
    if key not in _VIG_STILL:
        _VIG_STILL[key] = _vignette_image(
            w, h, 0.14 if idx == 0 else 0.40, 2.0 if idx == 0 else 1.7)
    return _VIG_STILL[key]


def apply_filter(img: Image.Image, idx: int) -> Image.Image:
    """320x170 RGB -> 滤镜后的 RGB。"""
    if idx == 0:
        out = img.point(LUT_BYTES[0])
        return ImageChops.multiply(out, vignette_preview(0))

    out = img.point(LUT_BYTES[1])
    out = ImageChops.multiply(out, vignette_preview(1))
    return out


def _big_const(w: int, h: int, rgb) -> Image.Image:
    key = (w, h)
    if key not in _BIG_CONST:
        _BIG_CONST[key] = Image.new("RGB", (w, h), rgb)
    return _BIG_CONST[key]


def process_still(img: Image.Image, idx: int, zoom: float = 1.0) -> Image.Image:
    """全分辨率照片的滤镜（可以慢一点，效果优先）。"""
    img = img.convert("RGB")
    z = max(1.0, min(3.0, zoom))
    if z > 1.01:
        w0, h0 = img.size
        cw, ch = int(w0 / z), int(h0 / z)
        x0, y0 = (w0 - cw) // 2, (h0 - ch) // 2
        img = img.crop((x0, y0, x0 + cw, y0 + ch))
    if idx == 0:
        out = img.point(LUT_BYTES[0])
        out = ImageChops.multiply(out, vignette_still(*img.size, 0))
        return out.filter(ImageFilter.SHARPEN)

    w, h = img.size
    out = img.point(LUT_BYTES[1])
    sw, sh = max(1, w // 4), max(1, h // 4)
    small = out.resize((sw, sh), Image.Resampling.BILINEAR)
    small = ImageChops.subtract(small, _big_const(sw, sh, (148, 148, 148)))
    small = small.filter(ImageFilter.GaussianBlur(4))
    glow = small.resize((w, h), Image.Resampling.BILINEAR).point(GLOW_LUT)
    out = ImageChops.add(out, glow)
    out = ImageChops.multiply(out, vignette_still(w, h, 1))
    # 胶片颗粒
    noise = np.random.default_rng().integers(-7, 8, (h, w, 1), dtype=np.int16)
    arr = np.asarray(out).astype(np.int16) + noise
    out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")

    # 边缘色散（老镜头）
    r, g, b = out.split()
    r = ImageChops.offset(r, 1, 0)
    b = ImageChops.offset(b, -1, 0)
    out = Image.merge("RGB", (r, g, b))
    return out.filter(ImageFilter.SHARPEN)


# ----------------------------------------------------------------------------
# HUD
# ----------------------------------------------------------------------------
class Hud:
    def __init__(self):
        self.f_time = font("mono", 9)
        self.f_label = font("sans", 8)
        self.f_tiny = font("sans", 6)
        self.f_tiny_r = font("sans_r", 6)
        self.f_filter = font("sans", 11)
        self.f_zoom = font("mono", 9)
        self.f_mono7 = font("mono", 7)
        self.f_mono6 = font("mono", 6)
        self.f_cjk = font("cjk", 8)
        self.f_cjk_s = font("cjk", 7)
        self.f_toast = font("cjk", 8)
        self.f_title = font("sans", 26)
        self.f_title_s = font("sans", 7)
        self.base = self._build_base()
        self._cache_key = None
        self._cache_img = None

    # -- 静态底板 ---------------------------------------------------------
    def _build_base(self) -> Image.Image:
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)

        # 上下渐变暗带
        for y in range(0, 30):
            a = int(168 * (1 - y / 30) ** 1.7)
            d.line([(0, y), (W, y)], fill=(3, 4, 6, a))
        for y in range(136, H):
            a = int(185 * ((y - 136) / (H - 136)) ** 1.5)
            d.line([(0, y), (W, y)], fill=(3, 4, 6, a))

        # 三分构图线
        for x in (106, 213):
            d.line([(x, 0), (x, H)], fill=(255, 255, 255, 12))
        for y in (56, 113):
            d.line([(0, y), (W, y)], fill=(255, 255, 255, 12))

        # 内框 + 顶栏细线（科技感）
        d.rectangle([2, 2, W - 3, H - 3], outline=(255, 255, 255, 26), width=1)
        d.line([(6, 21), (W - 6, 21)], fill=(255, 255, 255, 40))
        d.line([(6, 21), (64, 21)], fill=(245, 198, 25, 150), width=1)
        d.line([(6, 135), (W - 6, 135)], fill=(255, 255, 255, 32))
        d.line([(248, 135), (W - 6, 135)], fill=(245, 198, 25, 120), width=1)

        # 四边中心指示刻度
        d.line([(W // 2 - 3, 0), (W // 2 + 3, 0)], fill=(245, 198, 25, 220), width=2)
        d.line([(W // 2 - 3, H - 1), (W // 2 + 3, H - 1)], fill=(245, 198, 25, 220), width=2)
        d.line([(0, H // 2 - 3), (0, H // 2 + 3)], fill=(245, 198, 25, 160), width=2)
        d.line([(W - 1, H // 2 - 3), (W - 1, H // 2 + 3)], fill=(245, 198, 25, 160), width=2)

        # 四角黄色定位框
        arm, off, wd = 15, 5, 2
        for (x, y, dx, dy) in ((off, off, 1, 1), (W - 1 - off, off, -1, 1),
                               (off, H - 1 - off, 1, -1), (W - 1 - off, H - 1 - off, -1, -1)):
            d.line([(x, y), (x + dx * arm, y)], fill=YELLOW, width=wd)
            d.line([(x, y), (x, y + dy * arm)], fill=YELLOW, width=wd)
            d.line([(x + dx * 4, y + dy * 2), (x + dx * 4, y + dy * 5)],
                   fill=(255, 255, 255, 90))

        # 左侧刻度尺
        for i, y in enumerate(range(40, 136, 8)):
            ln = 7 if i % 4 == 0 else 3
            d.line([(5, y), (5 + ln, y)], fill=(255, 255, 255, 90))

        # 中央取景框
        cx, cy, bw, bh = W // 2, 80, 23, 16
        x0, y0, x1, y1 = cx - bw, cy - bh, cx + bw, cy + bh
        for (x, y, dx, dy) in ((x0, y0, 1, 1), (x1, y0, -1, 1),
                               (x0, y1, 1, -1), (x1, y1, -1, -1)):
            d.line([(x, y), (x + dx * 7, y)], fill=(245, 245, 245, 205), width=1)
            d.line([(x, y), (x, y + dy * 7)], fill=(245, 245, 245, 205), width=1)
        d.line([(cx - 3, cy), (cx + 3, cy)], fill=YELLOW, width=1)
        d.line([(cx, cy - 3), (cx, cy + 3)], fill=YELLOW, width=1)
        d.ellipse([cx - 1, cy - 1, cx + 1, cy + 1], fill=(255, 255, 255, 180))
        d.text((x1 + 4, y1 - 4), "FIX", font=self.f_tiny, fill=YELLOW_DIM)

        # 顶部左侧 REC 区域底衬
        d.rounded_rectangle([6, 4, 126, 17], radius=2, fill=(0, 0, 0, 105))

        # 底部滤镜铭牌（斜切角）+ 右下信息板
        d.polygon([(6, 137), (176, 137), (168, 165), (6, 165)], fill=(4, 5, 7, 210))
        d.line([(6, 137), (176, 137)], fill=YELLOW, width=1)
        d.line([(6, 137), (6, 165)], fill=(245, 198, 25, 90), width=1)
        d.rectangle([228, 137, 313, 165], fill=(3, 4, 6, 175))
        d.line([(228, 137), (313, 137)], fill=(255, 255, 255, 45))
        return layer

    # -- 单帧 HUD（带状态缓存，约 10Hz 刷新） ------------------------------
    def draw(self, st, frame_lum: float, t: float) -> Image.Image:
        lvl = max(0.0, min(1.0, frame_lum / 210.0))
        key = (st.filter_idx, st.hud, st.help_on, st.recording,
               int(st.battery), st.expo, st.toast, st.busy, st.lang,
               int(st.zoom * 10), int(t * 10), int(lvl * 12))
        if key != self._cache_key or self._cache_img is None:
            self._cache_key = key
            self._cache_img = self._render(st, frame_lum, t)
        return self._cache_img

    def _render(self, st, frame_lum: float, t: float) -> Image.Image:
        hud = self.base.copy()
        d = ImageDraw.Draw(hud)

        # 扫描线（慢速下移的 CRT 微光）
        sy = int((t * 21) % (H + 40)) - 20
        if 0 <= sy < H:
            d.line([(0, sy), (W, sy)], fill=(255, 225, 120, 16))

        if st.recording:
            d.rectangle([1, 1, W - 2, H - 2], outline=(255, 58, 48, 110), width=1)

        if st.help_on:
            self._draw_help(d, st)
            return hud

        # ---- 顶部左：REC / 时间码 ----
        blink = (t % 1.0) < 0.62
        if st.recording:
            if blink:
                d.ellipse([9, 8, 14, 13], fill=RED)
            d.text((18, 4), "REC", font=self.f_label, fill=RED)
            secs = int(t - st.rec_start)
        else:
            d.rectangle([9, 8, 14, 13], outline=RED_DIM, width=1)
            d.text((18, 4), "STBY", font=self.f_label, fill=(225, 225, 225, 200))
            secs = int(t - st.boot)
        tc = f"{secs // 3600:02d}:{secs // 60 % 60:02d}:{secs % 60:02d}:{int(t * 25) % 25:02d}"
        d.text((45, 4), tc, font=self.f_time, fill=WHITE)
        d.text((108, 6), "25P", font=self.f_tiny, fill=(255, 255, 255, 130))

        # ---- 顶部右：电量 ----
        bx, by = 244, 6
        d.rectangle([bx, by, bx + 17, by + 9], outline=(235, 235, 235, 215), width=1)
        d.rectangle([bx + 18, by + 3, bx + 19, by + 6], fill=(235, 235, 235, 215))
        pct = max(0.0, min(1.0, st.battery / 100.0))
        inner = int(15 * pct)
        col = CYAN if st.battery > 30 else RED
        if inner > 0:
            d.rectangle([bx + 1, by + 1, bx + inner, by + 8], fill=col)
        rtext(d, (W - 16, 4), f"{int(st.battery)}%", self.f_mono7,
              (235, 235, 235, 230) if st.battery > 15 else RED)

        # ---- 顶栏：机型 ----
        d.text((182, 6), "DV-2", font=self.f_tiny, fill=(245, 198, 25, 120))
        d.line([(173, 9), (176, 9)], fill=(245, 198, 25, 120), width=1)

        # ---- 右侧曝光表 ----
        lx, ly0, ly1 = 312, 40, 134
        d.line([(lx, ly0), (lx, ly1)], fill=(255, 255, 255, 50))
        for i in range(0, 10):
            y = ly1 - int((ly1 - ly0) * i / 9)
            d.line([(lx - 3, y), (lx, y)], fill=(255, 255, 255, 65))
        lvl = max(0.0, min(1.0, frame_lum / 210.0))
        yfill = ly1 - int((ly1 - ly0) * lvl)
        d.rectangle([lx - 1, yfill, lx + 2, ly1], fill=(64, 232, 224, 160))
        d.line([(lx - 4, ly0 + int((ly1 - ly0) * 0.32)),
                (lx + 3, ly0 + int((ly1 - ly0) * 0.32))],
               fill=(255, 210, 90, 200))

        # ---- 顶部状态行：曝光参数 ----
        d.text((7, 24), st.expo, font=self.f_mono6, fill=(0, 0, 0, 170))
        d.text((6, 23), st.expo, font=self.f_mono6, fill=(215, 215, 220, 170))

        # ---- 底部滤镜铭牌 ----
        d.text((11, 139), f"FILTER {st.filter_idx + 1:02d}", font=self.f_tiny,
               fill=(185, 187, 193, 210))
        name = "NORMAL" if st.filter_idx == 0 else "KARAX"
        d.text((10, 146), name, font=self.f_filter, fill=YELLOW)
        d.text((122, 147), f"Z {st.zoom:.1f}x", font=self.f_mono7,
               fill=(225, 225, 230, 180))

        # ---- 右上状态行：日期 ----
        stamp = datetime.now().strftime("%y.%m.%d  %H:%M")
        d.text((W - 17, 24), stamp, font=self.f_mono6, fill=(0, 0, 0, 160), anchor="ra")
        rtext(d, (W - 16, 23), stamp, self.f_mono6, (215, 215, 220, 150))

        # ---- 右下信息板 ----
        rtext(d, (W - 8, 139), "H HELP · I HUD", self.f_tiny, (232, 232, 236, 215))
        rtext(d, (W - 8, 148), "F FILTER · R REC", self.f_tiny, (225, 225, 230, 205))
        rtext(d, (W - 8, 157), "ENTER SNAP · ESC EXIT", self.f_tiny,
              (200, 200, 206, 190))

        # ---- 变焦指示条（随倍率平滑移动，1.4 秒后淡出） ----
        if t < st.zoom_hud_until:
            remain = st.zoom_hud_until - t
            a = 1.0 if remain > 0.35 else max(0.0, remain / 0.35)
            x0, y0, x1, y1 = 96, 96, 224, 122
            d.rounded_rectangle([x0, y0, x1, y1], radius=3,
                                fill=(3, 4, 6, int(205 * a)),
                                outline=(245, 198, 25, int(185 * a)))
            d.text((W // 2, y0 + 2), f"Z {st.zoom:.2f}x", font=self.f_zoom,
                   fill=(240, 240, 240, int(245 * a)), anchor="ma")
            bx0, bx1, by = 104, 216, 115
            d.line([(bx0, by), (bx1, by)], fill=(255, 255, 255, int(85 * a)), width=2)
            frac = max(0.0, min(1.0, (st.zoom - 1.0) / 2.0))
            if frac > 0:
                d.line([(bx0, by), (bx0 + int((bx1 - bx0) * frac), by)],
                       fill=(245, 198, 25, int(235 * a)), width=2)
            for z in (1.0, 1.5, 2.0, 2.5, 3.0):
                px = bx0 + int((bx1 - bx0) * (z - 1.0) / 2.0)
                d.line([(px, by - 2), (px, by + 2)],
                       fill=(255, 255, 255, int(130 * a)))

        # ---- Toast ----
        if st.toast and t < st.toast_until:
            self._draw_toast(d, st.toast)
        return hud

    def _draw_toast(self, d, msg: str):
        f = self.f_toast
        tw = tlen(d, msg, f)
        x0 = int((W - tw) / 2 - 7)
        y0 = 118
        d.rounded_rectangle([x0, y0, x0 + tw + 14, y0 + 17], radius=2,
                            fill=(2, 3, 5, 215), outline=(245, 198, 25, 200), width=1)
        d.text((x0 + 7, y0 + 3), msg, font=f, fill=(240, 240, 240, 245))

    def _draw_help(self, d, st):
        d.rectangle([0, 0, W, H], fill=(2, 3, 5, 244))
        d.rectangle([7, 7, W - 8, H - 8], outline=(245, 198, 25, 110), width=1)
        d.text((14, 11), "EDGECAM", font=font("sans", 10), fill=YELLOW)
        d.text((14, 25), T(st, "help_sub"), font=self.f_cjk_s,
               fill=(200, 200, 200, 220))
        snap_key = "ENTER / SPACE" if st.lang == "en" else "回车 / 空格"
        rows = [
            (snap_key, T(st, "help_snap")),
            ("R", T(st, "help_rec")),
            ("F", T(st, "help_filter")),
            ("9 / 0", T(st, "help_zoom")),
            ("H / TAB", T(st, "help_help")),
            ("I", T(st, "help_hud")),
            ("L", T(st, "help_lang")),
            ("ESC", T(st, "help_exit")),
        ]
        y = 38
        for k, v in rows:
            d.text((16, y), k, font=self.f_cjk_s, fill=YELLOW)
            d.text((104, y), v, font=self.f_cjk_s, fill=(225, 225, 225, 230))
            y += 13
        d.text((14, 140), T(st, "help_paths"), font=self.f_cjk_s,
               fill=(170, 170, 175, 215))


# ----------------------------------------------------------------------------
# Picamera2（采集线程，避免渲染被阻塞）
# ----------------------------------------------------------------------------
class Camera:
    def __init__(self):
        from picamera2 import Picamera2
        self.picam = Picamera2()
        cfg = self.picam.create_preview_configuration(
            main={"size": (960, 540), "format": "RGB888"}, buffer_count=4)
        self._preview_cfg = cfg
        self.picam.configure(cfg)
        try:
            self.picam.set_controls({
                "FrameRate": 30,
                "FrameDurationLimits": (33333, 66667),
            })
        except Exception:
            pass
        self.picam.start()
        time.sleep(0.6)

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._frame: Image.Image | None = None
        self.expo = "1/50  F2.0  ISO 200"
        self.zoom = 1.0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        n = 0
        while not self._stop.is_set():
            try:
                with self._lock:
                    a = self.picam.capture_array("main")
                if a.ndim == 3 and a.shape[2] == 4:
                    a = a[:, :, :3]
                # 这条 ISP 管线输出 BGR 顺序，转成 RGB（否则红蓝互换）
                a = a[:, :, ::-1]
                a = a[15:525]                     # 960x510，匹配 320x170 比例
                z = max(1.0, min(3.0, self.zoom))
                if z > 1.01:
                    hh, ww = a.shape[:2]
                    cw, ch = int(ww / z), int(hh / z)
                    x0, y0 = (ww - cw) // 2, (hh - ch) // 2
                    a = a[y0:y0 + ch, x0:x0 + cw]
                img = Image.fromarray(
                    np.ascontiguousarray(a), "RGB").transpose(
                    Image.Transpose.ROTATE_180).resize(
                    (W, H), Image.Resampling.BOX)
                self._frame = img
                n += 1
                if n % 15 == 0:
                    try:
                        with self._lock:
                            m = self.picam.capture_metadata()
                        exp = float(m.get("ExposureTime", 0) or 0)
                        gain = float(m.get("AnalogueGain", 1) or 1)
                        if exp >= 1000:
                            s = f"1/{max(1, round(1e6 / exp))}"
                        elif exp > 0:
                            s = f"{exp / 1000:.1f}s"
                        else:
                            s = "AUTO"
                        iso = int(round(gain * 100 / 50.0) * 50)
                        self.expo = f"{s}  F2.0  ISO {iso}"
                    except Exception:
                        pass
            except Exception:
                time.sleep(0.2)

    def latest(self):
        return self._frame

    def capture_still(self) -> Image.Image:
        """停流 -> 切静态模式 -> 拍 -> 切回预览 -> 重启采集线程。"""
        self._stop.set()
        try:
            self.picam.stop()
        except Exception:
            pass
        try:
            self._thread.join(timeout=3.0)
        except Exception:
            pass
        try:
            still = self.picam.create_still_configuration(
                main={"size": (1640, 1232), "format": "RGB888"})
            self.picam.configure(still)
            self.picam.start()
            time.sleep(0.9)      # 让 AE/AWB 稳定
            a = self.picam.capture_array("main")
        finally:
            try:
                self.picam.stop()
            except Exception:
                pass
            try:
                self.picam.configure(self._preview_cfg)
                self.picam.start()
            except Exception:
                pass
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        if a.ndim == 3 and a.shape[2] == 4:
            a = a[:, :, :3]
        a = a[:, :, ::-1]
        return Image.fromarray(np.ascontiguousarray(a), "RGB").transpose(Image.Transpose.ROTATE_180)

    def close(self):
        self._stop.set()
        try:
            self.picam.stop()
        except Exception:
            pass
        try:
            self._thread.join(timeout=2)
            self.picam.close()
        except Exception:
            pass


# ----------------------------------------------------------------------------
# 录像：把带滤镜的取景帧交给 ffmpeg 编码（所见即所得）
# ----------------------------------------------------------------------------
class Recorder:
    def __init__(self, path: Path):
        VIDEO_DIR.mkdir(parents=True, exist_ok=True)
        self.path = path
        cmd = [
            "/usr/bin/ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{W}x{H}", "-r", str(VIDEO_FPS), "-i", "-",
            "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "24",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-y", str(path),
        ]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        self.q: queue.Queue = queue.Queue(maxsize=4)
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.dropped = 0

    def _run(self):
        while True:
            item = self.q.get()
            if item is None:
                break
            try:
                self.proc.stdin.write(item)
            except Exception:
                break
        try:
            self.proc.stdin.close()
        except Exception:
            pass

    def feed(self, img: Image.Image):
        if self.q.full():
            self.dropped += 1
            return
        try:
            self.q.put_nowait(img.tobytes())
        except Exception:
            pass

    def stop(self):
        try:
            self.q.put(None, timeout=1.0)
        except Exception:
            pass
        self.thread.join(timeout=4.0)
        try:
            self.proc.wait(timeout=6.0)
        except Exception:
            try:
                self.proc.terminate()
            except Exception:
                pass
        return self.path


# ----------------------------------------------------------------------------
# 按键
# ----------------------------------------------------------------------------
class Keys:
    def __init__(self):
        self.devices = []
        self._paths = set()
        self._last_scan = 0.0
        self._scan()

    def _scan(self):
        try:
            import evdev
        except ImportError:
            return
        import glob
        for p in sorted(glob.glob("/dev/input/event*")):
            real = os.path.realpath(p)
            if real in self._paths:
                continue
            try:
                dev = evdev.InputDevice(real)
                caps = dev.capabilities(absinfo=False)
                if evdev.ecodes.EV_KEY in caps:
                    self.devices.append(dev)
                    self._paths.add(real)
            except OSError:
                continue

    def _rebuild(self):
        for d in self.devices:
            try:
                d.close()
            except Exception:
                pass
        self.devices = []
        self._paths.clear()
        self._scan()

    def poll(self) -> list[tuple[int, int]]:
        out = []
        now = time.monotonic()
        if now - self._last_scan > 1.5:
            self._last_scan = now
            self._scan()
        if not self.devices:
            return out
        try:
            ready, _, _ = select.select([d.fd for d in self.devices], [], [], 0)
        except (OSError, ValueError):
            self._rebuild()
            return out
        for dev in list(self.devices):
            if dev.fd not in ready:
                continue
            try:
                for ev in dev.read():
                    if ev.type == 1:
                        out.append((ev.code, ev.value))
            except OSError:
                self._rebuild()
                break
        return out


# ----------------------------------------------------------------------------
# 状态
# ----------------------------------------------------------------------------
@dataclass
class State:
    filter_idx: int = 0
    hud: bool = True
    help_on: bool = False
    recording: bool = False
    rec_start: float = 0.0
    zoom: float = 1.0
    battery: float = 100.0
    toast: str = ""
    toast_until: float = 0.0
    boot: float = 0.0
    splash_until: float = 0.0
    flash_until: float = 0.0
    last_photo: str = ""
    expo: str = "1/50  F2.0  ISO 200"
    busy: str = ""
    rec_path: str = ""
    last_feed: float = 0.0
    rec_frames: int = 0
    lang: str = "zh"
    zoom_target: float = 1.0
    zoom_hud_until: float = 0.0
    photo_freeze: Image.Image | None = None
    photo_until: float = 0.0

    def say(self, msg: str, t: float, secs: float = 2.0):
        self.toast = msg
        self.toast_until = t + secs


# ----------------------------------------------------------------------------
# 电池
# ----------------------------------------------------------------------------
def read_battery() -> float:
    try:
        for p in Path("/sys/class/power_supply").glob("*/capacity"):
            return float(p.read_text().strip())
    except Exception:
        pass
    return 100.0


# ----------------------------------------------------------------------------
# 合成
# ----------------------------------------------------------------------------
def lum_of(img: Image.Image) -> float:
    try:
        m = ImageStat.Stat(img).mean
        return (m[0] + m[1] + m[2]) / 3.0
    except Exception:
        return 100.0


def compose(img: Image.Image, hud: Hud, st: State, lum: float, t: float) -> Image.Image:
    base = img.convert("RGBA")
    if st.hud or st.help_on:
        base.alpha_composite(hud.draw(st, lum, t))
    elif st.toast and t < st.toast_until:
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        hud._draw_toast(ImageDraw.Draw(layer), st.toast)
        base.alpha_composite(layer)
    return base


# ----------------------------------------------------------------------------
# 合成测试画面（无相机时用于 UI 校对）
# ----------------------------------------------------------------------------
def synthetic(t: float) -> Image.Image:
    img = Image.new("RGB", (W, 180), (6, 8, 14))
    d = ImageDraw.Draw(img)
    for y in range(180):
        k = y / 180.0
        d.line([(0, y), (W, y)], fill=(int(8 + 52 * k * k), int(6 + 12 * k),
                                      int(20 + 46 * k)))
    rngl = np.random.default_rng(11)
    for i in range(18):
        x = int(i * 19 + rngl.integers(0, 6))
        wd = int(rngl.integers(12, 26))
        ht = int(rngl.integers(30, 90))
        d.rectangle([x, 140 - ht, x + wd, 150], fill=(10, 12, 20))
        for wy in range(140 - ht + 6, 144, 8):
            for wx in range(x + 4, x + wd - 4, 6):
                if rngl.random() > 0.55:
                    d.rectangle([wx, wy, wx + 2, wy + 3], fill=(90, 70, 30))
    d.rectangle([18, 46, 96, 78], fill=(16, 12, 8), outline=(245, 198, 25))
    d.text((24, 50), "NIGHT", font=font("sans", 13), fill=(245, 198, 25))
    d.text((24, 64), "CITY", font=font("sans", 10), fill=(255, 90, 60))
    d.rectangle([214, 34, 300, 58], fill=(6, 14, 18), outline=(64, 232, 224))
    d.text((220, 38), "DV-2  //  25P", font=font("mono", 8), fill=(64, 232, 224))
    d.ellipse([150, 92, 172, 114], fill=(24, 20, 28))
    d.rectangle([148, 112, 176, 152], fill=(24, 20, 28))
    for i, c in enumerate([(60, 40, 10), (10, 50, 52), (50, 20, 18)]):
        d.rectangle([20 + i * 90, 152, 120 + i * 60, 154], fill=c)
    d.text((120, 158), "ANIME FILM TEST", font=font("mono", 8), fill=(120, 120, 120))
    return img.crop((0, 5, W, 175))


# ----------------------------------------------------------------------------
# 主程序
# ----------------------------------------------------------------------------
RUNNING = True


def _stop(signum, frame):
    global RUNNING
    RUNNING = False


signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)
try:
    faulthandler.register(signal.SIGUSR1, all_threads=True)
except Exception:
    pass


def set_backlight(value: int):
    try:
        p = Path("/sys/class/backlight/backlight/brightness")
        if p.exists():
            p.write_text(str(value))
    except OSError:
        pass


def busy_image(msg: str) -> Image.Image:
    img = Image.new("RGBA", (W, H), (4, 5, 7, 255))
    d = ImageDraw.Draw(img)
    d.text((W // 2, 58), "EDGECAM", font=font("sans", 12), fill=YELLOW, anchor="ma")
    d.text((W // 2, 78), msg, font=font("cjk", 8), fill=(235, 235, 235, 235), anchor="ma")
    d.rectangle([W // 2 - 46, 94, W // 2 + 46, 97], outline=(245, 198, 25, 120))
    d.rectangle([W // 2 - 46, 94, W // 2 - 46 + 30, 97], fill=(245, 198, 25, 220))
    return img


def take_photo(cam: Camera, st: State, fb: Framebuffer, hud: Hud, t: float,
               freeze: Image.Image):
    """快门：立刻定格当前取景画面，不显示进度条；拍完展示成片。"""
    idx = st.filter_idx
    zoom = st.zoom
    st.photo_freeze = freeze
    st.photo_until = time.monotonic() + 6.0
    try:
        fb.write(compose(freeze, hud, st, lum_of(freeze), time.monotonic()))
        raw = cam.capture_still()
        out = process_still(raw, idx, zoom)
        PHOTO_DIR.mkdir(parents=True, exist_ok=True)
        path = PHOTO_DIR / (datetime.now().strftime("EDG_%Y%m%d_%H%M%S") +
                            ("_KARAX" if idx else "_NORMAL") + ".jpg")
        out.save(path, quality=92, optimize=False)
        st.last_photo = str(path)
        # 定格画面替换成刚拍下的成片（缩到屏幕大小）
        st.photo_freeze = out.resize((W, H), Image.Resampling.BILINEAR)
        st.photo_until = time.monotonic() + 1.7
        st.say(T(st, "saved", name=Path(path).name), time.monotonic(), 2.6)
    except Exception as exc:
        st.photo_until = time.monotonic() + 0.8
        st.say(T(st, "photo_fail", err=str(exc)[:22]), time.monotonic(), 2.5)


def main() -> int:
    global RUNNING
    ap = argparse.ArgumentParser()
    ap.add_argument("--render", help="渲染一帧到 PNG 后退出")
    ap.add_argument("--filter", type=int, default=0)
    ap.add_argument("--no-camera", action="store_true")
    ap.add_argument("--hud", dest="hud", action="store_true", default=True)
    ap.add_argument("--no-hud", dest="hud", action="store_false")
    ap.add_argument("--lock", help="覆盖电量数字")
    ap.add_argument("--bench", type=float, default=0.0, help="运行 N 秒后打印帧率并退出")
    args = ap.parse_args()

    hud = Hud()
    st = State(filter_idx=max(0, min(1, args.filter)), hud=args.hud)
    st.lang = load_lang()
    st.boot = time.monotonic()
    if not args.render:
        st.say(T(st, "boot_tip"), st.boot + 1.2, 4.5)

    # ---- 单帧渲染（调试 / 校对） ----
    if args.render:
        if args.no_camera:
            frame = synthetic(0.0)
        else:
            cam = Camera()
            time.sleep(0.4)
            frame = cam.latest() or synthetic(0.0)
            cam.close()
        st.battery = float(args.lock) if args.lock else read_battery()
        out = compose(apply_filter(frame, st.filter_idx), hud, st,
                      lum_of(frame), time.monotonic())
        frame.save(str(Path(args.render).with_name(Path(args.render).stem + "_raw.png")))
        out.convert("RGB").save(args.render)
        print("rendered", args.render)
        return 0

    # ---- 正常启动 ----
    old_bl = None
    try:
        old_bl = int(Path("/sys/class/backlight/backlight/brightness").read_text().strip())
    except Exception:
        pass
    set_backlight(85)
    fb = Framebuffer()
    fb.write(Image.new("RGB", (W, H), (0, 0, 0)))

    cam = None
    cam_err = ""
    rec: Recorder | None = None
    try:
        cam = Camera()
    except Exception as exc:
        cam_err = str(exc)

    keys = Keys()
    frame = None
    last_batt = 0.0
    bench_start = time.monotonic()
    bench_frames = 0
    last_t = time.monotonic()
    zoom_hold: int | None = None
    zoom_next = 0.0
    try:
        while RUNNING:
            t = time.monotonic()
            dt = min(0.12, max(0.0, t - last_t))
            last_t = t
            if abs(st.zoom_target - st.zoom) > 0.002:
                st.zoom += (st.zoom_target - st.zoom) * (1.0 - math.exp(-dt / 0.085))
                if abs(st.zoom_target - st.zoom) < 0.012:
                    st.zoom = st.zoom_target
                if cam is not None:
                    cam.zoom = st.zoom
            if (zoom_hold is not None and t >= zoom_next
                    and st.photo_freeze is None and not st.help_on):
                nxt = zoom_step(st.zoom_target, 1 if zoom_hold == KEY_0 else -1)
                if nxt != st.zoom_target:
                    st.zoom_target = nxt
                    st.zoom_hud_until = t + 1.4
                    zoom_next = t + 0.18
                else:
                    zoom_next = t + 0.35
                    st.zoom_hud_until = t + 1.4
            if args.bench and t - bench_start > args.bench:
                RUNNING = False
            frozen = st.photo_freeze is not None and t < st.photo_until
            if frozen:
                img = st.photo_freeze
            else:
                if st.photo_freeze is not None:
                    st.photo_freeze = None
                if cam is not None:
                    frame = cam.latest()
                if frame is None:
                    err = Image.new("RGBA", (W, H), (6, 8, 12, 255))
                    d = ImageDraw.Draw(err)
                    d.text((18, 58), T(st, "cam_offline"), font=font("sans", 16), fill=RED)
                    d.text((18, 82), cam_err[:52], font=font("sans", 6),
                           fill=(200, 200, 200, 220))
                    d.text((18, 100), T(st, "csi"),
                           font=font("cjk", 8), fill=(230, 230, 230, 230))
                    fb.write(err)
                    for code, value in keys.poll():
                        if value == 1 and code in (KEY_ESC, KEY_F1):
                            RUNNING = False
                    time.sleep(0.05)
                    continue
                img = apply_filter(frame, st.filter_idx)

            if (not frozen and rec is not None
                    and t - st.last_feed >= 1.0 / VIDEO_FPS):
                rec.feed(img)
                st.last_feed = t
                st.rec_frames += 1

            if t - last_batt > 5.0:
                st.battery = read_battery()
                last_batt = t
            if cam is not None:
                st.expo = cam.expo

            out = compose(img, hud, st, lum_of(img), t)

            if t < st.splash_until:
                k = min(1.0, (st.splash_until - t) / 0.4)
                layer = Image.new("RGBA", (W, H), (0, 0, 0, int(240 * k)))
                d = ImageDraw.Draw(layer)
                d.text((W // 2, 62), "EDGECAM", font=hud.f_title, fill=YELLOW, anchor="ma")
                d.text((W // 2, 96), "DV-2  ·  NIGHT CITY", font=hud.f_title_s,
                       fill=(220, 220, 220, 220), anchor="ma")
                d.line([(W // 2 - 58, 110), (W // 2 + 58, 110)], fill=(245, 198, 25, 180))
                out.alpha_composite(layer)

            fb.write(out)
            bench_frames += 1

            for code, value in keys.poll():
                if value == 0:
                    if code == zoom_hold:
                        zoom_hold = None
                        st.zoom_hud_until = max(st.zoom_hud_until,
                                                time.monotonic() + 1.2)
                    continue
                if code == KEY_ESC:
                    if st.help_on:
                        st.help_on = False
                    else:
                        RUNNING = False
                elif code in (KEY_ENTER, KEY_KPENTER, KEY_SPACE):
                    if st.help_on:
                        st.help_on = False
                        continue
                    if st.photo_freeze is not None:
                        continue
                    if st.recording:
                        st.say(T(st, "rec_wait"), time.monotonic(), 1.6)
                        continue
                    take_photo(cam, st, fb, hud, time.monotonic(), img)
                elif code == KEY_R:
                    if st.help_on:
                        st.help_on = False
                        continue
                    if not st.recording:
                        try:
                            path = VIDEO_DIR / datetime.now().strftime(
                                "EDG_%Y%m%d_%H%M%S.mp4")
                            rec = Recorder(path)
                            st.recording = True
                            st.rec_start = time.monotonic()
                            st.rec_frames = 0
                            st.last_feed = 0.0
                            st.say(T(st, "rec_start"), time.monotonic(), 1.4)
                        except Exception as exc:
                            rec = None
                            st.say(T(st, "rec_fail", err=str(exc)[:20]),
                                   time.monotonic(), 2.5)
                    else:
                        saved = None
                        try:
                            if rec is not None:
                                saved = rec.stop()
                        finally:
                            rec = None
                            st.recording = False
                        if saved:
                            st.say(T(st, "saved", name=Path(saved).name),
                                   time.monotonic(), 2.6)
                        else:
                            st.say(T(st, "rec_stopped"), time.monotonic(), 1.6)
                elif code == KEY_F:
                    st.filter_idx = (st.filter_idx + 1) % 2
                    st.say("FILTER %02d · %s" % (
                        st.filter_idx + 1,
                        "NORMAL" if st.filter_idx == 0 else "KARAX"),
                        time.monotonic(), 1.6)
                elif code in (KEY_0, KEY_9):
                    zoom_hold = code
                    zoom_next = time.monotonic() + 0.35
                    st.zoom_target = zoom_step(st.zoom_target, 1 if code == KEY_0 else -1)
                    st.zoom_hud_until = time.monotonic() + 1.4
                elif code in (KEY_H, KEY_TAB):
                    zoom_hold = None
                    st.help_on = not st.help_on
                elif code == KEY_I:
                    st.hud = not st.hud
                    st.say(T(st, "hud_on" if st.hud else "hud_off"),
                           time.monotonic(), 1.2)
                elif code == KEY_L:
                    st.lang = "en" if st.lang == "zh" else "zh"
                    save_lang(st.lang)
                    st.say(T(st, "lang_toast"), time.monotonic(), 1.6)
    finally:
        if rec is not None:
            try:
                rec.stop()
            except Exception:
                pass
            rec = None
            st.recording = False
        if cam is not None:
            cam.close()
        try:
            fb.write(Image.new("RGB", (W, H), (0, 0, 0)))
        except Exception:
            pass
        fb.close()
        if old_bl is not None:
            set_backlight(old_bl)
    if args.bench and bench_frames:
        dur = max(0.001, time.monotonic() - bench_start)
        print(f"bench: {bench_frames} frames / {dur:.1f}s = {bench_frames / dur:.1f} fps")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
