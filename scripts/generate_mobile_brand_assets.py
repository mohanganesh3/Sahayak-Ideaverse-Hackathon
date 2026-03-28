#!/usr/bin/env python3
"""Generate Expo mobile branding assets from the provided SAHAYAK logo."""

from __future__ import annotations

from pathlib import Path
import colorsys

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_IMAGE = PROJECT_ROOT.parent / "A4D1BF24-4CEA-4E4F-B2BA-55F5777DDD87_1_201_a.jpeg"
ASSETS_DIR = PROJECT_ROOT / "mobile" / "assets"

ICON_BG = (250, 249, 246, 255)
ADAPTIVE_BG = (13, 148, 136, 255)

# Hand-tuned crop around the emblem only. This intentionally excludes the wordmark.
EMBLEM_CROP = (320, 80, 880, 645)


def _load_emblem() -> Image.Image:
    image = Image.open(SOURCE_IMAGE).convert("RGBA")
    return image.crop(EMBLEM_CROP)


def _make_transparent_logo(emblem: Image.Image) -> Image.Image:
    logo = emblem.copy()
    pixels = logo.load()
    width, height = logo.size
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            hue, sat, value = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            # Remove the soft off-white background while keeping saturated logo pixels.
            if value > 0.96 and sat < 0.08:
                pixels[x, y] = (255, 255, 255, 0)
            elif value > 0.90 and sat < 0.16:
                alpha = max(0, int((1.0 - value) * 255 * 5))
                pixels[x, y] = (r, g, b, min(a, alpha))
    return logo


def _fit_on_canvas(image: Image.Image, size: int, scale: float, background: tuple[int, int, int, int]) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), background)
    max_side = int(size * scale)
    fitted = image.convert("RGBA")
    fitted.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    x = (size - fitted.width) // 2
    y = (size - fitted.height) // 2
    canvas.alpha_composite(fitted, (x, y))
    return canvas


def main() -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    emblem = _load_emblem()
    transparent_logo = _make_transparent_logo(emblem)

    icon = _fit_on_canvas(transparent_logo, 1024, 0.78, ICON_BG)
    icon.convert("RGB").save(ASSETS_DIR / "icon.png", format="PNG")

    splash = _fit_on_canvas(transparent_logo, 1024, 0.62, (255, 255, 255, 0))
    splash.save(ASSETS_DIR / "splash-icon.png", format="PNG")

    adaptive_foreground = _fit_on_canvas(transparent_logo, 512, 0.72, (255, 255, 255, 0))
    adaptive_foreground.save(ASSETS_DIR / "android-icon-foreground.png", format="PNG")

    adaptive_bg = Image.new("RGBA", (512, 512), ADAPTIVE_BG)
    adaptive_bg.save(ASSETS_DIR / "android-icon-background.png", format="PNG")

    monochrome = _fit_on_canvas(transparent_logo, 512, 0.72, (255, 255, 255, 0)).convert("L")
    mono_rgba = Image.new("RGBA", monochrome.size, (255, 255, 255, 0))
    mono_pixels = monochrome.load()
    out_pixels = mono_rgba.load()
    for y in range(monochrome.height):
        for x in range(monochrome.width):
            value = mono_pixels[x, y]
            out_pixels[x, y] = (13, 148, 136, value)
    mono_rgba.save(ASSETS_DIR / "android-icon-monochrome.png", format="PNG")

    favicon = icon.resize((48, 48), Image.Resampling.LANCZOS)
    favicon.convert("RGB").save(ASSETS_DIR / "favicon.png", format="PNG")

    print("Generated assets in", ASSETS_DIR)


if __name__ == "__main__":
    main()
