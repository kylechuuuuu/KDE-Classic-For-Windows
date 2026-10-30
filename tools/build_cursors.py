"""Build the KDE Classic Windows cursor scheme from the upstream SVG sources.

Upstream: https://github.com/na0miluv/kde-classic-cursors (CC0-1.0)

Usage (from the repo root):
    git clone https://github.com/na0miluv/kde-classic-cursors.git src
    uv run --with pillow python tools/build_cursors.py \
        --svg src/src/svg --config src/src/config --out .

Produces <out>/install.inf and <out>/*.cur (32/48/64 px per file) plus preview.png.
"""
from __future__ import annotations

import argparse
import struct
import subprocess
import zipfile
from pathlib import Path

from PIL import Image

VERSION = "1.1.0"

BASE = 32  # nominal size the SVG artwork and the upstream hotspots are authored for
SIZES = (32, 48, 64)

# Windows role -> (svg stem, output basename)
ROLES = [
    ("Arrow", "default", "arrow"),
    ("Help", "help", "help"),
    ("AppStarting", "progress", "appstarting"),
    ("Wait", "wait", "wait"),
    ("Crosshair", "crosshair", "crosshair"),
    ("IBeam", "text", "ibeam"),
    ("NWPen", "pointer", "nwpen"),
    ("No", "not-allowed", "no"),
    ("SizeNS", "size_ver", "sizens"),
    ("SizeWE", "size_hor", "sizewe"),
    ("SizeNWSE", "size_fdiag", "sizenwse"),
    ("SizeNESW", "size_bdiag", "sizenesw"),
    ("SizeAll", "fleur", "sizeall"),
    ("UpArrow", "up-arrow", "uparrow"),
    ("Hand", "pointer", "hand"),
    ("Pin", "crosshair", "pin"),
    ("Person", "pointer", "person"),
]

SCHEME = "KDE Classic"
CUR_DIR = "Cursors\\KDE Classic"


def read_hotspots(cfg_dir: Path) -> dict[str, tuple[int, int]]:
    """Upstream <name>.cursor line 1 is '<nominal> <xhot> <yhot> <png>' (nominal 24 == the 32px render)."""
    spots: dict[str, tuple[int, int]] = {}
    for cfg in cfg_dir.glob("*.cursor"):
        lines = [ln.strip() for ln in cfg.read_text(errors="ignore").splitlines() if ln.strip()]
        if not lines:
            continue
        parts = lines[0].split()
        if len(parts) < 3:
            continue
        try:
            spots[cfg.stem] = (int(parts[1]), int(parts[2]))
        except ValueError:
            continue
    return spots


def render(inkscape: str, svg_dir: Path, stem: str, size: int, work: Path) -> Path:
    out = work / f"png{size}" / f"{stem}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    svg = svg_dir / f"{stem}.svg"
    if not svg.exists():
        raise SystemExit(f"missing svg: {svg}")
    cmd = [
        inkscape, str(svg),
        "--export-type=png",
        f"--export-filename={out}",
        f"--export-width={size}",
        f"--export-height={size}",
        "--export-background-opacity=0",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not out.exists():
        raise SystemExit(f"inkscape failed for {stem}@{size}: {r.stderr[-400:]}")
    return out


def cur_frame(png: Path) -> bytes:
    """One cursor image as a 32bpp BMP DIB (BITMAPINFOHEADER + XOR pixels + AND mask)."""
    im = Image.open(png).convert("RGBA")
    w, h = im.size
    assert w == h, (png, im.size)
    px = im.load()

    header = struct.pack(
        "<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, w * h * 4, 0, 0, 0, 0,
    )
    xor = bytearray()
    for y in range(h - 1, -1, -1):  # bottom-up
        for x in range(w):
            r, g, b, a = px[x, y]
            xor += bytes((b, g, r, a))
    and_mask = b"\x00" * (((w + 31) // 32) * 4 * h)  # alpha does the masking
    return header + bytes(xor) + and_mask


def write_cur(path: Path, frames: list[tuple[Path, tuple[int, int]]]) -> None:
    blobs = [cur_frame(p) for p, _ in frames]
    offset = 6 + 16 * len(blobs)
    entries, body = bytearray(), bytearray()
    for (png, (hx, hy)), blob in zip(frames, blobs):
        w = Image.open(png).size[0]
        entries += struct.pack(
            "<BBBBHHII",
            0 if w >= 256 else w, 0 if w >= 256 else w,  # bWidth, bHeight
            0, 0,                                        # bColorCount, bReserved
            hx, hy,                                      # wPlanes/wBitCount hold the hotspot
            len(blob), offset,
        )
        body += blob
        offset += len(blob)
    path.write_bytes(struct.pack("<HHH", 0, 2, len(blobs)) + entries + body)


def inf_text() -> str:
    files = "\n".join(f"{r[2]}.cur" for r in ROLES)
    order = ",".join(f"%10%\\%CUR_DIR%\\%{r[2]}%" for r in ROLES)
    strings = "\n".join(f'{r[2]:<12} = "{r[2]}.cur"' for r in ROLES)
    return f"""; KDE Classic cursor scheme for Windows
; Version {VERSION}
; Source artwork: https://github.com/na0miluv/kde-classic-cursors (CC0-1.0)
; Right-click this file -> Install, then apply "KDE Classic" in main.cpl -> Pointers.

[Version]
Signature="$Windows NT$"

[DefaultInstall]
CopyFiles=Cursors.Files
AddReg=Cursors.Reg

[DestinationDirs]
Cursors.Files=10,"%CUR_DIR%"

[Cursors.Files]
{files}

[Cursors.Reg]
HKCU,"Control Panel\\Cursors\\Schemes","%SCHEME_NAME%",0x00020000,"{order}"

[Strings]
CUR_DIR     = "{CUR_DIR}"
SCHEME_NAME = "{SCHEME}"
{strings}
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--svg", required=True, type=Path, help="upstream src/svg directory")
    ap.add_argument("--config", required=True, type=Path, help="upstream src/config directory")
    ap.add_argument("--out", default=Path("."), type=Path, help="output directory")
    ap.add_argument("--inkscape", default="inkscape")
    ap.add_argument("--work", default=Path("build"), type=Path, help="scratch dir for rendered PNGs")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    args.work.mkdir(parents=True, exist_ok=True)
    spots = read_hotspots(args.config)

    rendered: dict[str, dict[int, Path]] = {}
    for _, stem, base in ROLES:
        rendered[base] = {s: render(args.inkscape, args.svg, stem, s, args.work) for s in SIZES}

    for _, stem, base in ROLES:
        hx, hy = spots.get(stem, (0, 0))
        frames = [(rendered[base][s], (round(hx * s / BASE), round(hy * s / BASE))) for s in SIZES]
        write_cur(args.out / f"{base}.cur", frames)
        print(f"{base}.cur  <- {stem}.svg  hotspot {hx},{hy}")

    (args.out / "install.inf").write_text(inf_text(), encoding="ascii", newline="\r\n")

    # preview sheet (6 columns)
    cell = BASE * 4
    sheet = Image.new("RGBA", (cell * 6, cell * 3), (245, 245, 245, 255))
    for i, (_, _, base) in enumerate(ROLES):
        im = Image.open(rendered[base][BASE]).convert("RGBA").resize((cell, cell), Image.NEAREST)
        sheet.alpha_composite(im, ((i % 6) * cell, (i // 6) * cell))
    sheet.save(args.out / "preview.png")

    print(f"\nwrote {len(ROLES)} .cur + install.inf + preview.png to {args.out}")


if __name__ == "__main__":
    main()
