"""定位《竞拍之王》游戏窗口并截取画面（安全方式，不触发反作弊）。

实现细节:
- 用 EnumWindows 枚举所有顶层窗口，按窗口标题 + 进程 exe 路径双重匹配。
  避开"游戏 exe 名 = 项目 exe 名 = BidKing.exe"的歧义。
- **不**用 PrintWindow（向游戏发 WM_PRINT 消息，可被反作弊 hook）。
- 改用 PIL.ImageGrab.grab() 从桌面 DWM 合成结果读图，
  与系统截图工具 Win+Shift+S 同源，游戏完全感知不到。
- 为保证游戏不被遮挡，截图前先把游戏窗口拉到前台，截完恢复原前台窗口。
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from pathlib import Path

import win32con
import win32gui
import win32process
from PIL import Image, ImageGrab


# Steam 游戏目录的特征字符串，用来识别游戏进程而非本 GUI
_STEAM_PATH_HINT = "steamapps\\common\\bidking"

# 窗口标题候选 (大小写不敏感，子串匹配)
_TITLE_HINTS = ("竞拍之王", "bidking")

# 切到前台后等待 DWM 合成的时间
_FOCUS_WAIT_SECONDS = 0.18

# Alt 键虚拟键码，用于绕过 SetForegroundWindow 的焦点拦截
_VK_MENU = 0x12
_KEYEVENTF_KEYUP = 0x0002


class GameWindowNotFound(RuntimeError):
    """找不到游戏窗口。"""


class CaptureFailed(RuntimeError):
    """截图过程失败。"""


def _process_exe_path(hwnd: int) -> str:
    """返回窗口所属进程的 exe 完整路径，小写化方便比对。"""
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
    except Exception:
        return ""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return buf.value.lower()
    finally:
        kernel32.CloseHandle(handle)


def _find_game_hwnd() -> int:
    """枚举顶层窗口，挑出符合"标题 + Steam 路径"的那个。

    优先级:
      1) 标题含 "竞拍之王" 或 "bidking" 且 exe 在 Steam 游戏目录下  → 强匹配
      2) 仅 exe 在 Steam 游戏目录下                              → 次匹配
    """
    strong: list[int] = []
    weak: list[int] = []

    def _enum(hwnd: int, _arg: object) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd) or ""
        title_low = title.lower()
        exe_path = _process_exe_path(hwnd)
        in_steam_dir = _STEAM_PATH_HINT in exe_path
        title_hit = any(hint.lower() in title_low for hint in _TITLE_HINTS)
        if in_steam_dir and title_hit:
            strong.append(hwnd)
        elif in_steam_dir:
            weak.append(hwnd)
        return True

    win32gui.EnumWindows(_enum, None)
    if strong:
        return strong[0]
    if weak:
        return weak[0]
    raise GameWindowNotFound("未找到《竞拍之王》游戏窗口，请确认游戏已打开")


def _bring_to_front(hwnd: int) -> None:
    """把窗口拉到前台。

    Windows 10+ 的 SetForegroundWindow 有焦点拦截，
    模拟一次 Alt 按下/抬起绕过限制。
    """
    user32 = ctypes.windll.user32
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    # Alt 键按下/抬起 trick (绕过 SetForegroundWindow 的焦点偷取拦截)
    user32.keybd_event(_VK_MENU, 0, 0, 0)
    user32.keybd_event(_VK_MENU, 0, _KEYEVENTF_KEYUP, 0)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        # 即使失败也继续 —— 可能窗口已经在前台
        pass


def capture_game_window() -> Image.Image:
    """安全截取游戏窗口当前画面，返回 RGB PIL Image。

    工作流:
      1) 记下当前前台窗口（通常是 BidKing GUI）
      2) 把游戏窗口拉到前台
      3) 等 DWM 合成
      4) 从桌面 DC 截游戏窗口矩形那一块
      5) 把原前台窗口拉回来

    Raises:
        GameWindowNotFound: 没找到游戏窗口
        CaptureFailed: 窗口矩形异常或 ImageGrab 返回空
    """
    hwnd = _find_game_hwnd()
    rect = win32gui.GetWindowRect(hwnd)
    left, top, right, bottom = rect
    if right - left <= 0 or bottom - top <= 0:
        raise CaptureFailed(f"游戏窗口尺寸异常: {right - left}x{bottom - top}")

    prev_foreground = win32gui.GetForegroundWindow()

    img: Image.Image | None = None
    try:
        _bring_to_front(hwnd)
        time.sleep(_FOCUS_WAIT_SECONDS)
        # 从桌面合成层抓图。all_screens=True 兼容多显示器
        img = ImageGrab.grab(bbox=rect, all_screens=True)
    finally:
        # 把原来的窗口拉回来（通常是 BidKing GUI）
        if prev_foreground and prev_foreground != hwnd:
            try:
                _bring_to_front(prev_foreground)
            except Exception:
                pass

    if img is None:
        raise CaptureFailed("ImageGrab 返回空图")
    return img.convert("RGB")


def save_image(img: Image.Image, dest: str | Path) -> Path:
    """把 PIL Image 存成 PNG，返回保存路径。"""
    p = Path(dest)
    p.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(p), "PNG")
    return p
