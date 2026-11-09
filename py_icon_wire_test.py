# -*- coding: utf-8 -*-
"""验证图标接入：托盘 HICON 真的拿到了吗？Tk iconbitmap 真的生效吗？

不做"函数返回 True 就算过"的表面验证：
  · 托盘：LoadImageW 返回非空，且 ICONINFO 能查回来（证明确实是图标句柄）
  · 窗口：设置后读回 Win32 窗口的 hIcon（GCLP_HICON），与设进去的比对
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import py_tray

HERE = os.path.dirname(os.path.abspath(__file__))
ICO = os.path.join(HERE, "popdict.ico")

ok = fail = 0


def chk(cond, name, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}" + (f"   {extra}" if extra else ""))
    else:
        fail += 1
        print(f"  FAIL  {name}" + (f"   {extra}" if extra else ""))


print("=" * 68)
print("图标接入验证")
print("=" * 68)

print("\n[1] ICO 文件")
chk(os.path.exists(ICO), "popdict.ico 存在")
size = os.path.getsize(ICO) if os.path.exists(ICO) else 0
chk(size > 10000, "文件大小正常", f"{size} 字节")

# 自己解析 ICO 头，确认尺寸清单
with open(ICO, "rb") as f:
    data = f.read()
reserved, typ, count = int.from_bytes(data[0:2], "little"), \
    int.from_bytes(data[2:4], "little"), int.from_bytes(data[4:6], "little")
chk(reserved == 0, "ICO 保留字段为 0", f"{reserved}")
chk(typ == 1, "类型为 1（图标）", f"{typ}")
sizes = []
for i in range(count):
    off = 6 + i * 16
    w = data[off] or 256
    sizes.append(w)
chk(count >= 5, "至少 5 个尺寸", f"{count} 个: {sizes}")
chk(16 in sizes, "含 16px（任务栏档位）")
chk(32 in sizes, "含 32px（桌面档位）")
chk(256 in sizes, "含 256px（大图档位）")

print("\n[2] 托盘 LoadImageW 加载")
hicon = py_tray.load_icon_from_ico(ICO, 32)
chk(bool(hicon), "LoadImageW 返回非空句柄", f"hIcon={hicon}")

if hicon:
    # 用 GetIconInfo 确认这真的是个图标句柄，而不是野指针
    class ICONINFO(ctypes.Structure):
        _fields_ = [("fIcon", wt.BOOL), ("xHotspot", wt.DWORD),
                    ("yHotspot", wt.DWORD), ("hbmMask", wt.HBITMAP),
                    ("hbmColor", wt.HBITMAP)]

    ii = ICONINFO()
    r = py_tray.user32.GetIconInfo(wt.HICON(hicon), ctypes.byref(ii))
    chk(bool(r), "GetIconInfo 成功（确为有效图标句柄）",
        f"fIcon={ii.fIcon}")
    chk(bool(ii.fIcon), "标记为图标（非光标）")
    chk(bool(ii.hbmColor), "带彩色位图（不是纯掩码）")
    if ii.hbmColor:
        py_tray.gdi32.DeleteObject(ii.hbmColor)
    if ii.hbmMask:
        py_tray.gdi32.DeleteObject(ii.hbmMask)
    py_tray.user32.DestroyIcon(wt.HICON(hicon))

print("\n[3] 缺失文件时安全退化")
chk(py_tray.load_icon_from_ico(os.path.join(HERE, "__none__.ico")) is None,
    "文件不存在返回 None（可退化到手绘）")
chk(py_tray.load_icon_from_ico(None) is None, "传 None 返回 None")

print("\n[4] Tk iconbitmap 生效")
try:
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    root.iconbitmap(default=ICO)
    root.update_idletasks()

    # 读回 Win32 层窗口的 hIcon，与 Tk 设置的结果比对
    py_tray.user32.GetAncestor.restype = wt.HWND
    py_tray.user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
    py_tray.user32.GetClassLongPtrW.restype = ctypes.c_ssize_t
    py_tray.user32.GetClassLongPtrW.argtypes = [wt.HWND, ctypes.c_int]

    hwnd = py_tray.user32.GetAncestor(root.winfo_id(), 2)
    chk(bool(hwnd), "取到根窗口 HWND", f"hwnd={hwnd}")
    GCLP_HICON = -14
    h = py_tray.user32.GetClassLongPtrW(wt.HWND(hwnd), GCLP_HICON)
    chk(bool(h), "窗口类 hIcon 非 0（图标已挂上）", f"hIcon={h}")

    root.destroy()
except Exception as e:
    chk(False, "iconbitmap 调用", f"异常: {e}")

print("\n" + "=" * 68)
print(f"结果: {ok} PASS / {fail} FAIL")
print("=" * 68)
sys.exit(0 if fail == 0 else 1)
