from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageEnhance

BASE_DIR = Path(__file__).resolve().parents[1]
FONT_PATH = BASE_DIR / "static" / "fonts" / "Glass_TTY_VT220.ttf"

WIDTH = 800
HEIGHT = 480

COLORS = {
    "background": (4, 8, 15),
    "panel": (18, 27, 39),
    "panel_soft": (16, 24, 36),
    "border": (82, 112, 152),
    "muted": (143, 164, 194),
    "hostile": (217, 83, 79),
    "friendly": (76, 175, 80),
    "mixed": (255, 193, 7),
    "sheffield": (148, 110, 255),
    "hermes": (255, 152, 0),
    "harrier_outline": (0, 188, 212),
    "grid_bg": (8, 14, 24),
    "grid_border": (60, 92, 126),
    "text_main": (229, 240, 255),
}

MARGIN = 20
HEADER_HEIGHT = 56
COL_HEADER_HEIGHT = 20
ROW_LABEL_WIDTH = 32
GRID_WIDTH = 520
GRID_HEIGHT = 320
GAP = 16
SIDEBAR_WIDTH = WIDTH - (MARGIN + ROW_LABEL_WIDTH + GRID_WIDTH + GAP + MARGIN)
FOOTER_HEIGHT = 24


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(str(FONT_PATH), size)
    except Exception:
        return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> Tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _cell_fill(classes: str) -> Tuple[int, int, int] | None:
    classes_set = set(classes.split())
    if "sys-cell--hostile" in classes_set:
        return COLORS["hostile"]
    if "sys-cell--friendly" in classes_set:
        return COLORS["friendly"]
    if "sys-cell--mixed" in classes_set:
        return COLORS["mixed"]
    if "sys-cell--sheffield" in classes_set:
        return COLORS["sheffield"]
    if "sys-cell--hermes" in classes_set:
        return COLORS["hermes"]
    return None


def _cell_outline(classes: str) -> Tuple[int, int, int] | None:
    classes_set = set(classes.split())
    if "sys-cell--harrier" in classes_set:
        return COLORS["harrier_outline"]
    return None


def _draw_header(draw: ImageDraw.ImageDraw, ctx: Dict[str, Any]) -> None:
    title_font = _load_font(28)
    subtitle_font = _load_font(16)
    summary_font = _load_font(18)

    title_y = MARGIN
    draw.text((MARGIN, title_y), "Hermes Radar", font=title_font, fill=COLORS["text_main"])
    draw.text(
        (MARGIN, title_y + 32),
        "Contact snapshot",
        font=subtitle_font,
        fill=COLORS["muted"],
    )

    summary = [
        ("Friendlies", str(ctx["friendly_total"])),
        ("Hostiles", str(ctx["hostile_total"])),
    ]
    x = WIDTH - MARGIN
    y = title_y + 6
    for label, value in reversed(summary):
        text = f"{label} {value}"
        w, h = _text_size(draw, text, summary_font)
        draw.text((x - w, y), text, font=summary_font, fill=COLORS["muted"])
        y += h + 4


def _draw_grid(draw: ImageDraw.ImageDraw, ctx: Dict[str, Any]) -> None:
    columns = ctx["columns"]
    rows = ctx["rows"]

    grid_left = MARGIN + ROW_LABEL_WIDTH
    grid_top = MARGIN + HEADER_HEIGHT + COL_HEADER_HEIGHT
    grid_right = grid_left + GRID_WIDTH
    grid_bottom = grid_top + GRID_HEIGHT

    # Panel background
    draw.rounded_rectangle(
        (
            MARGIN - 6,
            MARGIN + HEADER_HEIGHT - 10,
            MARGIN + ROW_LABEL_WIDTH + GRID_WIDTH + 6,
            grid_bottom + 10,
        ),
        radius=18,
        fill=COLORS["panel"],
        outline=tuple(int(c * 0.6) for c in COLORS["border"]),
    )

    # Grid background
    draw.rectangle(
        (grid_left, grid_top, grid_right, grid_bottom),
        fill=COLORS["grid_bg"],
        outline=COLORS["grid_border"],
    )

    grid_rows = len(rows)
    grid_cols = len(columns)
    cell_w = GRID_WIDTH / grid_cols
    cell_h = GRID_HEIGHT / grid_rows

    # Column headers
    header_font = _load_font(12)
    for idx, col_label in enumerate(columns):
        x0 = grid_left + int(round(idx * cell_w))
        x1 = grid_left + int(round((idx + 1) * cell_w))
        cx = (x0 + x1) // 2
        draw.text(
            (cx, grid_top - COL_HEADER_HEIGHT + 2),
            col_label,
            font=header_font,
            fill=COLORS["muted"],
            anchor="ma",
        )

    # Row labels and cells
    cell_font = _load_font(12)
    for r_idx, row in enumerate(rows):
        y0 = grid_top + int(round(r_idx * cell_h))
        y1 = grid_top + int(round((r_idx + 1) * cell_h))
        cy = (y0 + y1) // 2
        draw.text(
            (MARGIN + ROW_LABEL_WIDTH - 6, cy),
            row["label"],
            font=header_font,
            fill=COLORS["muted"],
            anchor="rm",
        )

        for c_idx, cell in enumerate(row["cells"]):
            x0 = grid_left + int(round(c_idx * cell_w))
            x1 = grid_left + int(round((c_idx + 1) * cell_w))
            fill = _cell_fill(cell["classes"])
            outline = COLORS["grid_border"]
            if fill:
                draw.rectangle((x0, y0, x1, y1), fill=fill, outline=outline)
            else:
                draw.rectangle((x0, y0, x1, y1), outline=outline)
            harrier_outline = _cell_outline(cell["classes"])
            if harrier_outline:
                draw.rectangle((x0 + 1, y0 + 1, x1 - 1, y1 - 1), outline=harrier_outline)
            label = cell.get("label") or ""
            if label:
                draw.text(
                    ((x0 + x1) // 2, (y0 + y1) // 2),
                    label,
                    font=cell_font,
                    fill=(10, 18, 28),
                    anchor="mm",
                )


def _draw_legend(draw: ImageDraw.ImageDraw, origin_x: int, origin_y: int) -> Tuple[int, int]:
    legend_font = _load_font(14)
    swatch_size = 14
    spacing_y = 18
    items = [
        ("hostile", "Hostile"),
        ("friendly", "Friendly"),
        ("hermes", "Hermes"),
        ("sheffield", "Sheffield"),
        ("harrier_outline", "Harrier Flight"),
        ("mixed", "Mixed"),
    ]
    x = origin_x
    y = origin_y
    for key, label in items:
        color = COLORS[key] if key != "harrier_outline" else COLORS["harrier_outline"]
        if key == "harrier_outline":
            draw.rectangle(
                (x, y, x + swatch_size, y + swatch_size),
                outline=color,
                fill=None,
                width=2,
            )
        else:
            draw.rectangle(
                (x, y, x + swatch_size, y + swatch_size),
                fill=color,
                outline=tuple(int(c * 0.6) for c in COLORS["border"]),
            )
        draw.text(
            (x + swatch_size + 8, y + swatch_size // 2),
            label,
            font=legend_font,
            fill=COLORS["muted"],
            anchor="lm",
        )
        y += spacing_y
    return x, y


def _draw_contacts(draw: ImageDraw.ImageDraw, ctx: Dict[str, Any], origin_x: int, origin_y: int, width: int) -> None:
    panel_top = origin_y
    panel_bottom = HEIGHT - MARGIN - FOOTER_HEIGHT - 6
    draw.rounded_rectangle(
        (
            origin_x - 6,
            panel_top - 10,
            origin_x + width + 6,
            panel_bottom + 10,
        ),
        radius=16,
        fill=COLORS["panel_soft"],
        outline=tuple(int(c * 0.6) for c in COLORS["border"]),
    )

    title_font = _load_font(18)
    meta_font = _load_font(14)
    draw.text(
        (origin_x, panel_top),
        "Priorities",
        font=title_font,
        fill=COLORS["muted"],
    )
    y = panel_top + 26
    contact_height = 42
    listed = 0
    for contact in ctx["contact_rows"]:
        if listed >= 8:
            break
        meta_parts = []
        if contact.get("range_nm"):
            meta_parts.append(f"{contact['range_nm']} nm")
        if contact.get("speed"):
            meta_parts.append(f"{contact['speed']} kts")
        if contact.get("course"):
            meta_parts.append(f"{contact['course']}°")
        meta = " · ".join(meta_parts)
        allegiance = contact["allegiance"].lower()
        if allegiance == "hostile":
            bg = (54, 20, 26)
            border = (217, 83, 79)
        elif allegiance == "friendly":
            bg = (22, 36, 16)
            border = (76, 175, 80)
        else:
            bg = (24, 30, 42)
            border = tuple(int(c * 0.6) for c in COLORS["border"])
        draw.rounded_rectangle(
            (origin_x, y, origin_x + width, y + contact_height),
            radius=10,
            fill=bg,
        )
        draw.rounded_rectangle(
            (origin_x, y, origin_x + width, y + contact_height),
            radius=10,
            outline=border,
            width=2,
        )
        draw.text(
            (origin_x + 10, y + contact_height / 2),
            contact["cell"],
            font=_load_font(16),
            fill=COLORS["text_main"],
            anchor="lm",
        )
        draw.text(
            (origin_x + 70, y + 12),
            contact["name"],
            font=_load_font(16),
            fill=COLORS["text_main"],
            anchor="la",
        )
        if meta:
            draw.text(
                (origin_x + 70, y + contact_height - 12),
                meta,
                font=meta_font,
                fill=COLORS["muted"],
                anchor="ls",
            )
        y += contact_height + 6
        listed += 1

    if listed == 0:
        empty_font = _load_font(14)
        draw.text(
            (origin_x + width / 2, panel_top + 60),
            "No contacts tracked.",
            font=empty_font,
            fill=COLORS["muted"],
            anchor="mm",
        )


def _draw_footer(draw: ImageDraw.ImageDraw, ctx: Dict[str, Any]) -> None:
    footer_font = _load_font(14)
    text = f"Generated {ctx['generated']}"
    w, h = _text_size(draw, text, footer_font)
    draw.text(
        (WIDTH - MARGIN - w, HEIGHT - MARGIN - FOOTER_HEIGHT / 2),
        text,
        font=footer_font,
        fill=COLORS["muted"],
    )


def render_radar_png(context: Dict[str, Any], output_path: Path) -> Path:
    """
    Render the radar snapshot context to an 800x480 PNG image suitable for TRMNL.

    Args:
        context: Data produced by `build_radar_view`.
        output_path: Where to write the PNG file.
    """
    image = Image.new("RGB", (WIDTH, HEIGHT), COLORS["background"])
    draw = ImageDraw.Draw(image)

    _draw_header(draw, context)
    _draw_grid(draw, context)
    legend_x = MARGIN + ROW_LABEL_WIDTH + GRID_WIDTH + GAP
    legend_y = MARGIN + HEADER_HEIGHT
    _, legend_end_y = _draw_legend(draw, legend_x, legend_y)
    _draw_contacts(draw, context, legend_x, legend_end_y + 10, SIDEBAR_WIDTH)
    _draw_footer(draw, context)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    grayscale = image.convert("L")
    grayscale = ImageEnhance.Contrast(grayscale).enhance(1.6)
    grayscale.save(output_path, format="PNG", optimize=True)
    return output_path


__all__ = ["render_radar_png"]




def render_test_pattern_png(output_path: Path) -> Path:
    """Generate a simple high-contrast test pattern."""
    image = Image.new("L", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(image)
    # Border
    draw.rectangle((0, 0, WIDTH - 1, HEIGHT - 1), outline=0, width=4)
    # Diagonal lines
    draw.line((0, 0, WIDTH, HEIGHT), fill=0, width=3)
    draw.line((0, HEIGHT, WIDTH, 0), fill=0, width=3)
    # Grid dots
    for x in range(0, WIDTH, 40):
        draw.rectangle((x, HEIGHT // 2 - 12, x + 20, HEIGHT // 2 + 12), fill=0 if (x // 40) % 2 == 0 else 255)
    # Text label
    font = _load_font(48)
    draw.text((WIDTH // 2, HEIGHT // 2 - 100), "TRMNL TEST", fill=0, anchor="mm", font=font)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG", optimize=True)
    return output_path
