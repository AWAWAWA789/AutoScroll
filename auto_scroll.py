#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
网页自动滚动器 v3.0
Windows Browser Auto Scroll
可调参数 · 鼠标标定 · 翻页动画
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
import queue
import ctypes
import sys

try:
    import pyautogui
    pyautogui.FAILSAFE = True
except ImportError:
    pyautogui = None



# ============================================================
# Constants
# ============================================================
DEFAULT_SCROLL_INIT = 1000
DEFAULT_SCROLL_RUN = 500
DEFAULT_INIT_INTERVAL = 0.1
DEFAULT_RUN_INTERVAL = 15.0
MAX_CYCLES = 500
POLL_MS = 200
STARTUP_DELAY = 3
MIN_SCROLLS_FOR_CLICK = 2
CORNER_MARGIN = 10
ANIM_STEPS = 15
ANIM_MS = 16
ESCAPE_VK = 0x1B

PHASE_IDLE = "IDLE"
PHASE_INIT_DOWN = "INIT_DOWN"
PHASE_INIT_UP = "INIT_UP"
PHASE_RUN_DOWN = "RUN_DOWN"
PHASE_BOTTOM_WAIT = "BOTTOM_WAIT"
PHASE_RUN_UP = "RUN_UP"
PHASE_TOP_WAIT = "TOP_WAIT"
PHASE_STOPPED = "STOPPED"

PHASE_COLORS = {
    PHASE_IDLE: "#7F8C8D",
    PHASE_INIT_DOWN: "#27AE60",
    PHASE_INIT_UP: "#2980B9",
    PHASE_RUN_DOWN: "#27AE60",
    PHASE_BOTTOM_WAIT: "#E67E22",
    PHASE_RUN_UP: "#2980B9",
    PHASE_TOP_WAIT: "#E67E22",
    PHASE_STOPPED: "#E74C3C",
}

PHASE_LABELS = {
    PHASE_IDLE: "空闲",
    PHASE_INIT_DOWN: "向下标定",
    PHASE_INIT_UP: "向上标定",
    PHASE_RUN_DOWN: "向下滚动",
    PHASE_BOTTOM_WAIT: "到底等待",
    PHASE_RUN_UP: "向上滚动",
    PHASE_TOP_WAIT: "到顶等待",
    PHASE_STOPPED: "已停止",
}


# ============================================================
# ScrollState
# ============================================================
class ScrollState:
    def __init__(self):
        self.lock = threading.Lock()
        self.phase = PHASE_IDLE
        self.bottom_cycles = 0
        self.top_cycles = 0
        self.cycle_counter = 0
        self.wait_counter = 0
        self.running = False
        self.calibrated = False
        self.paused = False
        self.focus_lost = False
        self.emergency_stop = False
        self.total_loops = 0
        self.run_start_time = 0.0
        self.status_message = ""
        self.run_bottom_target = 0
        self.run_top_target = 0
        self._prev_mouse_down = False
        self.scroll_init = DEFAULT_SCROLL_INIT
        self.scroll_run = DEFAULT_SCROLL_RUN
        self.init_interval = DEFAULT_INIT_INTERVAL
        self.run_interval = DEFAULT_RUN_INTERVAL

    def snapshot(self):
        with self.lock:
            return {
                "phase": self.phase,
                "bottom_cycles": self.bottom_cycles,
                "top_cycles": self.top_cycles,
                "cycle_counter": self.cycle_counter,
                "wait_counter": self.wait_counter,
                "running": self.running,
                "calibrated": self.calibrated,
                "paused": self.paused,
                "focus_lost": self.focus_lost,
                "emergency_stop": self.emergency_stop,
                "total_loops": self.total_loops,
                "run_start_time": self.run_start_time,
                "status_message": self.status_message,
                "scroll_init": self.scroll_init,
                "scroll_run": self.scroll_run,
                "init_interval": self.init_interval,
                "run_interval": self.run_interval,
                "run_bottom_target": self.run_bottom_target,
                "run_top_target": self.run_top_target,
            }


# ============================================================
# Utilities
# ============================================================
def ensure_single_instance():
    try:
        name = "Global\\AutoScrollApp_SingleInstance_9527"
        ctypes.windll.kernel32.CreateMutexW(None, False, name)
        if ctypes.windll.kernel32.GetLastError() == 183:
            return False
        return True
    except Exception:
        return True


def get_active_window_title():
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value or ""
    except Exception:
        return ""


def is_browser_active():
    title = get_active_window_title().lower()
    keywords = ["chrome", "edge", "firefox", "opera", "brave", "browser",
                "iexplore", "chromium", "vivaldi", "torch", "yandex"]
    return any(k in title for k in keywords)


def is_escape_pressed():
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(ESCAPE_VK) & 0x8000)
    except Exception:
        return False


def check_left_mouse_click(state):
    try:
        now_down = bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)
        clicked = now_down and not state._prev_mouse_down
        state._prev_mouse_down = now_down
        return clicked
    except Exception:
        return False


def _sleep_interval(seconds, state):
    chunk = 0.05 if seconds < 0.25 else 0.25
    chunks = max(1, round(seconds / chunk))
    for _ in range(chunks):
        with state.lock:
            if not state.running or state.emergency_stop:
                return False
        time.sleep(chunk)
    return True


# ============================================================
# AutoScrollApp
# ============================================================
class AutoScrollApp:
    def __init__(self):
        self.state = ScrollState()
        self.gui_queue = queue.Queue()
        self.worker_thread = None
        self.safety_thread = None
        self.safety_started = False
        self.worker_id = 0

    def _ensure_safety_monitor(self):
        if self.safety_started:
            return
        self.safety_started = True
        self.safety_thread = threading.Thread(target=self._safety_loop, daemon=True)
        self.safety_thread.start()

    def _safety_loop(self):
        try:
            screen_w, screen_h = pyautogui.size()
        except Exception:
            screen_w = screen_h = 0
        while True:
            with self.state.lock:
                if self.state.emergency_stop:
                    break
            try:
                x, y = pyautogui.position()
                at_corner = screen_w > 0 and (
                    (x <= CORNER_MARGIN and y <= CORNER_MARGIN)
                    or (x >= screen_w - CORNER_MARGIN and y <= CORNER_MARGIN)
                    or (x <= CORNER_MARGIN and y >= screen_h - CORNER_MARGIN)
                    or (x >= screen_w - CORNER_MARGIN and y >= screen_h - CORNER_MARGIN)
                )
                if at_corner or is_escape_pressed():
                    self._emergency_stop()
                    break
            except Exception:
                pass
            time.sleep(0.15)

    def _start_worker(self):
        self.stop_worker()
        self.worker_id += 1
        wid = self.worker_id
        with self.state.lock:
            self.state.running = True
        t = threading.Thread(target=self._worker_loop, args=(wid,), daemon=True)
        t.start()
        self.worker_thread = t

    def stop_worker(self):
        with self.state.lock:
            self.state.running = False

    def start_calibration(self, scroll_init=None, init_interval=None):
        self._ensure_safety_monitor()
        check_left_mouse_click(self.state)
        self._start_worker()
        si = scroll_init or DEFAULT_SCROLL_INIT
        ii = init_interval if init_interval is not None else DEFAULT_INIT_INTERVAL
        with self.state.lock:
            s = self.state
            s.scroll_init = si
            s.init_interval = ii
            s.phase = PHASE_INIT_DOWN
            s.cycle_counter = 0
            s.bottom_cycles = 0
            s.top_cycles = 0
            s.calibrated = False
            s.paused = False
            s.focus_lost = False
            s.emergency_stop = False
            s.status_message = f"请切换至浏览器窗口... ({STARTUP_DELAY}秒)"

    def start_running(self, scroll_run=None, run_interval=None, wait_interval=None,
                      custom_down=0, custom_up=0):
        self._ensure_safety_monitor()
        sr = scroll_run or DEFAULT_SCROLL_RUN
        ri = run_interval or DEFAULT_RUN_INTERVAL
        wi = wait_interval or ri
        with self.state.lock:
            s = self.state
            s.scroll_run = sr
            s.init_interval = DEFAULT_INIT_INTERVAL
            s.run_interval = ri
            if custom_down > 0 and custom_up > 0:
                s.run_bottom_target = custom_down
                s.run_top_target = custom_up
            else:
                ratio = s.scroll_init / s.scroll_run
                s.run_bottom_target = max(1, round(s.bottom_cycles * ratio))
                s.run_top_target = max(1, round(s.top_cycles * ratio))
            s.phase = PHASE_RUN_DOWN
            s.cycle_counter = 0
            s.wait_counter = 0
            s.total_loops = 0
            s.paused = False
            s.focus_lost = False
            s.emergency_stop = False
            s.run_start_time = time.time()
        self._start_worker()

    def stop_all(self):
        self.stop_worker()
        with self.state.lock:
            self.state.phase = PHASE_STOPPED
            self.state.running = False
            self.state.paused = False

    def _emergency_stop(self):
        self.stop_worker()
        with self.state.lock:
            self.state.emergency_stop = True
            self.state.running = False
            self.state.phase = PHASE_STOPPED
        self.gui_queue.put(("emergency_stop", None))

    def _show_warning(self, msg):
        self.gui_queue.put(("warning", msg))

    def _worker_loop(self, wid):
        delay_done = False

        while True:
            with self.state.lock:
                if not self.state.running or self.state.emergency_stop or wid != self.worker_id:
                    break
                phase = self.state.phase
                si = self.state.scroll_init
                sr = self.state.scroll_run
                ri = self.state.run_interval
                ini = self.state.init_interval

            if phase in (PHASE_INIT_DOWN, PHASE_INIT_UP) and not delay_done:
                for i in range(STARTUP_DELAY, 0, -1):
                    with self.state.lock:
                        if not self.state.running or self.state.emergency_stop:
                            return
                        self.state.status_message = f"请切换至浏览器窗口... ({i}秒)"
                    check_left_mouse_click(self.state)
                    time.sleep(1)
                delay_done = True
                with self.state.lock:
                    if self.state.phase in (PHASE_INIT_DOWN, PHASE_INIT_UP):
                        self.state.status_message = ""
                continue

            if phase == PHASE_INIT_DOWN:
                try:
                    if pyautogui:
                        pyautogui.scroll(-si)
                except pyautogui.FailSafeException:
                    self._emergency_stop()
                    break
                with self.state.lock:
                    self.state.cycle_counter += 1
                    if self.state.cycle_counter >= MAX_CYCLES:
                        self.state.cycle_counter = 0
                        self._show_warning("已达最大标定次数，请点击鼠标左键确认底部")

                if self.state.cycle_counter >= MIN_SCROLLS_FOR_CLICK and check_left_mouse_click(self.state):
                    with self.state.lock:
                        self.state.bottom_cycles = self.state.cycle_counter
                        self.state.phase = PHASE_INIT_UP
                        self.state.cycle_counter = 0
                    continue

                if not _sleep_interval(ini, self.state):
                    break

            elif phase == PHASE_INIT_UP:
                try:
                    if pyautogui:
                        pyautogui.scroll(si)
                except pyautogui.FailSafeException:
                    self._emergency_stop()
                    break
                with self.state.lock:
                    self.state.cycle_counter += 1
                    bc = self.state.bottom_cycles
                    if self.state.cycle_counter >= bc:
                        self.state.phase = PHASE_IDLE
                        self.state.calibrated = True
                        self.state.top_cycles = self.state.cycle_counter
                if not _sleep_interval(ini, self.state):
                    break

            elif phase == PHASE_RUN_DOWN:
                with self.state.lock:
                    if self.state.paused:
                        if not _sleep_interval(0.5, self.state):
                            break
                        continue
                if not is_browser_active():
                    with self.state.lock:
                        self.state.focus_lost = True
                    if not _sleep_interval(0.5, self.state):
                        break
                    continue
                with self.state.lock:
                    self.state.focus_lost = False
                try:
                    if pyautogui:
                        pyautogui.scroll(-sr)
                except pyautogui.FailSafeException:
                    self._emergency_stop()
                    break
                with self.state.lock:
                    self.state.cycle_counter += 1
                    if self.state.cycle_counter >= self.state.run_bottom_target:
                        self.state.phase = PHASE_BOTTOM_WAIT
                        self.state.wait_counter = int(ri)
                if not _sleep_interval(ri, self.state):
                    break

            elif phase == PHASE_BOTTOM_WAIT:
                with self.state.lock:
                    self.state.wait_counter -= 1
                    if self.state.wait_counter <= 0:
                        self.state.phase = PHASE_RUN_UP
                        self.state.cycle_counter = 0
                if not _sleep_interval(1.0, self.state):
                    break

            elif phase == PHASE_RUN_UP:
                with self.state.lock:
                    if self.state.paused:
                        if not _sleep_interval(0.5, self.state):
                            break
                        continue
                if not is_browser_active():
                    with self.state.lock:
                        self.state.focus_lost = True
                    if not _sleep_interval(0.5, self.state):
                        break
                    continue
                with self.state.lock:
                    self.state.focus_lost = False
                try:
                    if pyautogui:
                        pyautogui.scroll(sr)
                except pyautogui.FailSafeException:
                    self._emergency_stop()
                    break
                with self.state.lock:
                    self.state.cycle_counter += 1
                    if self.state.cycle_counter >= self.state.run_top_target:
                        self.state.phase = PHASE_TOP_WAIT
                        self.state.wait_counter = int(ri)
                if not _sleep_interval(ri, self.state):
                    break

            elif phase == PHASE_TOP_WAIT:
                with self.state.lock:
                    self.state.wait_counter -= 1
                    if self.state.wait_counter <= 0:
                        self.state.total_loops += 1
                        self.state.phase = PHASE_RUN_DOWN
                        self.state.cycle_counter = 0
                if not _sleep_interval(1.0, self.state):
                    break

            else:
                if not _sleep_interval(0.3, self.state):
                    break


# ============================================================
# PageContainer with Slide Animation
# ============================================================
class PageContainer(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=BG_BODY)
        self.app = app
        self.current_page = None
        self.pages = {}
        self.animating = False
        self._pending = None

    def register(self, name, cls):
        self.pages[name] = cls

    def show(self, name, forward=True, **kwargs):
        if self.animating:
            self._pending = (name, forward, kwargs)
            return
        if name not in self.pages:
            return

        cls = self.pages[name]
        new_page = cls(self, self.app, **kwargs)

        cw = self.winfo_width()
        ch = self.winfo_height()
        if cw < 10:
            cw = 600
        if ch < 10:
            ch = 460

        if self.current_page is None:
            new_page.place(x=0, y=0, width=cw, height=ch)
            self.current_page = new_page
            return

        direction = 1 if forward else -1
        new_page.place(x=direction * cw, y=0, width=cw, height=ch)
        new_page.lower()

        self.animating = True
        self._anim_step = 0
        self._anim_steps = ANIM_STEPS
        self._anim_old = self.current_page
        self._anim_new = new_page
        self._anim_cw = cw
        self._anim_dir = direction
        self._do_animation()

    def _do_animation(self):
        self._anim_step += 1
        progress = self._anim_step / self._anim_steps
        eased = 1 - (1 - progress) ** 3

        old_target = -self._anim_dir * self._anim_cw
        old_x = int(eased * old_target)
        new_start = self._anim_dir * self._anim_cw
        new_x = new_start - int(eased * new_start)

        if self._anim_old:
            self._anim_old.place_configure(x=old_x)
        self._anim_new.place_configure(x=new_x)

        if self._anim_step < self._anim_steps:
            self.after(ANIM_MS, self._do_animation)
        else:
            if self._anim_old:
                self._anim_old.destroy()
            self._anim_new.lift()
            self.current_page = self._anim_new
            self.animating = False
            if self._pending:
                n, f, kw = self._pending
                self._pending = None
                self.show(n, forward=f, **kw)


# ============================================================
# GUI Helpers
# ============================================================
FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_TITLE = ("Segoe UI", 15, "bold")
FONT_LARGE = ("Segoe UI", 12, "bold")
FONT_SMALL = ("Segoe UI", 9)

ACCENT = "#3498DB"
BG_BODY = "#F0F2F5"
CARD_BG = "#FFFFFF"
TEXT_DARK = "#1A1A2E"
TEXT_MUTED = "#6C757D"
TEXT_HINT = "#ADB5BD"


def make_card(parent, title=None, **kwargs):
    pad = kwargs.pop("padx_inner", 16)
    f = tk.Frame(parent, bg=CARD_BG, highlightbackground="#E9ECEF",
                 highlightthickness=1, **kwargs)
    tk.Frame(f, bg=ACCENT, height=3).pack(fill="x")
    inner = tk.Frame(f, bg=CARD_BG)
    inner.pack(fill="both", expand=True, padx=pad, pady=(6, 10))
    if title:
        tk.Label(inner, text=title, bg=CARD_BG, fg=TEXT_DARK,
                 font=FONT_BOLD).pack(anchor="w")
    return f, inner


def make_step_bar(parent, steps, active_idx):
    f = tk.Frame(parent, bg="white", height=42)
    f.pack(fill="x")
    f.pack_propagate(False)
    inner = tk.Frame(f, bg="white")
    inner.pack(expand=True)

    for i, (txt, done_c, active_c) in enumerate(steps):
        if i > 0:
            fg = done_c if i <= active_idx else "#DEE2E6"
            tk.Label(inner, text="━━━", bg="white", fg=fg,
                     font=("Segoe UI", 8)).pack(side="left", padx=4)
        if i < active_idx:
            bg_pill = done_c
            txt_display = f"  {txt}  "
        elif i == active_idx:
            bg_pill = active_c
            txt_display = f"  {txt}  "
        else:
            bg_pill = "#E9ECEF"
            txt_display = f"  {txt}  "

        lbl = tk.Label(inner, text=txt_display, bg=bg_pill, fg="white" if i <= active_idx else TEXT_MUTED,
                       font=("Segoe UI", 9, "bold"), padx=10, pady=2)
        lbl.pack(side="left")
    return f


def make_btn(parent, text, color, command, **kw):
    wide = kw.pop("wide", False)
    active = kw.pop("active", None) or color
    btn = tk.Button(parent, text=text, font=("Segoe UI", 10, "bold"),
                    bg=color, fg="white", relief="flat",
                    activebackground=active, activeforeground="white",
                    cursor="hand2", padx=22 if wide else 16, pady=8, bd=0,
                    command=command, **kw)
    return btn


# ============================================================
# Home Page
# ============================================================
class HomePage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=BG_BODY)
        self.app = app
        self._build_ui()
        self._poll()

    def _build_ui(self):
        hf = tk.Frame(self, bg="white", height=56)
        hf.pack(fill="x")
        hf.pack_propagate(False)
        tk.Label(hf, text="网页自动滚动器", fg=TEXT_DARK, bg="white",
                 font=FONT_TITLE).pack(expand=True)

        body = tk.Frame(self, bg=BG_BODY)
        body.pack(fill="both", expand=True, padx=20, pady=(10, 6))

        card_s, inner_s = make_card(body, title="参数设置")
        card_s.pack(fill="x", pady=(0, 6))

        tk.Label(inner_s, text="标定：速度 1000 行/次 | 间隔 0.1 秒（固定不可调）",
                 bg=CARD_BG, fg=TEXT_HINT, font=("Segoe UI", 8)).pack(anchor="w", pady=(0, 6))

        sub = tk.Frame(inner_s, bg="#F8F9FA", highlightbackground="#E9ECEF",
                       highlightthickness=1)
        sub.pack(fill="x", ipady=2)
        tk.Label(sub, text="运行参数", bg="#F8F9FA", fg=TEXT_DARK,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=10, pady=(4, 0))
        g = tk.Frame(sub, bg="#F8F9FA")
        g.pack(fill="x", padx=10, pady=(2, 6))

        self.entries = {}
        for key, label, default, unit in [
            ("run_spd", "运行速度", f"{DEFAULT_SCROLL_RUN}", "行/次"),
            ("interval", "滚动间隔", f"{DEFAULT_RUN_INTERVAL:.0f}", "秒"),
        ]:
            f = tk.Frame(g, bg="#F8F9FA")
            f.pack(side="left", expand=True)
            tk.Label(f, text=label, bg="#F8F9FA", fg=TEXT_MUTED,
                     font=("Segoe UI", 9)).pack()
            ef = tk.Frame(f, bg="#F8F9FA")
            ef.pack(pady=(2, 0))
            e = tk.Entry(ef, font=FONT, width=6, justify="center",
                         relief="solid", bd=1)
            e.insert(0, default)
            e.pack(side="left")
            tk.Label(ef, text=unit, bg="#F8F9FA", fg=TEXT_HINT,
                     font=("Segoe UI", 9)).pack(side="left", padx=(3, 0))
            self.entries[key] = e

        tk.Frame(sub, bg="#E9ECEF", height=1).pack(fill="x", padx=10)
        g2 = tk.Frame(sub, bg="#F8F9FA")
        g2.pack(fill="x", padx=10, pady=(2, 6))
        f = tk.Frame(g2, bg="#F8F9FA")
        f.pack(side="left", expand=True, fill="x")
        tk.Label(f, text="滚动次数", bg="#F8F9FA", fg=TEXT_MUTED,
                 font=("Segoe UI", 9)).pack()
        ef = tk.Frame(f, bg="#F8F9FA")
        ef.pack(pady=(2, 0))
        e = tk.Entry(ef, font=FONT, width=6, justify="center",
                     relief="solid", bd=1)
        e.insert(0, "0")
        e.pack(side="left")
        tk.Label(ef, text="次", bg="#F8F9FA", fg=TEXT_HINT,
                 font=("Segoe UI", 9)).pack(side="left", padx=(3, 0))
        self.entries["custom_scrolls"] = e
        f2 = tk.Frame(g2, bg="#F8F9FA")
        f2.pack(side="left", expand=True, fill="x")
        tk.Label(f2, text="", bg="#F8F9FA", font=("Segoe UI", 9)).pack()
        tk.Label(f2, text="（填 0=使用标定值）", bg="#F8F9FA", fg=TEXT_HINT,
                 font=("Segoe UI", 8)).pack(pady=(6, 0))

        card_c, inner_c = make_card(body, title="标定状态")
        card_c.pack(fill="x", pady=(0, 6))

        sf = tk.Frame(inner_c, bg=CARD_BG)
        sf.pack(fill="x", pady=(2, 0))
        self.calib_dot = tk.Canvas(sf, width=16, height=16, bg=CARD_BG,
                                   highlightthickness=0)
        self.calib_dot.pack(side="left")
        self.dot_id = self.calib_dot.create_oval(2, 2, 14, 14, fill="#DEE2E6", outline="")
        self.calib_label = tk.Label(sf, text="未标定", fg=TEXT_MUTED, bg=CARD_BG,
                                    font=FONT_LARGE)
        self.calib_label.pack(side="left", padx=(8, 0))
        self.calib_detail = tk.Label(inner_c, text="", fg=TEXT_HINT, bg=CARD_BG,
                                     font=("Segoe UI", 9))
        self.calib_detail.pack(anchor="w", padx=(20, 0))

        card_st, inner_st = make_card(body, title="快捷键")
        card_st.pack(fill="x", pady=(0, 6))
        tk.Label(inner_st, text="标定中: 鼠标左键确认底部  |  任意时刻: ESC 或 鼠标移到屏幕角落 紧急停止",
                 bg=CARD_BG, fg=TEXT_MUTED, font=("Segoe UI", 9)).pack(anchor="w")

        btn_f = tk.Frame(self, bg=BG_BODY)
        btn_f.pack(fill="x", padx=20, pady=(2, 12))

        self.init_btn = make_btn(btn_f, "开始标定", ACCENT, self._on_init)
        self.init_btn.pack(side="left")

        self.run_btn = make_btn(btn_f, "▶ 开始运行", "#27AE60",
                                self._on_run, state="disabled", disabledforeground="#ADB5BD")
        self.run_btn.pack(side="left", padx=(10, 0))

        make_btn(btn_f, "退出", "#E74C3C", self._on_exit).pack(side="right")

        # Status bar
        self.status_bar = tk.Label(self, text='准备就绪 | 标定或填写自定义次数后点击"开始运行"',
                                   bg="#D4EDDA", fg="#155724",
                                   font=FONT_SMALL, anchor="w", padx=12, pady=4)
        self.status_bar.pack(fill="x", side="bottom")
        self._hide_job = None

    def _get_params(self):
        try:
            sr = int(self.entries["run_spd"].get().strip())
            ri = float(self.entries["interval"].get().strip())
            if sr < 10 or ri < 1:
                raise ValueError
            return sr, ri
        except Exception:
            self._show_status("参数无效，速度>=10，间隔>=1", True)
            return None

    def _poll(self):
        snap = self.app.state.snapshot()
        cal = snap["calibrated"]
        try:
            cs = int(self.entries["custom_scrolls"].get().strip() or 0)
            has_custom = cs > 0
        except Exception:
            has_custom = False

        if cal:
            self.calib_dot.itemconfig(self.dot_id, fill="#27AE60")
            self.calib_label.config(text="已标定", fg="#27AE60")
            bc = snap["bottom_cycles"]
            tc = snap["top_cycles"]
            ratio = snap["scroll_init"] / max(snap["scroll_run"], 1)
            self.calib_detail.config(
                text=f"底部: {bc} 次  |  顶部: {tc} 次  |  运行等效: {round(bc*ratio)} / {round(tc*ratio)} 次")
            self.run_btn.config(state="normal")
        elif has_custom:
            self.calib_dot.itemconfig(self.dot_id, fill="#3498DB")
            self.calib_label.config(text="手动模式", fg="#3498DB")
            self.calib_detail.config(text=f"使用自定义次数：每轮 {cs} 次（向下+向上）")
            self.run_btn.config(state="normal")
        else:
            self.calib_dot.itemconfig(self.dot_id, fill="#BDC3C7")
            self.calib_label.config(text="未标定", fg="#7F8C8D")
            self.calib_detail.config(text="")
            self.run_btn.config(state="disabled")

        try:
            while True:
                evt = self.app.gui_queue.get_nowait()
                typ = evt[0]
                if typ in ("warning", "error"):
                    self._show_status(evt[1], typ == "error")
                elif typ == "emergency_stop":
                    self._show_status("紧急停止 (ESC/鼠标角落)", True)
        except queue.Empty:
            pass

        self.after(POLL_MS, self._poll)

    def _show_status(self, msg, is_error=False):
        if self._hide_job:
            self.after_cancel(self._hide_job)
        if is_error:
            self.status_bar.config(text=f"⚠ {msg}", bg="#F8D7DA", fg="#721C24")
        else:
            self.status_bar.config(text=msg, bg="#FFF3CD", fg="#856404")
        self._hide_job = self.after(10000, lambda: self.status_bar.config(text="", bg="#FFF3CD", fg="#856404"))

    def _on_init(self):
        if pyautogui is None:
            messagebox.showerror("错误", "pyautogui 未安装")
            return
        if self.app.state.running:
            return
        self.app.stop_all()
        self.app.start_calibration()
        self.after(50, lambda: self.master.show("init", forward=True))

    def _on_run(self):
        if pyautogui is None:
            messagebox.showerror("错误", "pyautogui 未安装")
            return
        params = self._get_params()
        if params is None:
            return
        sr, ri = params
        try:
            cs = int(self.entries["custom_scrolls"].get().strip() or 0)
        except Exception:
            cs = 0
        has_custom = cs > 0
        if not has_custom and not self.app.state.calibrated:
            self._show_status("请先进行标定，或填写自定义次数", True)
            return
        if self.app.state.running:
            return
        self.app.start_running(scroll_run=sr, run_interval=ri, wait_interval=ri,
                               custom_down=cs, custom_up=cs)
        self.master.show("run", forward=True)

    def _on_exit(self):
        self.app.stop_all()
        self.master.master.destroy()
        sys.exit(0)


# ============================================================
# Init Page
# ============================================================
class InitPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=BG_BODY)
        self.app = app
        self._build_ui()
        self._poll()

    def _build_ui(self):
        hf = tk.Frame(self, bg="white", height=56)
        hf.pack(fill="x")
        hf.pack_propagate(False)
        make_btn(hf, "< 返回", "#6C757D", self._on_back).place(x=8, y=12)
        tk.Label(hf, text="初始化标定", fg=TEXT_DARK, bg="white",
                 font=FONT_TITLE).pack(expand=True)

        body = tk.Frame(self, bg=BG_BODY)
        body.pack(fill="both", expand=True, padx=20, pady=(10, 6))

        # Steps card
        card_st, inner_st = make_card(body)
        card_st.pack(fill="x", pady=(0, 6))
        tk.Label(inner_st, text="操作步骤", bg=CARD_BG, fg=TEXT_DARK,
                 font=FONT_BOLD).pack(anchor="w")
        self.step1_lb = tk.Label(inner_st,
            text="1. 请切换到浏览器  →  页面快速向下滚动  →  到底后轻点鼠标左键",
            bg=CARD_BG, fg=TEXT_MUTED, font=FONT)
        self.step1_lb.pack(anchor="w", pady=(4, 1))
        self.step2_lb = tk.Label(inner_st,
            text="2. 页面自动向上滚回顶部  →  标定完成",
            bg=CARD_BG, fg=TEXT_MUTED, font=FONT)
        self.step2_lb.pack(anchor="w", pady=(1, 6))
        self.step1_done = tk.Label(inner_st, text="", bg=CARD_BG, fg="#27AE60", font=FONT)
        self.step1_done.pack(anchor="w")

        # Status card
        card_st2, inner_st2 = make_card(body)
        card_st2.pack(fill="x", pady=(0, 6))
        tk.Label(inner_st2, text="标定状态", bg=CARD_BG, fg=TEXT_DARK,
                 font=FONT_BOLD).pack(anchor="w")

        sf = tk.Frame(inner_st2, bg=CARD_BG)
        sf.pack(fill="x", pady=(4, 2))
        self.phase_dot = tk.Canvas(sf, width=14, height=14, bg=CARD_BG, highlightthickness=0)
        self.phase_dot.pack(side="left")
        self.phase_dot_id = self.phase_dot.create_oval(1, 1, 13, 13, fill="#BDC3C7", outline="")
        self.phase_label = tk.Label(sf, text="准备中...", bg=CARD_BG, fg=TEXT_MUTED,
                                    font=FONT_LARGE)
        self.phase_label.pack(side="left", padx=(8, 0))

        self.msg_label = tk.Label(inner_st2, text="", bg=CARD_BG, fg="#E67E22",
                                  font=FONT_SMALL)
        self.msg_label.pack(anchor="w")

        cf = tk.Frame(inner_st2, bg=CARD_BG)
        cf.pack(fill="x", pady=(4, 0))
        self.counter_label = tk.Label(cf, text="0 次", bg=CARD_BG, fg=TEXT_DARK,
                                      font=("Segoe UI", 22, "bold"))
        self.counter_label.pack(side="left")
        tk.Label(cf, text="已滚动", bg=CARD_BG, fg=TEXT_HINT,
                 font=FONT_SMALL).pack(side="left", padx=(6, 0), pady=(6, 0))

        self.progress = ttk.Progressbar(inner_st2, mode="indeterminate", length=480)
        self.progress.pack(fill="x", pady=(8, 2))

        btn_f = tk.Frame(self, bg=BG_BODY)
        btn_f.pack(fill="x", padx=20, pady=(4, 12))

        make_btn(btn_f, "退出标定", "#6C757D", self._on_cancel).pack(side="left")

        self.finish_btn = make_btn(btn_f, "完成标定", "#BDC3C7",
                                   self._on_finish, active="#BDC3C7",
                                   state="disabled", disabledforeground="white")
        self.finish_btn.pack(side="right", padx=4)

    def _set_dot(self, color):
        self.phase_dot.itemconfig(self.phase_dot_id, fill=color)

    def _poll(self):
        snap = self.app.state.snapshot()
        phase = snap["phase"]
        cal = snap["calibrated"]

        if phase == PHASE_INIT_DOWN:
            self._set_dot(PHASE_COLORS[PHASE_INIT_DOWN])
            self.phase_label.config(text="↓ 向下滚动 — 到底请点击鼠标左键", fg=PHASE_COLORS[PHASE_INIT_DOWN])
            self.step1_lb.config(fg="#2C3E50")
            self.step2_lb.config(fg="#BDC3C7")
            self.step1_done.config(text="")
        elif phase == PHASE_INIT_UP:
            self._set_dot(PHASE_COLORS[PHASE_INIT_UP])
            self.phase_label.config(text="↑ 向上滚回 — 请等待", fg=PHASE_COLORS[PHASE_INIT_UP])
            self.step1_lb.config(fg="#27AE60")
            self.step2_lb.config(fg="#2C3E50")
            self.step1_done.config(text="✔ 底部已确认")
        elif cal:
            self._set_dot("#27AE60")
            self.phase_label.config(text="✔ 标定完成", fg="#27AE60")
            self.step1_lb.config(fg="#27AE60")
            self.step2_lb.config(fg="#27AE60")
            self.step2_lb.config(text="2. 滚回顶部  ✔")
            self.step1_done.config(text="✔ 底部已确认")
            self.finish_btn.config(bg="#27AE60", activebackground="#1E8449", state="normal")
            self.progress.stop()
        else:
            self._set_dot("#E74C3C")
            self.phase_label.config(text="标定中断", fg="#E74C3C")

        self.msg_label.config(text=snap["status_message"])

        txt = f"{snap['cycle_counter']}"
        if snap["bottom_cycles"] > 0:
            txt += f"  (底部: {snap['bottom_cycles']})"
        self.counter_label.config(text=txt)

        self.after(POLL_MS, self._poll)

    def _go_home(self):
        self.app.stop_all()
        self.progress.stop()
        self.master.show("home", forward=False)

    def _on_back(self):
        self._go_home()

    def _on_cancel(self):
        self._go_home()

    def _on_finish(self):
        self._go_home()


# ============================================================
# Run Page
# ============================================================
class RunPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=BG_BODY)
        self.app = app
        self._build_ui()
        self._poll()

    def _build_ui(self):
        hf = tk.Frame(self, bg="white", height=56)
        hf.pack(fill="x")
        hf.pack_propagate(False)
        make_btn(hf, "停止", "#E74C3C", self._on_stop).place(x=8, y=12)
        tk.Label(hf, text="运行监控", fg=TEXT_DARK, bg="white",
                 font=FONT_TITLE).pack(expand=True)

        body = tk.Frame(self, bg=BG_BODY)
        body.pack(fill="both", expand=True, padx=20, pady=(10, 6))

        card_s, inner_s = make_card(body, title="运行状态")
        card_s.pack(fill="x", pady=(0, 6))

        sf = tk.Frame(inner_s, bg=CARD_BG)
        sf.pack(fill="x", pady=(2, 0))
        self.status_dot = tk.Canvas(sf, width=18, height=18, bg=CARD_BG,
                                    highlightthickness=0)
        self.status_dot.pack(side="left")
        self.status_dot_id = self.status_dot.create_oval(2, 2, 16, 16, fill="#27AE60", outline="")
        self.status_label = tk.Label(sf, text="运行中", bg=CARD_BG, fg="#27AE60",
                                     font=("Segoe UI", 14, "bold"))
        self.status_label.pack(side="left", padx=(8, 0))
        self.focus_label = tk.Label(inner_s, text="", bg=CARD_BG, fg="#E67E22",
                                    font=("Segoe UI", 9))
        self.focus_label.pack(anchor="w")

        card_p, inner_p = make_card(body, title="进度信息")
        card_p.pack(fill="x", pady=(0, 6))

        pf = tk.Frame(inner_p, bg=CARD_BG)
        pf.pack(fill="x", pady=(2, 0))
        self.phase_dot = tk.Canvas(pf, width=14, height=14, bg=CARD_BG, highlightthickness=0)
        self.phase_dot.pack(side="left")
        self.phase_dot_id = self.phase_dot.create_oval(1, 1, 13, 13, fill="#27AE60", outline="")
        self.phase_label = tk.Label(pf, text="↓ 向下滚动", bg=CARD_BG, fg="#27AE60",
                                    font=FONT_LARGE)
        self.phase_label.pack(side="left", padx=(8, 0))

        pf2 = tk.Frame(inner_p, bg=CARD_BG)
        pf2.pack(fill="x", pady=(6, 0))
        self.progress_text = tk.Label(pf2, text="进度: 0 / 0 次", bg=CARD_BG, fg=TEXT_DARK,
                                      font=FONT_LARGE)
        self.progress_text.pack(side="left")
        self.wait_text = tk.Label(pf2, text="", bg=CARD_BG, fg="#E67E22",
                                  font=FONT_LARGE)
        self.wait_text.pack(side="right")

        info_f = tk.Frame(inner_p, bg=CARD_BG)
        info_f.pack(fill="x", pady=(4, 0))
        self.loops_text = tk.Label(info_f, text="已完成: 0 轮", bg=CARD_BG, fg=TEXT_MUTED,
                                   font=("Segoe UI", 10))
        self.loops_text.pack(side="left")
        self.time_text = tk.Label(info_f, text="时间: 00:00:00", bg=CARD_BG, fg=TEXT_MUTED,
                                  font=("Segoe UI", 10))
        self.time_text.pack(side="right")

        btn_f = tk.Frame(self, bg=BG_BODY)
        btn_f.pack(fill="x", padx=20, pady=(4, 12))

        self.pause_btn = make_btn(btn_f, "暂停", "#F39C12", self._on_pause)
        self.pause_btn.pack(side="left")
        make_btn(btn_f, "停止运行", "#E74C3C", self._on_stop).pack(side="right")

    def _set_status_dot(self, color):
        self.status_dot.itemconfig(self.status_dot_id, fill=color)

    def _set_phase_dot(self, color):
        self.phase_dot.itemconfig(self.phase_dot_id, fill=color)

    def _poll(self):
        snap = self.app.state.snapshot()
        phase = snap["phase"]
        paused = snap["paused"]
        emergency = snap["emergency_stop"]

        if emergency or phase == PHASE_STOPPED or not snap["running"]:
            self._on_stop()
            return

        focus_lost = snap["focus_lost"]
        if paused or focus_lost:
            self._set_status_dot("#F39C12")
            if focus_lost:
                self.status_label.config(text="已暂停 - 浏览器失焦", fg="#F39C12")
                self.focus_label.config(text="切换回浏览器窗口后点击「继续」恢复运行")
            else:
                self.status_label.config(text="已暂停", fg="#F39C12")
                self.focus_label.config(text="")
        else:
            self._set_status_dot("#27AE60")
            self.status_label.config(text="运行中", fg="#27AE60")
            self.focus_label.config(text="")

        color = PHASE_COLORS.get(phase, "#7F8C8D")
        label = PHASE_LABELS.get(phase, phase)
        prefix = {"RUN_DOWN": "↓ ", "BOTTOM_WAIT": "━ ", "RUN_UP": "↑ ", "TOP_WAIT": "━ "}.get(phase, "")
        self._set_phase_dot(color)
        self.phase_label.config(text=f"{prefix}{label}", fg=color)

        loop_num = snap["total_loops"] + 1
        if phase == PHASE_RUN_DOWN:
            self.progress_text.config(
                text=f"↓ 第{loop_num}轮向下: {snap['cycle_counter']} / {snap['run_bottom_target']} 次")
            self.wait_text.config(text="")
        elif phase == PHASE_RUN_UP:
            self.progress_text.config(
                text=f"↑ 第{loop_num}轮向上: {snap['cycle_counter']} / {snap['run_top_target']} 次")
            self.wait_text.config(text="")
        elif phase in (PHASE_BOTTOM_WAIT, PHASE_TOP_WAIT):
            direction = "底" if phase == PHASE_BOTTOM_WAIT else "顶"
            self.progress_text.config(text=f"━ 已到{direction}，保持等待中")
            self.wait_text.config(text=f"剩余 {snap['wait_counter']} 秒")
        else:
            self.progress_text.config(text="")
            self.wait_text.config(text="")

        self.loops_text.config(text=f"已循环: {snap['total_loops']} 轮（无限循环，点击「停止运行」结束）")
        if snap["run_start_time"] > 0:
            elapsed = int(time.time() - snap["run_start_time"])
            h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
            self.time_text.config(text=f"时间: {h:02d}:{m:02d}:{s:02d}")

        self.pause_btn.config(text="继续 >>" if paused else "暂停")
        self.after(POLL_MS, self._poll)

    def _on_pause(self):
        with self.app.state.lock:
            self.app.state.paused = not self.app.state.paused

    def _on_stop(self):
        self.app.stop_all()
        self.master.show("home", forward=False)


# ============================================================
# MainWindow
# ============================================================
class MainWindow:
    def __init__(self, app):
        self.app = app
        self.root = tk.Tk()
        self.root.title("网页自动滚动器 v3.0")
        self.root.geometry("680x760")
        self.root.minsize(620, 680)
        self.root.configure(bg="#ECF0F1")

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

        container = PageContainer(self.root, app)
        container.pack(fill="both", expand=True)
        container.register("home", HomePage)
        container.register("init", InitPage)
        container.register("run", RunPage)
        container.show("home")

        self.root.protocol("WM_DELETE_WINDOW", self._on_exit)
        self.root.bind("<Configure>", self._on_root_resize)

    def _on_root_resize(self, event):
        if event.widget == self.root:
            cw, ch = event.width, event.height
            for child in self.root.winfo_children():
                if isinstance(child, PageContainer) and child.current_page:
                    child.current_page.place_configure(width=cw, height=ch)

    def _on_exit(self):
        self.app.stop_all()
        self.root.destroy()
        sys.exit(0)

    def run(self):
        self.root.mainloop()


# ============================================================
# Entry
# ============================================================
def main():
    print(f"Python: {sys.executable}")
    print(f"pyautogui: {'OK' if pyautogui else 'FAIL'}")

    if not ensure_single_instance():
        messagebox.showerror("程序已运行", "网页自动滚动器已在运行中。\n不允许同时启动多个实例。")
        sys.exit(1)

    if pyautogui is None:
        messagebox.showerror("错误", "pyautogui 未安装！\n请执行: pip install pyautogui")
        sys.exit(1)

    app = AutoScrollApp()
    mw = MainWindow(app)
    mw.run()


if __name__ == "__main__":
    main()
