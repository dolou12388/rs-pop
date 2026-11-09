# -*- coding: utf-8 -*-
"""浮层的抗锯齿圆角与淡阴影：Win32 逐像素透明底板。"""

import ctypes
import math
from ctypes import wintypes as wt

user32 = ctypes.WinDLL("user32.dll", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32.dll", use_last_error=True)

WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_FRAMECHANGED = 0x0020
SW_SHOWNOACTIVATE = 4
ULW_ALPHA = 0x00000002
HWND_TOPMOST = ctypes.c_void_p(-1)


def _distance(x, y, left, top, width, height, radius):
    """圆角矩形的带符号距离，负值表示位于内部。"""
    qx = abs(x - left - width / 2) - (width / 2 - radius)
    qy = abs(y - top - height / 2) - (height / 2 - radius)
    return (math.hypot(max(qx, 0), max(qy, 0)) +
            min(max(qx, qy), 0) - radius)


def backdrop_pixels(width, height, radius, color):
    """返回预乘 alpha BGRA 像素、画布尺寸与浮层内边距。"""
    radius = max(1, min(int(radius), width // 2, height // 2))
    pad = max(8, math.ceil(radius * 1.4))
    canvas_width, canvas_height = width + 2 * pad, height + 2 * pad
    red, green, blue = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    sigma = max(2.0, radius * 0.42)
    drop = max(1, round(radius * 0.14))
    pixels = bytearray(canvas_width * canvas_height * 4)
    samples = (0.125, 0.375, 0.625, 0.875)

    for y in range(canvas_height):
        for x in range(canvas_width):
            d = _distance(x + .5, y + .5, pad, pad, width, height, radius)
            if d <= -0.75:
                coverage = 1.0
            elif d >= 0.75:
                coverage = 0.0
            else:
                count = sum(_distance(x + sx, y + sy, pad, pad,
                                      width, height, radius) <= 0
                            for sy in samples for sx in samples)
                coverage = count / 16

            shadow_d = _distance(x + .5, y + .5, pad, pad + drop,
                                 width, height, radius)
            shadow = round(20 * math.exp(-.5 *
                           (max(shadow_d, 0) / sigma) ** 2))
            alpha = min(255, round(255 * coverage +
                                   shadow * (1 - coverage)))
            offset = (y * canvas_width + x) * 4
            pixels[offset:offset + 4] = bytes((
                round(blue * coverage), round(green * coverage),
                round(red * coverage), alpha))
    return bytes(pixels), canvas_width, canvas_height, pad


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("header", _BitmapInfoHeader), ("colors", wt.DWORD * 3)]


class _Size(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class _BlendFunction(ctypes.Structure):
    _fields_ = [("op", wt.BYTE), ("flags", wt.BYTE),
                ("opacity", wt.BYTE), ("alpha_format", wt.BYTE)]


_HANDLE = ctypes.c_void_p
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                  ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int,
                                  _HANDLE, _HANDLE, _HANDLE, _HANDLE]
user32.CreateWindowExW.restype = _HANDLE
user32.UpdateLayeredWindow.argtypes = [
    _HANDLE, _HANDLE, ctypes.POINTER(wt.POINT), ctypes.POINTER(_Size),
    _HANDLE, ctypes.POINTER(wt.POINT), wt.DWORD,
    ctypes.POINTER(_BlendFunction), wt.DWORD]
user32.UpdateLayeredWindow.restype = wt.BOOL
user32.SetWindowPos.argtypes = [_HANDLE, _HANDLE, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wt.UINT]
user32.SetWindowPos.restype = wt.BOOL
user32.ShowWindow.argtypes = [_HANDLE, ctypes.c_int]
user32.IsWindowVisible.argtypes = [_HANDLE]
user32.IsWindowVisible.restype = wt.BOOL
user32.DestroyWindow.argtypes = [_HANDLE]
gdi32.CreateCompatibleDC.argtypes = [_HANDLE]
gdi32.CreateCompatibleDC.restype = _HANDLE
gdi32.CreateDIBSection.argtypes = [
    _HANDLE, ctypes.POINTER(_BitmapInfo), wt.UINT,
    ctypes.POINTER(_HANDLE), _HANDLE, wt.DWORD]
gdi32.CreateDIBSection.restype = _HANDLE
gdi32.SelectObject.argtypes = [_HANDLE, _HANDLE]
gdi32.SelectObject.restype = _HANDLE
gdi32.DeleteDC.argtypes = [_HANDLE]
gdi32.DeleteObject.argtypes = [_HANDLE]


class PillBackdrop:
    """位于 Tk 按钮后方的透明原生窗口，不参与点击和焦点。"""

    def __init__(self):
        self.hwnd = user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW |
            WS_EX_NOACTIVATE | WS_EX_TOPMOST,
            "STATIC", "", WS_POPUP, 0, 0, 1, 1,
            None, None, None, None)
        self.pad = 0
        self.canvas_width = 0
        self.canvas_height = 0
        self._key = None
        self._position = None

    def prepare(self, width, height, radius, color):
        """上传位图；失败时调用方可退回现有二值圆角。"""
        key = (width, height, radius, color)
        if not self.hwnd:
            return False
        if key == self._key:
            return True
        pixels, canvas_width, canvas_height, pad = backdrop_pixels(
            width, height, radius, color)
        info = _BitmapInfo()
        info.header.biSize = ctypes.sizeof(_BitmapInfoHeader)
        info.header.biWidth = canvas_width
        info.header.biHeight = -canvas_height
        info.header.biPlanes = 1
        info.header.biBitCount = 32
        bits = _HANDLE()
        dc = gdi32.CreateCompatibleDC(None)
        if not dc:
            return False
        bitmap = None
        previous = None
        try:
            bitmap = gdi32.CreateDIBSection(
                dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
            if not bitmap or not bits.value:
                return False
            previous = gdi32.SelectObject(dc, bitmap)
            if not previous:
                return False
            ctypes.memmove(bits.value, pixels, len(pixels))
            if self._position is None:
                dest = wt.POINT(0, 0)
            else:
                dest = wt.POINT(self._position[0] - pad,
                                self._position[1] - pad)
            size = _Size(canvas_width, canvas_height)
            source = wt.POINT(0, 0)
            blend = _BlendFunction(0, 0, 255, 1)
            ok = user32.UpdateLayeredWindow(
                self.hwnd, None, ctypes.byref(dest), ctypes.byref(size),
                dc, ctypes.byref(source), 0, ctypes.byref(blend), ULW_ALPHA)
            if not ok:
                return False
            self.pad = pad
            self.canvas_width = canvas_width
            self.canvas_height = canvas_height
            self._key = key
            return True
        finally:
            if previous:
                gdi32.SelectObject(dc, previous)
            if bitmap:
                gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(dc)

    def show(self, x, y, front_hwnd):
        if not self.hwnd or self._key is None:
            return False
        positioned = user32.SetWindowPos(
            self.hwnd, front_hwnd, x - self.pad, y - self.pad,
            self.canvas_width, self.canvas_height,
            SWP_NOACTIVATE | SWP_SHOWWINDOW)
        if not positioned:
            return False
        # STATIC 类窗口需要显式 ShowWindow 才会置上 WS_VISIBLE。
        if not user32.IsWindowVisible(self.hwnd):
            user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
        reordered = user32.SetWindowPos(
            self.hwnd, front_hwnd, x - self.pad, y - self.pad,
            self.canvas_width, self.canvas_height, SWP_NOACTIVATE)
        visible = bool(reordered and user32.IsWindowVisible(self.hwnd))
        if visible:
            self._position = (x, y)
        return visible

    def hide(self):
        if self.hwnd:
            user32.ShowWindow(self.hwnd, 0)

    def close(self):
        if self.hwnd:
            user32.DestroyWindow(self.hwnd)
            self.hwnd = None
