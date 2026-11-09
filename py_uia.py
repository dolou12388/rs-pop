"""纯 ctypes 调用 UIAutomationCore.dll 读取当前选中文字（零剪贴板）。

验证目标：不依赖 selection-hook、不依赖任何原生插件，Python 标准库能否
         直接走 UIA TextPattern 拿到选区文本。

实现的 COM 交互完全手工：
  CoCreateInstance(CLSID_CUIAutomation) -> IUIAutomation
  GetFocusedElement() -> IUIAutomationElement
  GetCurrentPattern(UIA_TextPatternId=10014) -> IUITextPattern
  GetSelection() -> IUIAutomationTextRangeArray
  GetElement(0) -> IUIAutomationTextRange
  GetText(-1) -> BSTR
"""

import ctypes
import ctypes.wintypes as wt
import sys
import time

ole32 = ctypes.WinDLL("ole32")
oleaut32 = ctypes.WinDLL("oleaut32")
uia = ctypes.WinDLL("UIAutomationCore.dll")

CLSID_CUIAutomation = "{FF48DBA4-60EF-4201-AA87-54103EEF594E}"
IID_IUIAutomation = "{30CBE57D-D9D0-452A-AB13-7AC5AC4825EE}"

UIA_TextPatternId = 10014
UIA_ValuePatternId = 10002

S_OK = 0


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wt.DWORD),
        ("Data2", wt.WORD),
        ("Data3", wt.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def guid_from_string(s):
    g = GUID()
    ole32.CLSIDFromString(ctypes.c_wchar_p(s), ctypes.byref(g))
    return g


def hresult_ok(hr):
    return hr >= 0


def fmt_hr(hr):
    return f"0x{hr & 0xFFFFFFFF:08X}"


class UIAReader:
    """手工 COM 调用封装。注意：所有接口指针都用 vtable 索引直接调用。"""

    def __init__(self):
        self._uia = None
        self._elems = []

    def open(self):
        hr = ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
        if not hresult_ok(hr) and hr != 1:  # 1 = S_FALSE，已初始化
            print(f"  CoInitializeEx FAIL {fmt_hr(hr)}")
            return False

        clsid = guid_from_string(CLSID_CUIAutomation)
        iid = guid_from_string(IID_IUIAutomation)
        p = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(
            ctypes.byref(clsid), None, 1,  # CLSCTX_INPROC_SERVER
            ctypes.byref(iid), ctypes.byref(p),
        )
        if not hresult_ok(hr):
            print(f"  CoCreateInstance FAIL {fmt_hr(hr)}")
            return False
        self._uia = p
        return True

    def _vt(self, ptr, index):
        """取 vtable 上第 index 个函数地址。"""
        vtable = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p))[0]
        fn = ctypes.cast(vtable, ctypes.POINTER(ctypes.c_void_p))[index]
        return fn

    def get_focused(self):
        # IUIAutomation vtable: GetFocusedElement 在索引 8
        p = ctypes.c_void_p()
        fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
            self._vt(self._uia, 8)
        )
        hr = fn(self._uia, ctypes.byref(p))
        if not hresult_ok(hr):
            print(f"  GetFocusedElement FAIL {fmt_hr(hr)}")
            return None
        return p

    def get_pattern_text(self, elem):
        """elem.GetCurrentPattern(UIA_TextPatternId) -> IUIAutomationTextPattern"""
        iid_textpat = ctypes.c_void_p()
        # 用接口名解析 IID 更稳：直接按已知 GUID
        iid = guid_from_string("{32C7AB15-9A17-4A3B-9D9F-9E0A2E3B1F5C}")
        pat = ctypes.c_void_p()
        # IUIAutomationElement::GetCurrentPattern 索引 15
        fn = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p),
        )(self._vt(elem, 15))
        hr = fn(elem, UIA_TextPatternId, ctypes.byref(pat))
        if not hresult_ok(hr):
            return None, fmt_hr(hr)
        return pat, None

    def get_selection_text(self, pat):
        """IUIAutomationTextPattern::GetSelection -> array; array.GetElement(0); range.GetText(-1)"""
        arr = ctypes.c_void_p()
        # GetSelection 在 IUIAutomationTextPattern 的索引 5
        fn_sel = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
        )(self._vt(pat, 5))
        hr = fn_sel(pat, ctypes.byref(arr))
        if not hresult_ok(hr):
            return None, f"GetSelection {fmt_hr(hr)}"

        # IUIAutomationTextRangeArray::get_Length 索引 7
        length = ctypes.c_int(0)
        fn_len = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)
        )(self._vt(arr, 7))
        hr = fn_len(arr, ctypes.byref(length))
        if not hresult_ok(hr):
            return None, f"get_Length {fmt_hr(hr)}"
        if length.value == 0:
            return "", "无选区（length=0）"

        # GetElement 索引 8
        rng = ctypes.c_void_p()
        fn_get = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)
        )(self._vt(arr, 8))
        hr = fn_get(arr, 0, ctypes.byref(rng))
        if not hresult_ok(hr):
            return None, f"GetElement {fmt_hr(hr)}"

        # IUIAutomationTextRange::GetText(maxLength) 索引 12
        bstr = ctypes.c_void_p()
        fn_text = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)
        )(self._vt(rng, 12))
        hr = fn_text(rng, -1, ctypes.byref(bstr))
        if not hresult_ok(hr):
            return None, f"GetText {fmt_hr(hr)}"

        oleaut32.SysStringLen.restype = ctypes.c_uint
        n = oleaut32.SysStringLen(bstr)
        buf = ctypes.create_unicode_buffer(n + 1)
        ctypes.memmove(buf, bstr, (n + 1) * 2)
        oleaut32.SysFreeString(bstr)
        return buf.value, None


def main():
    print("=" * 64)
    print("Python ctypes 直调 UIAutomationCore —— 零剪贴板取词验证")
    print("=" * 64)

    r = UIAReader()
    if not r.open():
        print("UIA 初始化失败")
        return 1

    print("[1] CoCreateInstance(CUIAutomation) 成功")

    for attempt in range(1, 4):
        elem = r.get_focused()
        if elem is None:
            print(f"[{attempt}] GetFocusedElement 失败")
            time.sleep(0.5)
            continue

        print(f"[{attempt}] GetFocusedElement 成功  ptr=0x{elem.value:X}")

        pat, err = r.get_pattern_text(elem)
        if pat is None:
            print(f"    TextPattern 不可用: {err}")
            print("    (焦点元素不支持 TextPattern —— 属预期，很多控件不支持)")
            time.sleep(0.5)
            continue

        print(f"    TextPattern 获取成功  ptr=0x{pat.value:X}")
        text, err = r.get_selection_text(pat)
        if err:
            print(f"    GetSelection: {err}")
        else:
            print(f"    选中文字: {text!r}")
            return 0
        time.sleep(0.5)

    print("\n结论：焦点元素未提供可读选区。")
    print("请先在记事本里选中一段文字，再重新运行本脚本。")
    return 2


if __name__ == "__main__":
    sys.exit(main())
