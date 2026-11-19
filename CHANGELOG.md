# Changelog

## 1.0.0

- 320×170 framebuffer viewfinder with DV-style HUD (REC/timecode, battery, exposure, zoom bar, scanline).
- Two grades: `NORMAL` (cool) and `KARAX` (warm film, grain, vignette, halation in stills).
- BGR→RGB correction for the CardputerZero IMX219 pipeline; 180° sensor rotation.
- Snapshots at 1640×1232 (cropped by zoom) with an instant freeze-frame shutter, no progress bar.
- 1.0–3.0× digital zoom with smooth easing; hold `9`/`0` to keep zooming.
- Filtered 320×170 @20 fps H.264 recording through ffmpeg.
- Hidden HUD (`I`), bilingual help (`H`/`TAB`, `L`), 960×540 preview downsized with BOX.
- Debian package, GitHub installer and AppStore manifest.
