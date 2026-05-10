#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import socket
import sys
from pathlib import Path

from PyQt6.QtCore import QEvent, QPointF, QPropertyAnimation, QRect, QTimer, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QWidget

from openknob.settings import SETTINGS_FILE, load_settings

import os as _os
DEFAULT_SOCKET  = f"/run/user/{_os.getuid()}/openknob.sock"
HIDE_TIMEOUT_MS = 2000
_DEF_SIZE       = 220
_DEF_MARGIN     = 20
_DEF_POS        = "bottom-right"

_POSITION_MAP = {
    "top-left":      lambda a, w, h, m: (a.left()           + m, a.top()             + m),
    "top-center":    lambda a, w, h, m: (a.center().x()-w//2,    a.top()             + m),
    "top-right":     lambda a, w, h, m: (a.right()  - w     - m, a.top()             + m),
    "middle-left":   lambda a, w, h, m: (a.left()           + m, a.center().y()-h//2   ),
    "center":        lambda a, w, h, m: (a.center().x()-w//2,    a.center().y()-h//2   ),
    "middle-right":  lambda a, w, h, m: (a.right()  - w     - m, a.center().y()-h//2   ),
    "bottom-left":   lambda a, w, h, m: (a.left()           + m, a.bottom() - h     - m),
    "bottom-center": lambda a, w, h, m: (a.center().x()-w//2,    a.bottom() - h     - m),
    "bottom-right":  lambda a, w, h, m: (a.right()  - w     - m, a.bottom() - h     - m),
}


class OverlayWindow(QWidget):
    def __init__(self, socket_path: str, parent=None):
        super().__init__(parent)
        self.socket_path = socket_path
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        # Theme defaults — overridden by _apply_settings()
        self._pos_name   = _DEF_POS
        self._margin     = _DEF_MARGIN
        self._win_size   = _DEF_SIZE
        self._ring_track = QColor(255, 255, 255, 18)
        self._ring_fill  = QColor(40,  190, 255, 255)
        self._bg_color   = QColor(10,  10,  16,  210)
        self._text_color = QColor(255, 255, 255, 255)
        self._settings_mtime: float = 0.0

        self._apply_settings()
        self.setFixedSize(self._win_size, self._win_size)

        self.opacity_anim = QPropertyAnimation(self, b"windowOpacity")
        self.opacity_anim.setDuration(180)
        self.opacity_anim.finished.connect(self._on_anim_finished)

        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.fade_out)

        # Knob state
        self.value        = 50
        self.label        = "Volume"
        self.event_text   = ""
        self.func_index   = 0
        self.func_count   = 1
        self.show_pct     = True
        self.show_ring    = True

        self.sock    = None
        self.pending = b""

        self._setup_socket()
        self.position_window()
        self.fade_out(immediate=True)

        self.read_timer = QTimer(self)
        self.read_timer.setInterval(50)
        self.read_timer.timeout.connect(self._socket_readable)
        self.read_timer.start()

        self.reconnect_timer = QTimer(self)
        self.reconnect_timer.setInterval(1000)
        self.reconnect_timer.timeout.connect(self._setup_socket)
        self.reconnect_timer.start()

        self.settings_timer = QTimer(self)
        self.settings_timer.setInterval(1000)
        self.settings_timer.timeout.connect(self._check_settings)
        self.settings_timer.start()

    # ── Settings live-reload ──────────────────────────────────────────────────

    def _apply_settings(self) -> None:
        try:
            if SETTINGS_FILE.exists():
                self._settings_mtime = SETTINGS_FILE.stat().st_mtime
        except Exception:
            pass
        try:
            s  = load_settings()
            ov = s.get("overlay", {})
            c  = ov.get("colors", {})
            self._pos_name   = ov.get("position", _DEF_POS)
            self._margin     = ov.get("margin",   _DEF_MARGIN)
            self._win_size   = ov.get("size",      _DEF_SIZE)
            self._ring_track = QColor(*c.get("ring_track", [255, 255, 255,  18]))
            self._ring_fill  = QColor(*c.get("ring_fill",  [ 40, 190, 255, 255]))
            self._bg_color   = QColor(*c.get("background", [ 10,  10,  16, 210]))
            self._text_color = QColor(*c.get("text",       [255, 255, 255, 255]))
        except Exception:
            pass

    def _check_settings(self) -> None:
        try:
            mtime = SETTINGS_FILE.stat().st_mtime if SETTINGS_FILE.exists() else 0.0
        except Exception:
            return
        if mtime == self._settings_mtime:
            return
        self._apply_settings()
        self.setFixedSize(self._win_size, self._win_size)
        self.position_window()
        self.update()

    # ── Window position ───────────────────────────────────────────────────────

    def position_window(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        a = screen.availableGeometry()
        w, h = self.width(), self.height()
        fn = _POSITION_MAP.get(self._pos_name, _POSITION_MAP[_DEF_POS])
        self.move(*fn(a, w, h, self._margin))

    # ── Fade ─────────────────────────────────────────────────────────────────

    def show_overlay(self) -> None:
        # Always show on press so the user sees the function switch
        if not self.show_pct and not self.show_ring and self.event_text != "press":
            return
        self.hide_timer.start(HIDE_TIMEOUT_MS)
        self._fade(1.0)
        self.update()

    def fade_out(self, immediate: bool = False) -> None:
        if immediate:
            self.opacity_anim.stop()
            self.setWindowOpacity(0.0)
            self.hide()
            return
        self._fade(0.0)

    def _on_anim_finished(self) -> None:
        if self.windowOpacity() < 0.01:
            self.hide()

    def _fade(self, target: float) -> None:
        self.opacity_anim.stop()
        if target > 0 and not self.isVisible():
            self.show()
        self.opacity_anim.setStartValue(self.windowOpacity())
        self.opacity_anim.setEndValue(target)
        self.opacity_anim.start()

    # ── State update ──────────────────────────────────────────────────────────

    def update_state(self, event: str) -> None:
        parts = event.split("\t")
        action = parts[0]

        def _s(i: int, default: str = "") -> str:
            return parts[i] if len(parts) > i else default

        def _i(i: int, default: int = 0) -> int:
            try:
                return int(parts[i]) if len(parts) > i else default
            except ValueError:
                return default

        if action == "rotate_cw":
            self.value      = min(100, self.value + 5)
            self.event_text = "cw"
            self.label      = _s(2, self.label)
            self.func_index = _i(3, self.func_index)
            self.func_count = _i(4, self.func_count)
            self.show_pct   = bool(_i(5, int(self.show_pct)))
            self.show_ring  = bool(_i(6, int(self.show_ring)))
        elif action == "rotate_ccw":
            self.value      = max(0, self.value - 5)
            self.event_text = "ccw"
            self.label      = _s(2, self.label)
            self.func_index = _i(3, self.func_index)
            self.func_count = _i(4, self.func_count)
            self.show_pct   = bool(_i(5, int(self.show_pct)))
            self.show_ring  = bool(_i(6, int(self.show_ring)))
        elif action == "press":
            self.event_text = "press"
            self.label      = _s(1, self.label)
            self.func_index = _i(2, self.func_index)
            self.func_count = _i(3, self.func_count)
            self.show_pct   = bool(_i(4, int(self.show_pct)))
            self.show_ring  = bool(_i(5, int(self.show_ring)))
        elif action == "release":
            return
        elif action == "profile_change":
            self.label      = _s(2, self.label)
            self.func_index = _i(3, self.func_index)
            self.func_count = _i(4, self.func_count)
            self.event_text = f"profile:{_s(1)}"
        else:
            self.event_text = action

        self.show_overlay()

    # ── Paint ─────────────────────────────────────────────────────────────────

    def paintEvent(self, _event) -> None:
        p  = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        w, h   = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0

        # ── Background ────────────────────────────────────────────────────────
        bg_inset = 7
        bg_rect  = self.rect().adjusted(bg_inset, bg_inset, -bg_inset, -bg_inset)

        # Subtle outer halo
        halo = QColor(self._ring_fill)
        halo.setAlpha(22)
        p.setPen(QPen(halo, 4))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(bg_rect.adjusted(-3, -3, 3, 3))

        # Border ring
        border = QColor(255, 255, 255, 12)
        p.setPen(QPen(border, 1.5))
        p.setBrush(self._bg_color)
        p.drawEllipse(bg_rect)

        # ── Arc ───────────────────────────────────────────────────────────────
        if self.show_ring:
            ring_pad = 20
            arc_rect = self.rect().adjusted(ring_pad, ring_pad, -ring_pad, -ring_pad)
            arc_r    = arc_rect.width() / 2.0
            pen_w    = 9

            span_deg   = self.value / 100.0 * 360.0
            span_angle = int(span_deg * 16)

            # Track
            p.setPen(QPen(self._ring_track, pen_w, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(arc_rect)

            if self.value > 0:
                # Glow pass
                glow = QColor(self._ring_fill)
                glow.setAlpha(45)
                glow_pen = QPen(glow, pen_w + 10, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap)
                p.setPen(glow_pen)
                p.drawArc(arc_rect, 90 * 16, -span_angle)

                # Main arc
                fill_pen = QPen(self._ring_fill, pen_w, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap)
                p.setPen(fill_pen)
                p.drawArc(arc_rect, 90 * 16, -span_angle)

                # Tip
                angle_rad = math.radians(90.0 - span_deg)
                tx = cx + arc_r * math.cos(angle_rad)
                ty = cy - arc_r * math.sin(angle_rad)

                tip_glow = QColor(self._ring_fill)
                tip_glow.setAlpha(70)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(tip_glow)
                p.drawEllipse(QPointF(tx, ty), 10.0, 10.0)

                p.setBrush(QColor(255, 255, 255, 220))
                p.drawEllipse(QPointF(tx, ty), 4.5, 4.5)

        # ── Label (function name) ─────────────────────────────────────────────
        label_only = not self.show_pct and not self.show_ring
        lbl_c = QColor(self._text_color)
        lbl_c.setAlpha(200 if label_only else 120)
        p.setPen(lbl_c)
        lbl_font = QFont()
        if label_only:
            # Fit text inside the circle: start big, shrink until it fits
            pad = int(w * 0.22)
            lbl_rect = QRect(pad, int(cy - w // 4), w - pad * 2, w // 2)
            for pt in (20, 17, 14, 11, 9):
                lbl_font.setPointSize(pt)
                lbl_font.setBold(pt >= 14)
                lbl_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.0)
                p.setFont(lbl_font)
                bound = p.boundingRect(lbl_rect,
                                       Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                                       self.label.upper())
                if bound.width() <= lbl_rect.width() and bound.height() <= lbl_rect.height():
                    break
            p.drawText(lbl_rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                       self.label.upper())
        else:
            lbl_font.setPointSize(9)
            lbl_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2.5)
            lbl_rect = QRect(28, int(cy - 48), w - 56, 20)
            p.setFont(lbl_font)
            p.drawText(lbl_rect, Qt.AlignmentFlag.AlignCenter, self.label.upper())

        # ── Value (large number + % suffix) ──────────────────────────────────
        if self.show_pct:
            num_str = str(self.value)

            val_font = QFont()
            val_font.setPointSize(34)
            val_font.setBold(True)
            pct_font = QFont()
            pct_font.setPointSize(13)
            pct_font.setBold(False)

            fm_val = QFontMetrics(val_font)
            fm_pct = QFontMetrics(pct_font)
            num_w  = fm_val.horizontalAdvance(num_str)
            pct_w  = fm_pct.horizontalAdvance("%")
            gap    = 3
            total  = num_w + gap + pct_w
            num_x  = cx - total / 2
            base_y = int(cy + fm_val.ascent() / 2 - 4)

            p.setPen(self._text_color)
            p.setFont(val_font)
            p.drawText(QPointF(num_x, base_y), num_str)

            pct_c = QColor(self._text_color)
            pct_c.setAlpha(110)
            p.setPen(pct_c)
            p.setFont(pct_font)
            p.drawText(QPointF(num_x + num_w + gap, base_y - 2), "%")

        # ── Event indicator ───────────────────────────────────────────────────
        ev_map = {"cw": "▲", "ccw": "▼", "press": "●"}
        ev_text = ev_map.get(self.event_text, self.event_text)
        if ev_text and ev_text not in ("▲", "▼", "●"):
            ev_text = ev_text[:22]
        if label_only:
            ev_text = ""  # label is already prominent; no redundant dot

        if ev_text:
            ev_c = QColor(self._ring_fill)
            ev_c.setAlpha(195)
            p.setPen(ev_c)
            ev_font = QFont()
            ev_font.setPointSize(9 if len(ev_text) > 3 else 11)
            p.setFont(ev_font)
            ev_rect = QRect(24, int(cy + 28), w - 48, 22)
            p.drawText(ev_rect, Qt.AlignmentFlag.AlignCenter, ev_text)

        # ── Function dots ─────────────────────────────────────────────────────
        if self.func_count > 1:
            dot_r   = 3.2
            spacing = dot_r * 3.5
            total_w = (self.func_count - 1) * spacing
            sx      = cx - total_w / 2
            dy      = cy + 50

            for i in range(self.func_count):
                ix = sx + i * spacing
                if i == self.func_index:
                    dot_c = QColor(self._ring_fill)
                    dot_c.setAlpha(240)
                    r = dot_r + 0.8
                else:
                    dot_c = QColor(self._text_color)
                    dot_c.setAlpha(45)
                    r = dot_r
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(dot_c)
                p.drawEllipse(QPointF(ix, dy), r, r)

    # ── Socket ────────────────────────────────────────────────────────────────

    def _setup_socket(self) -> None:
        if self.sock is not None:
            return
        if not Path(self.socket_path).exists():
            return
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.setblocking(False)
            s.connect(self.socket_path)
            self.sock = s
        except OSError:
            self.sock = None

    def _socket_readable(self) -> None:
        if self.sock is None:
            return
        try:
            data = self.sock.recv(1024)
            if not data:
                self.sock.close()
                self.sock = None
                return
            self.pending += data
            while b"\n" in self.pending:
                line, self.pending = self.pending.split(b"\n", 1)
                try:
                    ev = line.decode("utf-8").strip()
                except UnicodeDecodeError:
                    continue
                if ev:
                    self.update_state(ev)
        except BlockingIOError:
            return
        except OSError:
            if self.sock:
                self.sock.close()
            self.sock = None

    def event(self, ev: QEvent) -> bool:
        if ev.type() == QEvent.Type.WindowActivate:
            self.position_window()
        return super().event(ev)


def main() -> int:
    parser = argparse.ArgumentParser(description="openknob overlay.")
    parser.add_argument("--socket", default=DEFAULT_SOCKET)
    args = parser.parse_args()

    app = QApplication(sys.argv)
    overlay = OverlayWindow(args.socket)
    overlay.show()
    overlay.fade_out(immediate=True)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
