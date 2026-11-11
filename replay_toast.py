"""
Replay Toast for OBS Studio
---------------------------
Shows a fully customizable toast notification on your desktop whenever the
Replay Buffer is saved.

Setup:
  1. Install Python 3.9+ and point OBS at it:  Tools > Scripts > Python Settings
  2. In that same Python:  python -m pip install PySide6
  3. Keep this file and replay_toast_daemon.py in the same folder.
  4. Tools > Scripts > "+"  and add this file (replay_toast.py).
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import threading

import obspython as obs

DAEMON_NAME = "replay_toast_daemon.py"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(SCRIPT_DIR, "replay_toast_settings.json")


def col(hexstr, a=255):
    """'#RRGGBB' + alpha -> OBS color int (0xAABBGGRR)."""
    h = hexstr.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (a << 24) | (b << 16) | (g << 8) | r


# --------------------------------------------------------------------------- #
# Settings schema: one declarative table drives defaults, UI and the payload
# --------------------------------------------------------------------------- #
GROUPS = [
    ("pos", "Position & Size"),
    ("style", "Colors & Shape"),
    ("shadow", "Shadow & Glow"),
    ("text", "Text"),
    ("icon", "Icon & Graphics"),
    ("anim", "Animation & Timing"),
    ("behav", "Behavior & Advanced"),
]
FIELDS = []


def F(group, key, kind, label, default, **kw):
    FIELDS.append(dict(group=group, key=key, kind=kind, label=label, default=default, **kw))


# ---- Position & Size
F("pos", "anchor", "list", "Screen position", "bottom_right", choices=[
    ("Top Left", "top_left"), ("Top Center", "top_center"), ("Top Right", "top_right"),
    ("Bottom Left", "bottom_left"), ("Bottom Center", "bottom_center"), ("Bottom Right", "bottom_right")])
F("pos", "monitor", "int", "Monitor (0 = primary)", 0, min=0, max=8, step=1)
F("pos", "margin_x", "int", "Edge margin X (px @1080p)", 32, min=0, max=800, step=1)
F("pos", "margin_y", "int", "Edge margin Y (px @1080p)", 32, min=0, max=800, step=1)
F("pos", "use_work_area", "bool", "Stay clear of taskbar / dock", True)
F("pos", "auto_scale", "bool", "Auto-scale to monitor resolution (1080p reference)", True)
F("pos", "ui_scale", "int", "Extra scale (%)", 100, min=25, max=400, step=5)
F("pos", "width", "int", "Width (px @1080p)", 430, min=160, max=1400, step=2)
F("pos", "height", "int", "Height (px @1080p)", 92, min=40, max=500, step=2)
F("pos", "padding", "int", "Inner padding (px @1080p)", 18, min=0, max=120, step=1)

# ---- Colors & Shape
F("style", "radius", "int", "Corner radius (px @1080p)", 22, min=0, max=250, step=1)
F("style", "corner_style", "list", "Corner style", "round", choices=[
    ("Round", "round"), ("Squircle (smooth)", "squircle")])
F("style", "bg_color", "color_a", "Background color", col("#1B1F26", 238))
F("style", "gradient", "bool", "Use background gradient", True)
F("style", "bg_color2", "color_a", "Gradient end color", col("#11141A", 238))
F("style", "gradient_angle", "int", "Gradient angle (0 = left>right, 90 = top>bottom)", 120, min=0, max=360, step=1)
F("style", "border_color", "color_a", "Border color", col("#FFFFFF", 30))
F("style", "border_width", "float", "Border width (px @1080p)", 1.0, min=0.0, max=12.0, step=0.25)
F("style", "accent_color", "color", "Accent color", col("#76B900"))
F("style", "accent_bar", "bool", "Accent bar on left edge", True)
F("style", "accent_bar_width", "int", "Accent bar width", 5, min=1, max=60, step=1)
F("style", "progress_bar", "bool", "Countdown bar along the bottom", True)
F("style", "progress_height", "int", "Countdown bar height", 4, min=1, max=40, step=1)
F("style", "progress_color", "color_a", "Countdown bar color", col("#76B900", 255))
F("style", "opacity", "int", "Overall opacity (%)", 100, min=10, max=100, step=1)

# ---- Shadow & Glow
F("shadow", "shadow_enabled", "bool", "Enable shadow / glow", True)
F("shadow", "shadow_color", "color_a", "Shadow color (use a bright color for glow)", col("#000000", 150))
F("shadow", "shadow_blur", "int", "Blur size (px @1080p)", 36, min=0, max=160, step=1)
F("shadow", "shadow_x", "int", "Offset X", 0, min=-120, max=120, step=1)
F("shadow", "shadow_y", "int", "Offset Y", 10, min=-120, max=120, step=1)

# ---- Text
F("text", "title_text", "text", "Title", "Replay Saved")
F("text", "subtitle_text", "text",
  "Subtitle  ({filename} {name} {ext} {folder} {path} {time} {time12} {date} {count})", "{filename}")
F("text", "font", "font", "Font family", None)
F("text", "title_size", "int", "Title size (px @1080p)", 21, min=8, max=120, step=1)
F("text", "subtitle_size", "int", "Subtitle size (px @1080p)", 14, min=6, max=100, step=1)
F("text", "title_bold", "bool", "Bold title", True)
F("text", "subtitle_bold", "bool", "Bold subtitle", False)
F("text", "text_align", "list", "Text alignment", "left", choices=[
    ("Left", "left"), ("Center", "center"), ("Right", "right")])
F("text", "title_color", "color_a", "Title color", col("#FFFFFF"))
F("text", "subtitle_color", "color_a", "Subtitle color", col("#A9B0BC"))

# ---- Icon & Graphics
F("icon", "icon_style", "list", "Icon", "replay", choices=[
    ("Replay arrow", "replay"), ("Checkmark", "check"), ("Play", "play"),
    ("Record dot", "record"), ("Custom image", "custom"), ("None", "none")])
F("icon", "icon_path", "file_img", "Custom icon image (PNG/JPG/SVG...)", "")
F("icon", "icon_size", "int", "Icon size (px @1080p)", 54, min=16, max=300, step=1)
F("icon", "icon_pos", "list", "Icon position", "left", choices=[("Left", "left"), ("Right", "right")])
F("icon", "icon_color", "color_a", "Icon glyph color", col("#0E1114"))
F("icon", "icon_bg_shape", "list", "Icon backdrop / crop shape", "circle", choices=[
    ("Circle", "circle"), ("Rounded square", "rounded"), ("None", "none")])
F("icon", "icon_bg_color", "color_a", "Icon backdrop color", col("#76B900"))
F("icon", "bg_image", "file_img", "Background image (optional)", "")
F("icon", "bg_image_opacity", "int", "Background image opacity (%)", 30, min=0, max=100, step=1)

# ---- Animation & Timing
F("anim", "anim_style", "list", "Entrance style", "slide_fade", choices=[
    ("Slide", "slide"), ("Slide + Fade", "slide_fade"), ("Fade", "fade"),
    ("Pop (scale)", "pop"), ("Slide + Pop", "slide_pop")])
F("anim", "slide_dir", "list", "Slide direction (comes from)", "auto", choices=[
    ("Auto (nearest edge)", "auto"), ("Left", "left"), ("Right", "right"),
    ("Top", "top"), ("Bottom", "bottom")])
F("anim", "slide_distance", "int", "Slide distance (px @1080p)", 90, min=0, max=800, step=1)
F("anim", "ease", "list", "Easing", "OutCubic", choices=[
    ("Smooth (OutCubic)", "OutCubic"), ("Soft (OutQuart)", "OutQuart"), ("Snappy (OutExpo)", "OutExpo"),
    ("Overshoot (OutBack)", "OutBack"), ("Elastic", "OutElastic"), ("Bounce", "OutBounce"),
    ("Linear", "Linear")])
F("anim", "anim_in_ms", "int", "Entrance duration (ms)", 550, min=50, max=3000, step=10)
F("anim", "anim_out_ms", "int", "Exit duration (ms)", 380, min=50, max=3000, step=10)
F("anim", "duration_ms", "int", "Time on screen (ms)", 4500, min=500, max=30000, step=100)

# ---- Behavior & Advanced
F("behav", "enabled", "bool", "Show toast when replay is saved", True)
F("behav", "click_through", "bool",
  "Click-through (off = left-click opens file location, right-click dismisses)", True)
F("behav", "sound_path", "file_snd", "Sound (.wav, optional)", "")
F("behav", "sound_volume", "int", "Sound volume (%)", 60, min=0, max=100, step=1)
F("behav", "auto_preview", "bool", "Auto-preview when a setting changes", True)
F("behav", "python_path", "text", "Python executable for the toast engine (blank = auto-detect)", "")

FIELD_BY_KEY = {f["key"]: f for f in FIELDS}

# Default look: flat, square, no accents, no shadow, plain fade, muted grey checkmark.
# These override the per-field defaults above on a fresh install.
DEFAULT_LOOK = dict(
    padding=16, ui_scale=70, opacity=100,
    radius=0, corner_style="round",
    bg_color=col("#1E1E1E", 255), gradient=False, bg_color2=col("#1E1E1E", 255),
    border_color=col("#FFFFFF", 255), border_width=0.0,
    accent_color=col("#FFFFFF"), accent_bar=False,
    progress_bar=False, progress_color=col("#FFFFFF", 255),
    shadow_enabled=False,
    title_color=col("#FFFFFF"), subtitle_color=col("#BDBDBD"), title_bold=False, text_align="left",
    icon_style="check", icon_bg_shape="none", icon_bg_color=col("#FFFFFF"),
    icon_color=col("#8C8C8C"), icon_size=40, icon_pos="left",
    anim_style="fade", ease="OutCubic", anim_in_ms=250, anim_out_ms=250, duration_ms=3500,
)
for _k, _v in DEFAULT_LOOK.items():
    FIELD_BY_KEY[_k]["default"] = _v

# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #
_settings = None
_cfg = None
_cfg_json = None
_script_dir = ""
_proc = None
_booting = False
_pending = None
_lock = threading.Lock()
_save_count = 0
_last_python = None


def log(msg, level=None):
    obs.script_log(obs.LOG_INFO if level is None else level, "[Replay Toast] " + str(msg))


# --------------------------------------------------------------------------- #
# Settings <-> OBS data
# --------------------------------------------------------------------------- #
def _decode_color(v):
    v &= 0xFFFFFFFF
    return [v & 255, (v >> 8) & 255, (v >> 16) & 255, (v >> 24) & 255]


def read_cfg(settings):
    cfg = {}
    for f in FIELDS:
        k, kind = f["key"], f["kind"]
        if kind == "bool":
            cfg[k] = obs.obs_data_get_bool(settings, k)
        elif kind == "int":
            cfg[k] = obs.obs_data_get_int(settings, k)
        elif kind == "float":
            cfg[k] = obs.obs_data_get_double(settings, k)
        elif kind in ("text", "list", "file_img", "file_snd"):
            cfg[k] = obs.obs_data_get_string(settings, k)
        elif kind in ("color", "color_a"):
            c = _decode_color(obs.obs_data_get_int(settings, k))
            if kind == "color":
                c[3] = 255
            cfg[k] = c
        elif kind == "font":
            obj = obs.obs_data_get_obj(settings, k)
            face = ""
            if obj:
                face = obs.obs_data_get_string(obj, "face")
                obs.obs_data_release(obj)
            cfg[k] = face or "Segoe UI"
    return cfg


def _set_value(settings, key, value):
    kind = FIELD_BY_KEY[key]["kind"]
    if kind == "bool":
        obs.obs_data_set_bool(settings, key, bool(value))
    elif kind in ("int", "color", "color_a"):
        obs.obs_data_set_int(settings, key, int(value))
    elif kind == "float":
        obs.obs_data_set_double(settings, key, float(value))
    elif kind == "font":
        o = obs.obs_data_create()
        obs.obs_data_set_string(o, "face", str(value))
        obs.obs_data_set_string(o, "style", "Regular")
        obs.obs_data_set_int(o, "size", 16)
        obs.obs_data_set_int(o, "flags", 0)
        obs.obs_data_set_obj(settings, key, o)
        obs.obs_data_release(o)
    else:
        obs.obs_data_set_string(settings, key, str(value))


# --------------------------------------------------------------------------- #
# Export / import settings file (becomes the defaults on load)
# --------------------------------------------------------------------------- #
def _to_jsonable(cfg):
    out = {}
    for f in FIELDS:
        k = f["key"]
        if k == "python_path":      # machine-specific, never exported
            continue
        v = cfg[k]
        if f["kind"] in ("color", "color_a"):
            v = "#%02X%02X%02X%02X" % tuple(v)   # #RRGGBBAA
        out[k] = v
    return out


def _from_json_value(f, v):
    if f["kind"] in ("color", "color_a"):
        h = str(v).lstrip("#")
        a = int(h[6:8], 16) if len(h) >= 8 else 255
        return col("#" + h[:6], a)
    return v


def _load_file_values():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    out = {}
    for k, v in data.items():
        f = FIELD_BY_KEY.get(k)
        if f is None:
            continue
        try:
            out[k] = _from_json_value(f, v)
        except Exception:
            pass
    return out


# --------------------------------------------------------------------------- #
# Toast engine management
# --------------------------------------------------------------------------- #
def _creationflags():
    return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _python_candidates(custom):
    c = []
    if custom:
        c.append(custom)
    for base in (sys.exec_prefix, sys.base_prefix):
        if sys.platform == "win32":
            c.append(os.path.join(base, "python.exe"))
        else:
            c += [os.path.join(base, "bin", "python3"), os.path.join(base, "bin", "python")]
    if sys.platform != "win32":
        c += [shutil.which("python3"), shutil.which("python")]
    seen, out = set(), []
    for p in c:
        if p and p not in seen and os.path.isfile(p):
            seen.add(p)
            out.append(p)
    return out


def _has_pyside(py):
    try:
        r = subprocess.run([py, "-c", "import PySide6.QtWidgets"], capture_output=True,
                           timeout=45, creationflags=_creationflags())
        return r.returncode == 0
    except Exception:
        return False


def _boot(custom, daemon_path):
    global _proc, _booting, _pending
    try:
        cands = _python_candidates(custom)
        py = next((p for p in cands if _has_pyside(p)), None)
        if not py:
            tip = cands[0] if cands else "python"
            log('PySide6 not found. Install it with:  "%s" -m pip install PySide6' % tip, obs.LOG_WARNING)
            return
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONHOME", "PYTHONPATH")}
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen([py, "-u", daemon_path], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, env=env, creationflags=_creationflags(),
                                cwd=os.path.dirname(daemon_path))
        with _lock:
            _proc = proc
            pend, _pending = _pending, None
        _booting = False
        if pend:
            try:
                proc.stdin.write(pend)
                proc.stdin.flush()
            except Exception:
                pass
        for line in proc.stdout:
            line = line.decode("utf-8", "replace").rstrip()
            if line:
                log(line)
    except Exception as e:
        log("Failed to start toast engine: %s" % e, obs.LOG_WARNING)
    finally:
        _booting = False


def start_engine():
    global _booting
    if _booting:
        return
    daemon = os.path.join(_script_dir, DAEMON_NAME)
    if not os.path.isfile(daemon):
        log("%s not found next to this script (%s)" % (DAEMON_NAME, _script_dir), obs.LOG_WARNING)
        return
    _booting = True
    custom = (_cfg or {}).get("python_path", "")
    threading.Thread(target=_boot, args=(custom, daemon), daemon=True).start()


def stop_engine():
    global _proc
    with _lock:
        p, _proc = _proc, None
    if not p:
        return
    try:
        p.stdin.write(b'{"cmd":"quit"}\n')
        p.stdin.flush()
        p.stdin.close()
    except Exception:
        pass
    try:
        p.wait(timeout=1.5)
    except Exception:
        try:
            p.terminate()
        except Exception:
            pass


def send(msg):
    global _pending
    data = (json.dumps(msg) + "\n").encode("ascii")
    with _lock:
        p = _proc
    if p is None or p.poll() is not None:
        _pending = data
        start_engine()
        return
    try:
        p.stdin.write(data)
        p.stdin.flush()
    except Exception:
        _pending = data
        start_engine()


def show_toast(path, count, force=False):
    cfg = _cfg or (read_cfg(_settings) if _settings else None)
    if not cfg or (not cfg["enabled"] and not force):
        return
    send({"cmd": "toast", "cfg": cfg, "ctx": {"path": path or "", "count": count}})


def show_test():
    now = datetime.datetime.now().strftime("%Y-%m-%d %H-%M-%S")
    fake = os.path.join(os.path.expanduser("~"), "Videos", "Replay %s.mp4" % now)
    show_toast(fake, _save_count + 1, force=True)


# --------------------------------------------------------------------------- #
# OBS callbacks
# --------------------------------------------------------------------------- #
def on_event(event):
    global _save_count
    if event == obs.OBS_FRONTEND_EVENT_REPLAY_BUFFER_SAVED:
        _save_count += 1
        path = ""
        try:
            path = obs.obs_frontend_get_last_replay() or ""
        except Exception:
            pass
        show_toast(path, _save_count)
    elif event == obs.OBS_FRONTEND_EVENT_EXIT:
        stop_engine()


def _preview_fire():
    obs.timer_remove(_preview_fire)
    show_test()


def _test_clicked(props, prop):
    show_test()
    return False


def _restart_clicked(props, prop):
    stop_engine()
    start_engine()
    return False


def _export_clicked(props, prop):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
            json.dump(_to_jsonable(read_cfg(_settings)), fh, indent=2)
        log("Saved settings to %s" % SETTINGS_FILE)
    except Exception as e:
        log("Could not save settings: %s" % e, obs.LOG_WARNING)
    return False


def _import_clicked(props, prop):
    vals = _load_file_values()
    if not vals:
        log("No readable settings file at %s" % SETTINGS_FILE, obs.LOG_WARNING)
        return False
    for k, v in vals.items():
        _set_value(_settings, k, v)
    script_update(_settings)
    show_test()
    return True


def script_description():
    return ("<b>Replay Toast</b><br>Desktop toast notification whenever the Replay Buffer is saved. "
            "Requires <code>python -m pip install PySide6</code> in the Python OBS uses. "
            "Keep <code>replay_toast_daemon.py</code> in the same folder as this script.")


def script_defaults(settings):
    fv = _load_file_values()
    for f in FIELDS:
        k, kind, d = f["key"], f["kind"], f["default"]
        d = fv.get(k, d)
        if kind == "bool":
            obs.obs_data_set_default_bool(settings, k, d)
        elif kind in ("int", "color", "color_a"):
            obs.obs_data_set_default_int(settings, k, d)
        elif kind == "float":
            obs.obs_data_set_default_double(settings, k, d)
        elif kind == "font":
            o = obs.obs_data_create()
            obs.obs_data_set_string(o, "face", d or "Segoe UI")
            obs.obs_data_set_string(o, "style", "Regular")
            obs.obs_data_set_int(o, "size", 16)
            obs.obs_data_set_int(o, "flags", 0)
            obs.obs_data_set_default_obj(settings, k, o)
            obs.obs_data_release(o)
        else:
            obs.obs_data_set_default_string(settings, k, d)


def _add_prop(gp, f):
    k, kind, label = f["key"], f["kind"], f["label"]
    if kind == "bool":
        obs.obs_properties_add_bool(gp, k, label)
    elif kind == "int":
        obs.obs_properties_add_int_slider(gp, k, label, f["min"], f["max"], f["step"])
    elif kind == "float":
        obs.obs_properties_add_float_slider(gp, k, label, f["min"], f["max"], f["step"])
    elif kind == "text":
        obs.obs_properties_add_text(gp, k, label, obs.OBS_TEXT_DEFAULT)
    elif kind == "file_img":
        obs.obs_properties_add_path(gp, k, label, obs.OBS_PATH_FILE,
                                    "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.svg)", None)
    elif kind == "file_snd":
        obs.obs_properties_add_path(gp, k, label, obs.OBS_PATH_FILE, "WAV audio (*.wav)", None)
    elif kind == "color":
        obs.obs_properties_add_color(gp, k, label)
    elif kind == "color_a":
        obs.obs_properties_add_color_alpha(gp, k, label)
    elif kind == "font":
        obs.obs_properties_add_font(gp, k, label)
    elif kind == "list":
        p = obs.obs_properties_add_list(gp, k, label, obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING)
        for lab, val in f["choices"]:
            obs.obs_property_list_add_string(p, lab, val)


def script_properties():
    props = obs.obs_properties_create()
    obs.obs_properties_add_button(props, "test", "Show test toast", _test_clicked)

    for gkey, glabel in GROUPS:
        gp = obs.obs_properties_create()
        for f in FIELDS:
            if f["group"] == gkey:
                _add_prop(gp, f)
        obs.obs_properties_add_group(props, "grp_" + gkey, glabel, obs.OBS_GROUP_NORMAL, gp)

    obs.obs_properties_add_button(props, "export", "Export settings to file (saves as new defaults)",
                                  _export_clicked)
    obs.obs_properties_add_button(props, "import", "Load settings from file", _import_clicked)
    obs.obs_properties_add_button(props, "restart", "Restart toast engine", _restart_clicked)
    return props


def script_update(settings):
    global _settings, _cfg, _cfg_json, _last_python
    _settings = settings
    _cfg = read_cfg(settings)
    new_json = json.dumps(_cfg, sort_keys=True)
    changed = _cfg_json is not None and new_json != _cfg_json
    _cfg_json = new_json

    if _last_python is not None and _cfg["python_path"] != _last_python:
        stop_engine()
        start_engine()
    _last_python = _cfg["python_path"]

    if changed and _cfg["auto_preview"]:
        obs.timer_remove(_preview_fire)
        obs.timer_add(_preview_fire, 500)


def script_load(settings):
    global _script_dir, _settings, _cfg, _cfg_json, _last_python
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    _settings = settings
    _cfg = read_cfg(settings)
    _cfg_json = json.dumps(_cfg, sort_keys=True)
    _last_python = _cfg["python_path"]
    obs.obs_frontend_add_event_callback(on_event)
    start_engine()


def script_unload():
    obs.timer_remove(_preview_fire)
    stop_engine()
