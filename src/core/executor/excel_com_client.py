"""Module: @role: Windows COMおよびアクセシビリティAPIを介した実行中Excelアプリケーションインスタンスの特定・取得を担当する。"""

import ctypes
import platform
from typing import Any, Optional


def _find_excel7_child_hwnd(hwnd: int) -> Optional[int]:
    import win32gui

    # Why: タイトル文字列に依存せずウィンドウクラス名(XLMAIN/EXCEL7)でExcelを厳密判定
    cls_name = win32gui.GetClassName(hwnd)
    if cls_name == "EXCEL7":
        return hwnd
    if cls_name != "XLMAIN":
        return None

    found: Optional[int] = None

    def enum_child(child: int, _: Any) -> bool:
        nonlocal found
        try:
            if win32gui.GetClassName(child) == "EXCEL7":
                found = child
                return False
        except Exception:
            pass
        return True

    try:
        win32gui.EnumChildWindows(hwnd, enum_child, None)
    except Exception:
        pass
    return found


def get_excel_application(hwnd: Optional[int] = None, expected_title: str = "") -> Any:
    if platform.system() != "Windows":
        return None
    try:
        import pythoncom
        import win32com.client
        import win32gui

        excel7_hwnd = _find_excel7_child_hwnd(hwnd) if hwnd else None

        if excel7_hwnd:
            try:
                iid = (ctypes.c_byte * 16)(
                    0x00, 0x04, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00,
                    0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46,
                )
                p_acc = ctypes.c_void_p()
                hr = ctypes.oledll.oleacc.AccessibleObjectFromWindow(
                    excel7_hwnd, -16, iid, ctypes.byref(p_acc)
                )
                if hr == 0 and p_acc.value:
                    pycom_dll = ctypes.PyDLL(pythoncom.__file__)
                    pycom_func = pycom_dll.PyCom_PyObjectFromIUnknown
                    pycom_func.restype = ctypes.py_object
                    pycom_func.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int)
                    py_dispatch = pycom_func(p_acc.value, ctypes.byref(iid), 0)
                    if py_dispatch:
                        disp = win32com.client.Dispatch(py_dispatch)
                        app = getattr(disp, "Application", disp)
                        if app:
                            return app
            except Exception:
                pass

        try:
            excel = win32com.client.GetActiveObject("Excel.Application")
            if excel:
                actual_title = ""
                if hwnd:
                    try:
                        actual_title = win32gui.GetWindowText(hwnd)
                    except Exception:
                        pass
                target_search = actual_title if actual_title else expected_title
                # Why: 操作対象ウィンドウの実タイトルから一致するWorkbookを特定して誤爆防止
                if target_search:
                    for wb in excel.Workbooks:
                        if wb.Name.lower() in target_search.lower():
                            wb.Activate()
                            return excel
                return excel
        except Exception:
            pass

    except Exception:
        pass
    return None
