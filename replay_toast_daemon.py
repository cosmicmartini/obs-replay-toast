#!/usr/bin/env python3
"""
Replay Toast - renderer process.

Started automatically by replay_toast.py (the OBS script). It stays running in
the background, reads one JSON command per line from stdin, and shows a
customizable toast notification on the desktop.

Everything is drawn with vector graphics in "1080p units" and multiplied by a
scale factor derived from the target monitor, so it looks the same on 1080p,
1440p, 4K, ultrawide, and HiDPI displays.

Requires:  pip install PySide6
"""
import json
import math
import os
import subprocess
import sys
import threading
import traceback
from datetime import datetime

from PySide6.QtCore import (Qt, QObject, Signal, Slot, QRectF, QPointF, QUrl,
                            QVariantAnimation, QEasingCurve)
from PySide6.QtGui import (QGuiApplication, QPainter, QColor, QPainterPath, QFont,
                           QFontMetricsF, QPen, QBrush, QLinearGradient, QPixmap,
                           QPolygonF)
from PySide6.QtWidgets import QApplication, QWidget

try:
    from PySide6.QtMultimedia import QSoundEffect
except Exception:  # multimedia is optional
    QSoundEffect = None


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
EASING = {
    "Linear": QEasingCurve.Type.Linear,
    "OutCubic": QEasingCurve.Type.OutCubic,
    "OutQuart": QEasingCurve.Type.OutQuart,
    "OutExpo": QEasingCurve.Type.OutExpo,
    "OutBack": QEasingCurve.Type.OutBack,
    "OutElastic": QEasingCurve.Type.OutElastic,
    "OutBounce": QEasingCurve.Type.OutBounce,
}


class SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def qcolor(v, default=(255, 255, 255, 255)):
    try:
        r, g, b, a = v
        return QColor(int(r), int(g), int(b), int(a))
    except Exception:
        return QColor(*default)


def pick_screen(cfg):
    screens = QGuiApplication.screens()
    idx = int(cfg.get("monitor", 0))
    if 1 <= idx <= len(screens):
        return screens[idx - 1]
    return QGuiApplication.primaryScreen()


def make_path(rect, radius, smooth):
    """Rounded rectangle; 'smooth' gives a squircle-style continuous corner."""
    path = QPainterPath()
    w, h = rect.width(), rect.height()
    r = clamp(radius, 0.0, min(w, h) / 2.0)
    if r <= 0.01:
        path.addRect(rect)
        return path
    if not smooth:
        path.addRoundedRect(rect, r, r)
        return path

    r = min(r * 1.25, min(w, h) / 2.0)
    L, T, R, B = rect.left(), rect.top(), rect.right(), rect.bottom()
    n = 28

    def arc(cx, cy, sx, sy, reverse):
        pts = []
        for i in range(n + 1):
            a = (math.pi / 2.0) * (i / n)
            if reverse:
                a = math.pi / 2.0 - a
            pts.append(QPointF(cx + sx * r * math.sqrt(abs(math.cos(a))),
                               cy + sy * r * math.sqrt(abs(math.sin(a)))))
        return pts

    path.moveTo(L + r, T)
    for pt in arc(R - r, T + r, +1, -1, True):    # top-right
        path.lineTo(pt)
    for pt in arc(R - r, B - r, +1, +1, False):   # bottom-right
        path.lineTo(pt)
    for pt in arc(L + r, B - r, -1, +1, True):    # bottom-left
        path.lineTo(pt)
    for pt in arc(L + r, T + r, -1, -1, False):   # top-left
        path.lineTo(pt)
    path.closeSubpath()
    return path


def cover_pixmap(pix, w, h, dpr):
    pw, ph = max(1, math.ceil(w * dpr)), max(1, math.ceil(h * dpr))
    sc = pix.scaled(pw, ph, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    x, y = (sc.width() - pw) // 2, (sc.height() - ph) // 2
    out = sc.copy(x, y, pw, ph)
    out.setDevicePixelRatio(dpr)
    return out


def reveal_in_file_manager(path):
    try:
        if not path:
            return
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path) or "."])
    except Exception:
        traceback.print_exc()


# --------------------------------------------------------------------------- #
# the toast
# --------------------------------------------------------------------------- #
class Toast(QWidget):
    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow, True)
        self.setFocusPolicy(Qt.NoFocus)

        self._flags = None
        self.state = "idle"          # idle | in | hold | out | reset
        self.t = 0.0                 # visibility progress (can overshoot 1.0)
        self.remain = 1.0            # time-remaining fraction for progress bar
        self.card = None
        self.path = ""
        self.cfg = {}

        self.a_in = QVariantAnimation(self)
        self.a_in.valueChanged.connect(self._on_t)
        self.a_in.finished.connect(self._in_done)

        self.a_hold = QVariantAnimation(self)
        self.a_hold.setEasingCurve(QEasingCurve(QEasingCurve.Type.Linear))
        self.a_hold.valueChanged.connect(self._on_remain)
        self.a_hold.finished.connect(self._hold_done)

        self.a_out = QVariantAnimation(self)
        self.a_out.valueChanged.connect(self._on_t)
        self.a_out.finished.connect(self._out_done)

        self.sound = QSoundEffect(self) if QSoundEffect else None

    # ---- animation plumbing ------------------------------------------------
    def _on_t(self, v):
        self.t = float(v)
        self.update()

    def _on_remain(self, v):
        self.remain = float(v)
        self.update()

    def _in_done(self):
        if self.state != "in":
            return
        self.state = "hold"
        self.a_hold.setStartValue(1.0)
        self.a_hold.setEndValue(0.0)
        self.a_hold.setDuration(max(200, int(self.cfg["duration_ms"])))
        self.a_hold.start()

    def _hold_done(self):
        if self.state == "hold":
            self.dismiss()

    def _out_done(self):
        if self.state != "out":
            return
        self.state = "idle"
        self.hide()

    def dismiss(self):
        if self.state not in ("in", "hold"):
            return
        self.state = "out"
        self.a_in.stop()
        self.a_hold.stop()
        self.a_out.setStartValue(min(self.t, 1.0))
        self.a_out.setEndValue(0.0)
        self.a_out.setDuration(max(1, int(self.cfg["anim_out_ms"])))
        self.a_out.setEasingCurve(QEasingCurve(QEasingCurve.Type.InCubic))
        self.a_out.start()

    # ---- public entry ------------------------------------------------------
    def show_toast(self, cfg, ctx):
        visible = self.isVisible() and self.state != "idle"
        start_t = min(self.t, 1.0) if visible else 0.0

        self.state = "reset"
        for a in (self.a_in, self.a_hold, self.a_out):
            a.stop()

        self.cfg = cfg
        screen = pick_screen(cfg)
        self.prepare(cfg, ctx, screen)

        flags = (Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                 Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        if cfg["click_through"]:
            flags |= Qt.WindowTransparentForInput
        if flags != self._flags:
            self.setWindowFlags(flags)
            self._flags = flags

        self.setGeometry(*self._geom)
        self.t = start_t
        self.remain = 1.0
        self.state = "in"

        self.a_in.setStartValue(start_t)
        self.a_in.setEndValue(1.0)
        self.a_in.setDuration(max(1, int(cfg["anim_in_ms"])))
        self.a_in.setEasingCurve(QEasingCurve(EASING.get(cfg["ease"], QEasingCurve.Type.OutCubic)))
        self.a_in.start()

        self.show()
        self.raise_()
        self.update()
        self._play_sound(cfg)

    def _play_sound(self, cfg):
        try:
            p = cfg.get("sound_path") or ""
            if self.sound and p and os.path.isfile(p):
                self.sound.setSource(QUrl.fromLocalFile(p))
                self.sound.setVolume(clamp(cfg.get("sound_volume", 60) / 100.0, 0.0, 1.0))
                self.sound.play()
        except Exception:
            traceback.print_exc()

    # ---- layout + card rendering ------------------------------------------
    def prepare(self, cfg, ctx, screen):
        full = screen.geometry()
        area = screen.availableGeometry() if cfg["use_work_area"] else full
        dpr = screen.devicePixelRatio()

        # Resolution-independent scale: all sizes are authored at 1080p.
        base = min(full.width() / 1920.0, full.height() / 1080.0) if cfg["auto_scale"] else 1.0
        s = base * cfg["ui_scale"] / 100.0

        def S(v):
            return float(v) * s

        W, H = S(cfg["width"]), S(cfg["height"])
        pad = S(cfg["padding"])
        r = min(S(cfg["radius"]), min(W, H) / 2.0)
        smooth = cfg["corner_style"] == "squircle"

        shadow_on = bool(cfg["shadow_enabled"])
        sb = S(cfg["shadow_blur"]) if shadow_on else 0.0
        sox, soy = (S(cfg["shadow_x"]), S(cfg["shadow_y"])) if shadow_on else (0.0, 0.0)
        sm = math.ceil(sb + max(abs(sox), abs(soy)) + 3)  # shadow margin

        style = cfg["anim_style"]
        self.style = style
        self.has_slide = "slide" in style
        self.has_pop = "pop" in style
        self.pop_from = 0.82
        self.master = clamp(cfg["opacity"] / 100.0, 0.0, 1.0)
        travel = S(cfg["slide_distance"]) if self.has_slide else 0.0
        self.travel = travel

        anchor = cfg["anchor"]
        d = cfg["slide_dir"]
        if d == "auto":
            if anchor.endswith("left"):
                d = "left"
            elif anchor.endswith("right"):
                d = "right"
            elif anchor.startswith("top"):
                d = "top"
            else:
                d = "bottom"
        self.vec = {"left": (-1, 0), "right": (1, 0), "top": (0, -1), "bottom": (0, 1)}[d]

        # window margins: room for shadow, slide travel and overshoot
        m = sm
        if self.has_slide:
            m += 0.45 * travel
        if self.has_pop:
            m += 0.14 * max(W, H)
        ml = mt = mr = mb = m
        if self.has_slide:
            e = 0.55 * travel
            if d == "left":
                ml += e
            elif d == "right":
                mr += e
            elif d == "top":
                mt += e
            else:
                mb += e
        ml, mt, mr, mb = (math.ceil(v) for v in (ml, mt, mr, mb))
        ww, wh = math.ceil(W + ml + mr), math.ceil(H + mt + mb)

        mx, my = S(cfg["margin_x"]), S(cfg["margin_y"])
        ax, ay, aw, ah = area.x(), area.y(), area.width(), area.height()
        if anchor.endswith("left"):
            tx = ax + mx
        elif anchor.endswith("right"):
            tx = ax + aw - mx - W
        else:
            tx = ax + (aw - W) / 2.0
        ty = ay + my if anchor.startswith("top") else ay + ah - my - H
        self._geom = (round(tx - ml), round(ty - mt), ww, wh)

        self.ml, self.mt, self.W, self.H = ml, mt, W, H
        self.pb_h = S(cfg["progress_height"]) if cfg["progress_bar"] else 0.0
        self.pb_color = qcolor(cfg["progress_color"])
        self.path_win = make_path(QRectF(ml, mt, W, H), r, smooth)
        self.card_off = QPointF(ml - sm, mt - sm)

        # ---- tokens -------------------------------------------------------
        self.path = ctx.get("path") or ""
        now = datetime.now()
        fname = os.path.basename(self.path) if self.path else "clip.mp4"
        tokens = SafeDict(
            filename=fname,
            name=os.path.splitext(fname)[0],
            ext=os.path.splitext(fname)[1].lstrip("."),
            folder=os.path.dirname(self.path),
            path=self.path,
            time=now.strftime("%H:%M:%S"),
            time12=now.strftime("%I:%M:%S %p"),
            date=now.strftime("%Y-%m-%d"),
            count=ctx.get("count", 1),
        )

        def fmt(t):
            try:
                return str(t).format_map(tokens)
            except Exception:
                return str(t)

        # ---- render card into a cached pixmap ------------------------------
        cw, ch = W + 2 * sm, H + 2 * sm
        pm = QPixmap(math.ceil(cw * dpr), math.ceil(ch * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)

        rect = QRectF(sm, sm, W, H)
        path = make_path(rect, r, smooth)

        if shadow_on and sb >= 0:
            self._draw_shadow(p, QRectF(0, 0, cw, ch), rect, r, smooth, sb, sox, soy,
                              qcolor(cfg["shadow_color"]), path)

        # background
        p.save()
        p.setClipPath(path)
        c1 = qcolor(cfg["bg_color"])
        if cfg["gradient"]:
            c2 = qcolor(cfg["bg_color2"])
            ang = math.radians(cfg["gradient_angle"])
            dx, dy = math.cos(ang), math.sin(ang)
            half = (abs(dx) * W + abs(dy) * H) / 2.0
            c = rect.center()
            g = QLinearGradient(c.x() - dx * half, c.y() - dy * half,
                                c.x() + dx * half, c.y() + dy * half)
            g.setColorAt(0.0, c1)
            g.setColorAt(1.0, c2)
            p.fillRect(rect, QBrush(g))
        else:
            p.fillRect(rect, c1)

        if cfg["bg_image"] and os.path.isfile(cfg["bg_image"]):
            pix = QPixmap(cfg["bg_image"])
            if not pix.isNull():
                p.setOpacity(clamp(cfg["bg_image_opacity"] / 100.0, 0.0, 1.0))
                p.drawPixmap(rect.topLeft(), cover_pixmap(pix, W, H, dpr))
                p.setOpacity(1.0)

        aw_bar = S(cfg["accent_bar_width"]) if cfg["accent_bar"] else 0.0
        if aw_bar > 0:
            p.fillRect(QRectF(rect.left(), rect.top(), aw_bar, H), qcolor(cfg["accent_color"]))
        p.restore()

        # border
        bw = S(cfg["border_width"])
        if bw > 0.01:
            pen = QPen(qcolor(cfg["border_color"]), bw)
            pen.setJoinStyle(Qt.RoundJoin)
            inset = bw / 2.0
            bpath = make_path(rect.adjusted(inset, inset, -inset, -inset), max(0.0, r - inset), smooth)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPath(bpath)

        # content layout
        left = rect.left() + aw_bar + pad
        right = rect.right() - pad
        has_icon = cfg["icon_style"] != "none"
        isz = min(S(cfg["icon_size"]), H)
        gap = pad * 0.85
        if has_icon:
            ytop = rect.center().y() - isz / 2.0
            if cfg["icon_pos"] == "left":
                icon_rect = QRectF(left, ytop, isz, isz)
                tl, tr = left + isz + gap, right
            else:
                icon_rect = QRectF(right - isz, ytop, isz, isz)
                tl, tr = left, right - isz - gap
            self._draw_icon(p, icon_rect, cfg, dpr)
        else:
            tl, tr = left, right
        tw = max(1.0, tr - tl)

        face = cfg.get("font") or ""

        def mkfont(px, bold):
            f = QFont(face) if face else QFont()
            f.setPixelSize(max(1, round(px)))
            f.setBold(bool(bold))
            f.setHintingPreference(QFont.PreferNoHinting)
            return f

        lines = []
        title, sub = fmt(cfg["title_text"]), fmt(cfg["subtitle_text"])
        if title:
            f = mkfont(S(cfg["title_size"]), cfg["title_bold"])
            lines.append((title, f, QFontMetricsF(f), qcolor(cfg["title_color"])))
        if sub:
            f = mkfont(S(cfg["subtitle_size"]), cfg["subtitle_bold"])
            lines.append((sub, f, QFontMetricsF(f), qcolor(cfg["subtitle_color"])))
        if lines:
            lgap = S(3)
            total = sum(fm.height() for _, _, fm, _ in lines) + lgap * (len(lines) - 1)
            y = rect.center().y() - total / 2.0
            for text, font, fm, color in lines:
                el = fm.elidedText(text, Qt.ElideRight, tw)
                wtxt = fm.horizontalAdvance(el)
                if cfg["text_align"] == "center":
                    x = tl + (tw - wtxt) / 2.0
                elif cfg["text_align"] == "right":
                    x = tl + tw - wtxt
                else:
                    x = tl
                p.setFont(font)
                p.setPen(color)
                p.drawText(QPointF(x, y + fm.ascent()), el)
                y += fm.height() + lgap
        p.end()
        self.card = pm

    @staticmethod
    def _draw_shadow(p, whole, rect, r, smooth, blur, ox, oy, color, card_path):
        p.save()
        clip = QPainterPath()
        clip.addRect(whole)
        p.setClipPath(clip.subtracted(card_path))  # never darken the card itself
        p.setPen(Qt.NoPen)
        steps = 24
        base = color.alphaF()
        layer = QColor(color)
        layer.setAlphaF(clamp(base * 0.11, 0.0, 1.0))
        p.setBrush(layer)
        base_rect = rect.translated(ox, oy)
        for i in range(steps):
            grow = blur * (1.0 - i / steps)
            rr = base_rect.adjusted(-grow, -grow, grow, grow)
            p.drawPath(make_path(rr, r + grow, smooth))
        p.restore()

    @staticmethod
    def _draw_icon(p, r, cfg, dpr):
        style = cfg["icon_style"]
        shape = cfg["icon_bg_shape"]
        p.save()
        shape_path = QPainterPath()
        if shape == "circle":
            shape_path.addEllipse(r)
        elif shape == "rounded":
            shape_path.addRoundedRect(r, r.width() * 0.3, r.height() * 0.3)

        if style == "custom":
            pix = QPixmap(cfg["icon_path"]) if cfg.get("icon_path") else QPixmap()
            if not pix.isNull():
                if shape != "none":
                    p.setClipPath(shape_path)
                    p.drawPixmap(r.topLeft(), cover_pixmap(pix, r.width(), r.height(), dpr))
                else:
                    pw, ph = math.ceil(r.width() * dpr), math.ceil(r.height() * dpr)
                    sc = pix.scaled(pw, ph, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    sc.setDevicePixelRatio(dpr)
                    lw, lh = sc.width() / dpr, sc.height() / dpr
                    p.drawPixmap(QPointF(r.x() + (r.width() - lw) / 2, r.y() + (r.height() - lh) / 2), sc)
            p.restore()
            return

        if shape != "none":
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor(cfg["icon_bg_color"]))
            p.drawPath(shape_path)

        k = 0.27 if shape != "none" else 0.08
        g = QRectF(r.x() + r.width() * k, r.y() + r.height() * k,
                   r.width() * (1 - 2 * k), r.height() * (1 - 2 * k))
        color = qcolor(cfg["icon_color"])
        pen = QPen(color, g.width() * 0.13, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)

        def pt(ux, uy):
            return QPointF(g.x() + g.width() * ux, g.y() + g.height() * uy)

        if style == "check":
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPolyline(QPolygonF([pt(.08, .54), pt(.38, .82), pt(.92, .20)]))
        elif style == "play":
            p.setPen(QPen(color, g.width() * 0.08, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(color)
            p.drawPolygon(QPolygonF([pt(.25, .12), pt(.88, .50), pt(.25, .88)]))
        elif style == "record":
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawEllipse(g.center(), g.width() * 0.38, g.height() * 0.38)
        elif style == "replay":
            c = g.center()
            R = g.width() * 0.40
            arc_rect = QRectF(c.x() - R, c.y() - R, 2 * R, 2 * R)
            start, span = 110, -285
            ap = QPainterPath()
            ap.arcMoveTo(arc_rect, start)
            ap.arcTo(arc_rect, start, span)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPath(ap)
            # arrow head at the end of the arc (clockwise travel direction)
            phi = math.radians(-(start + span))
            ex, ey = c.x() + R * math.cos(phi), c.y() + R * math.sin(phi)
            tx, ty = -math.sin(phi), math.cos(phi)
            nx, ny = -ty, tx
            a = g.width() * 0.26
            tip = QPointF(ex + tx * a * 0.6, ey + ty * a * 0.6)
            b1 = QPointF(ex - tx * a * 0.4 + nx * a * 0.5, ey - ty * a * 0.4 + ny * a * 0.5)
            b2 = QPointF(ex - tx * a * 0.4 - nx * a * 0.5, ey - ty * a * 0.4 - ny * a * 0.5)
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawPolygon(QPolygonF([tip, b1, b2]))
        p.restore()

    # ---- painting ------------------------------------------------------------
    def paintEvent(self, _):
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.transparent)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        if self.card is None or self.state == "idle":
            return
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)

        t = self.t
        a = clamp(t, 0.0, 1.0) if self.style == "fade" else clamp(t * 2.5, 0.0, 1.0)
        p.setOpacity(a * self.master)

        cx, cy = self.ml + self.W / 2.0, self.mt + self.H / 2.0
        off = (1.0 - t) * self.travel if self.has_slide else 0.0
        dx, dy = self.vec[0] * off, self.vec[1] * off
        sc = (self.pop_from + (1.0 - self.pop_from) * t) if self.has_pop else 1.0
        p.translate(cx + dx, cy + dy)
        p.scale(sc, sc)
        p.translate(-cx, -cy)

        p.drawPixmap(self.card_off, self.card)
        if self.pb_h > 0 and self.remain > 0 and self.state in ("hold", "in"):
            p.setClipPath(self.path_win)
            p.fillRect(QRectF(self.ml, self.mt + self.H - self.pb_h,
                              self.W * self.remain, self.pb_h), self.pb_color)

    # ---- interaction (only when click-through is off) ----------------------
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            reveal_in_file_manager(self.path)
            self.dismiss()
        elif e.button() == Qt.RightButton:
            self.dismiss()


# --------------------------------------------------------------------------- #
# stdin bridge
# --------------------------------------------------------------------------- #
class Hub(QObject):
    message = Signal(str)
    quit_sig = Signal()

    def __init__(self, toast):
        super().__init__()
        self.toast = toast
        self.message.connect(self.on_message)
        self.quit_sig.connect(self.on_quit)

    @Slot(str)
    def on_message(self, line):
        try:
            msg = json.loads(line)
            cmd = msg.get("cmd")
            if cmd == "toast":
                self.toast.show_toast(msg["cfg"], msg.get("ctx", {}))
            elif cmd == "hide":
                self.toast.dismiss()
            elif cmd == "quit":
                QApplication.quit()
        except Exception:
            traceback.print_exc()
            sys.stderr.flush()

    @Slot()
    def on_quit(self):
        QApplication.quit()


def reader(hub):
    try:
        for line in sys.stdin:
            line = line.strip()
            if line:
                hub.message.emit(line)
    except Exception:
        pass
    hub.quit_sig.emit()  # parent (OBS) went away


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    toast = Toast()
    hub = Hub(toast)
    threading.Thread(target=reader, args=(hub,), daemon=True).start()
    print("toast engine ready", flush=True)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
