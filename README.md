# Replay Toast for OBS

A desktop toast notification that pops up whenever you save the OBS **Replay Buffer**, in the spirit of NVIDIA ShadowPlay's "clip saved" popup, but fully customizable.

The default look is deliberately minimal: a flat, square card with a muted grey checkmark, the clip's filename, and a quick fade. Everything about it can be changed from OBS's script settings.

<img width="400" height="225" alt="Replay 2026-11-11 00-44-03-00 00 22 000-00 00 28 483" src="https://github.com/user-attachments/assets/1157dd9a-531a-4ab2-b810-b25ec2b1f01d" />


## AI disclosure

This project was created with the help of AI. The code was written by Anthropic's Claude based on my requirements and feedback, and I tested and iterated on it in OBS. As with any AI-assisted code, review it before relying on it, and please report bugs by opening an issue. It is provided as-is, without warranty.

## Features

- Appears on your desktop each time the replay buffer is saved
- Resolution-independent: all sizes are authored at 1080p and scale to the monitor, so it looks the same on 1080p, 1440p, 4K and ultrawide, and stays sharp on HiDPI displays
- Customizable colors (with alpha), gradient, border, corner radius (round or squircle), padding, shadow/glow
- Built-in icons (replay arrow, checkmark, play, record dot) or your own image, plus an optional background image
- Title and subtitle text with tokens: `{filename}` `{name}` `{ext}` `{folder}` `{path}` `{time}` `{time12}` `{date}` `{count}`
- Slide / fade / pop animations with several easing curves, countdown bar, optional sound
- Choose which monitor and which screen corner or edge
- Click-through by default; or turn that off so left-click opens the clip's folder and right-click dismisses
- Live preview while you tweak settings

## Requirements

- OBS Studio 28 or newer
- Python 3.9+ installed and selected in OBS (**Tools → Scripts → Python Settings**)
- [PySide6](https://pypi.org/project/PySide6/)

## Install

1. Install PySide6 into the **same Python that OBS is pointed at**. Using that interpreter's full path avoids installing it into the wrong Python when you have several:

   ```
   "C:\Path\To\Your\Python\python.exe" -m pip install PySide6
   ```

2. Download `replay_toast.py` and `replay_toast_daemon.py` and put them **in the same folder**.
3. In OBS: **Tools → Scripts → +**, then pick `replay_toast.py`.
4. Click **Show test toast**.

Make sure the Replay Buffer is enabled in OBS (Settings → Output → Replay Buffer) and start it, then save a replay.

## How it works

OBS scripts can't draw on the desktop, so `replay_toast.py` listens for the "replay buffer saved" event and sends the details to a small helper process (`replay_toast_daemon.py`) that renders the toast with Qt. The helper starts when the script loads and closes when OBS exits.

## Customizing

All settings are in the script's panel in OBS, grouped into Position & Size, Colors & Shape, Shadow & Glow, Text, Icon & Graphics, Animation & Timing, and Behavior & Advanced. With **Auto-preview** on, the toast re-appears whenever you change a setting.

- **Export settings to file** saves your setup to `replay_toast_settings.json` next to the script. That file is then used as the defaults when the script loads.
- **Load settings from file** re-applies that file to the sliders.
- To change the built-in defaults for everyone, edit the `DEFAULT_LOOK` block in `replay_toast.py`.

## Troubleshooting

- **"PySide6 not found" in the Script Log**: PySide6 isn't installed in the Python the script found. Run the install command from the log (or from step 1) exactly as shown, then click **Restart toast engine**. You can also set a specific Python in **Behavior & Advanced → Python executable**.
- **Nothing shows in a game**: games in *exclusive fullscreen* can hide overlay windows. Use borderless / windowed fullscreen.
- **Toast appears on the wrong screen**: set **Monitor** (0 = primary, 1 = first screen, and so on).
- **Linux/Wayland**: window positioning may be ignored by the compositor.

## Files

| File | Purpose |
|---|---|
| `replay_toast.py` | The OBS script: settings UI, event hook, launches the helper |
| `replay_toast_daemon.py` | The renderer that draws the toast (PySide6) |
