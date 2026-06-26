# Building the Windows executable

`build_exe.py` produces a standalone, double-click Windows build of MLBDaily with
ffmpeg bundled — so end users need nothing installed.

## Prerequisites
- Windows
- Python 3.10+
- `pip install pyinstaller`

## Build
```bash
python build_exe.py
```

This will:
1. Download a slim **LGPL** ffmpeg *shared* build into `./ffmpeg/` (only if it's
   not already there). LGPL is enough because MLBDaily only remuxes (`-c copy`)
   and never re-encodes, so no GPL-only encoders are required.
2. Run PyInstaller to produce a windowed app at `dist/MLBDaily/`
   (`MLBDaily.exe` + an `_internal/` folder that includes ffmpeg/ffprobe).
3. Zip it to `dist/MLBDaily-v<version>-win64.zip`, with `LICENSE.txt`,
   `ffmpeg-LICENSE.txt`, and a `READ-ME-FIRST.txt` included.

Useful flags (keep heavy artifacts off a synced drive, etc.):
```bash
python build_exe.py --ffmpeg-dir ./ffmpeg --dist ./dist --work ./build
```

The version comes from `__version__` in `MLBDaily.py`. Bump it there before a
release.

## Verify
```bash
dist/MLBDaily/MLBDaily.exe --doctor     # exit code 0 = bundled ffmpeg works
```

## Publish a release
```bash
gh release create v<version> \
  "dist/MLBDaily-v<version>-win64.zip" \
  --title "MLBDaily v<version>" --notes "..."
```

## Notes
- **Licensing:** ffmpeg is called as a separate process (not linked), so MLBDaily
  stays MIT; the bundled ffmpeg is redistributed under the LGPL (its license ships
  in the zip).
- **SmartScreen:** the exe is unsigned, so Windows shows a one-time
  "unknown publisher" warning. Code signing removes it but requires a paid
  certificate.
- Build outputs (`build/`, `dist/`, `ffmpeg/`, `*.spec`) are gitignored.
