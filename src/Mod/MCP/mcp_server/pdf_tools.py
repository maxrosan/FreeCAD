# SPDX-License-Identifier: LGPL-2.1-or-later

"""Read floor plan PDFs: page info, scale detection, vector extraction, rendering.

Coordinates:

* "pt": PDF points with the origin at the bottom-left of the page, y up
  (pdfplumber reports y down; it is flipped here).
* "px": pixels of an image made by ``render_page``, origin top-left, y down.
* "mm": real-world millimetres: ``(pt - origin_pt) * 25.4 / 72 * scale``,
  where ``scale`` is N for a drawing at 1:N.

Only depends on pdfplumber (MIT) and pypdfium2 (Apache/BSD), not on FreeCAD.
"""

import math
import os
import re
import tempfile
import time

MM_PER_PT = 25.4 / 72

_SCALE_PATTERNS = [
    re.compile(r"\b(?:ESC(?:ALA)?|SCALE|ECHELLE|MASSSTAB)\.?\s*[:=]?\s*1\s*[:/]\s*(\d{1,5})", re.I),
    re.compile(r"(?<![\d.,:/])1\s*[:/]\s*(\d{1,5})(?![\d.,:/])"),
]
_COMMON_SCALES = {1, 2, 5, 10, 20, 25, 50, 75, 100, 125, 200, 250, 500, 1000, 2000, 2500, 5000}


def _open(path):
    import pdfplumber

    if not os.path.isfile(path):
        raise FileNotFoundError(f"PDF not found: {path}")
    return pdfplumber.open(path)


def _page(pdf, page):
    if not 1 <= page <= len(pdf.pages):
        raise ValueError(f"Page {page} does not exist; the PDF has {len(pdf.pages)} pages")
    return pdf.pages[page - 1]


def _page_text(p):
    words = p.extract_words(keep_blank_chars=True, use_text_flow=True)
    return " ".join(w["text"] for w in words), words


def detect_scale_in_text(text):
    """Return the scale denominators written in ``text``, most explicit first."""
    found = []
    for i, pattern in enumerate(_SCALE_PATTERNS):
        for m in pattern.finditer(text):
            n = int(m.group(1))
            if n <= 0 or (i == 1 and n not in _COMMON_SCALES):
                continue
            entry = {"denominator": n, "text": m.group(0).strip(), "explicit": i == 0}
            if all(e["denominator"] != n for e in found):
                found.append(entry)
    return found


def pdf_info(path):
    with _open(path) as pdf:
        pages = []
        for number, p in enumerate(pdf.pages, start=1):
            text, words = _page_text(p)
            area = p.width * p.height
            image_area = sum(abs((im["x1"] - im["x0"]) * (im["bottom"] - im["top"])) for im in p.images)
            vectors = len(p.lines) + len(p.rects) + len(p.curves)
            coverage = image_area / area if area else 0
            if p.images and coverage > 0.5 and vectors < 50:
                kind = "raster"  # a scan or photo, maybe with a few vector marks on top
            elif vectors:
                kind = "vector"
            else:
                kind = "raster" if p.images else "text only"
            pages.append({
                "page": number,
                "size_pt": [round(p.width, 2), round(p.height, 2)],
                "size_mm": [round(p.width * MM_PER_PT, 1), round(p.height * MM_PER_PT, 1)],
                "vector_objects": vectors,
                "lines": len(p.lines), "rects": len(p.rects), "curves": len(p.curves),
                "images": len(p.images),
                "image_coverage": round(coverage, 3),
                "words": len(words),
                "kind": kind,
                "scale_candidates": detect_scale_in_text(text),
                "text_sample": text[:400],
            })
        return {"file": os.path.abspath(path), "pages": pages}


# --- vector extraction -----------------------------------------------------


def _bezier(p0, p1, p2, p3, n):
    out = []
    for i in range(1, n + 1):
        t = i / n
        a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t ** 2, t ** 3
        out.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
                    a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return out


def _subpaths(obj, height, bezier_segments):
    """Split a pdfplumber path into polylines in pt, y up."""
    flip = lambda q: (q[0], height - q[1])  # noqa: E731
    result, current, closed = [], [], False
    for op in obj.get("path") or []:
        kind = op[0]
        if kind == "m":
            if len(current) > 1:
                result.append((current, closed))
            current, closed = [flip(op[1])], False
        elif kind == "l" and current:
            current.append(flip(op[1]))
        elif kind == "c" and current:
            current.extend(_bezier(current[-1], flip(op[1]), flip(op[2]), flip(op[3]), bezier_segments))
        elif kind in ("v", "y") and current:
            # PDF shorthand curves: pdfplumber gives the two given points
            c1, end = flip(op[1]), flip(op[-1])
            current.extend(_bezier(current[-1], c1, c1, end, bezier_segments))
        elif kind == "h" and current:
            closed = True
    if len(current) > 1:
        result.append((current, closed))
    if not result and obj.get("pts"):
        pts = [flip(q) for q in obj["pts"]]
        if len(pts) > 1:
            result.append((pts, obj.get("object_type") == "rect"))
    return result


def _hex(color):
    if color is None:
        return "#000000"
    if isinstance(color, (int, float)):
        color = (color, color, color)
    color = tuple(color)
    if len(color) == 1:
        color = color * 3
    if len(color) == 4:  # CMYK
        c, m, y, k = color
        color = ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    try:
        return "#" + "".join(f"{max(0, min(255, round(float(v) * 255))):02x}" for v in color[:3])
    except (TypeError, ValueError):
        return "#000000"


def _in_crop(points, crop):
    if crop is None:
        return True
    x0, y0, x1, y1 = crop
    return all(x0 <= x <= x1 and y0 <= y <= y1 for x, y in points)


def _length_pt(points):
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def extract_page(path, page=1, crop=None, bezier_segments=8, include_text=True):
    """Vector paths and texts of a page, in pt (y up)."""
    with _open(path) as pdf:
        p = _page(pdf, page)
        h = p.height
        paths = []
        for obj in list(p.lines) + list(p.rects) + list(p.curves):
            if not obj.get("stroke", True) and not obj.get("fill"):
                continue
            for pts, closed in _subpaths(obj, h, bezier_segments):
                if not _in_crop(pts, crop):
                    continue
                paths.append({
                    "points": pts,
                    "closed": closed,
                    "linewidth": round(float(obj.get("linewidth") or 0), 3),
                    "color": _hex(obj.get("stroking_color") if obj.get("stroke", True)
                                  else obj.get("non_stroking_color")),
                    "kind": obj.get("object_type"),
                    "dashed": bool(obj.get("dash")),
                })
        texts = []
        if include_text:
            for w in p.extract_words(keep_blank_chars=True, extra_attrs=["upright"]):
                pos = (w["x0"], h - w["bottom"])
                if crop is None or _in_crop([pos], crop):
                    texts.append({"text": w["text"], "position": pos,
                                  "height": w["bottom"] - w["top"], "upright": w.get("upright", True)})
        return {"page_size_pt": (p.width, p.height), "paths": paths, "texts": texts}


def bounds(paths):
    xs = [x for path in paths for x, _ in path["points"]]
    ys = [y for path in paths for _, y in path["points"]]
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def pt_to_mm(q, origin_pt, scale):
    return [round((q[0] - origin_pt[0]) * MM_PER_PT * scale, 3),
            round((q[1] - origin_pt[1]) * MM_PER_PT * scale, 3)]


def extract_mm(path, page=1, scale=None, crop=None, origin_pt=None, min_length_mm=0.0,
               linewidths=None, colors=None, include_text=True, bezier_segments=8):
    """Vector paths and texts converted to real-world mm.

    ``scale`` is N for 1:N; when missing, a single explicit scale found in the
    page text is used. ``origin_pt`` defaults to the bottom-left of the
    extracted geometry, so the drawing starts near (0, 0).
    """
    raw = extract_page(path, page, crop, bezier_segments, include_text)
    paths = raw["paths"]
    if linewidths:
        wanted = [float(w) for w in linewidths]
        paths = [p for p in paths if any(abs(p["linewidth"] - w) < 0.01 for w in wanted)]
    if colors:
        wanted = {c.lower() for c in colors}
        paths = [p for p in paths if p["color"].lower() in wanted]
    if scale is None:
        info = pdf_info(path)["pages"][page - 1]
        explicit = [c for c in info["scale_candidates"] if c["explicit"]]
        if len(explicit) != 1:
            raise ValueError(
                "Scale unknown: pass scale (N for 1:N) or calibrate with a known length. "
                f"Candidates in the text: {info['scale_candidates']}")
        scale = explicit[0]["denominator"]
    scale = float(scale)
    if origin_pt is None:
        box = bounds(paths)
        origin_pt = (box[0], box[1]) if box else (0.0, 0.0)
    min_pt = float(min_length_mm) / MM_PER_PT / scale
    out_paths = []
    for p in paths:
        if _length_pt(p["points"]) < min_pt:
            continue
        out_paths.append({**p, "points": [pt_to_mm(q, origin_pt, scale) for q in p["points"]]})
    out_texts = [{**t, "position": pt_to_mm(t["position"], origin_pt, scale),
                  "height": round(t["height"] * MM_PER_PT * scale, 1)} for t in raw["texts"]]
    groups = {}
    for p in out_paths:
        key = f"lw{p['linewidth']:.2f} {p['color']}"
        groups[key] = groups.get(key, 0) + 1
    return {"scale": scale, "origin_pt": [round(origin_pt[0], 4), round(origin_pt[1], 4)],
            "page_size_pt": raw["page_size_pt"], "paths": out_paths, "texts": out_texts,
            "groups": groups}


# --- rendering -------------------------------------------------------------


def render_page(path, page=1, dpi=100, crop=None, max_pixels=2400, out_path=None):
    """Render a page (or a crop of it, in pt y up) to PNG.

    Returns the PNG path and the mapping between pixels and PDF points:
    ``x_pt = crop[0] + u * pt_per_px`` and ``y_pt = crop[3] - v * pt_per_px``.
    """
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(path)
    try:
        if not 1 <= page <= len(pdf):
            raise ValueError(f"Page {page} does not exist; the PDF has {len(pdf)} pages")
        p = pdf[page - 1]
        width, height = p.get_size()
        x0, y0, x1, y1 = crop if crop else (0.0, 0.0, width, height)
        x0, y0, x1, y1 = max(0.0, x0), max(0.0, y0), min(width, x1), min(height, y1)
        if x1 <= x0 or y1 <= y0:
            raise ValueError("The crop area is empty")
        scale = float(dpi) / 72
        longest = max(x1 - x0, y1 - y0) * scale
        if longest > max_pixels:
            scale *= max_pixels / longest
        bitmap = p.render(scale=scale, crop=(x0, y0, width - x1, height - y1))
        image = bitmap.to_pil()
    finally:
        pdf.close()
    if out_path is None:
        folder = os.path.join(tempfile.gettempdir(), "freecad_mcp")
        os.makedirs(folder, exist_ok=True)
        out_path = os.path.join(folder, f"pdf_p{page}_{int(time.time() * 1000)}.png")
    image.save(out_path)
    return {"path": out_path, "width_px": image.width, "height_px": image.height,
            "pt_per_px": 1 / scale, "dpi": round(scale * 72, 2),
            "crop_pt": [x0, y0, x1, y1], "page_size_pt": [width, height]}


def px_to_pt(q, render):
    x0, _, _, y1 = render["crop_pt"]
    return (x0 + q[0] * render["pt_per_px"], y1 - q[1] * render["pt_per_px"])


def calibrate(p1, p2, real_length_mm, space="pt", render=None):
    """Scale denominator N (for 1:N) from two points whose real distance is known."""
    if space == "px":
        if render is None:
            raise ValueError("Pixel coordinates need a render (call pdf_render_page first)")
        p1, p2 = px_to_pt(p1, render), px_to_pt(p2, render)
    distance_pt = math.dist(p1, p2)
    if distance_pt == 0:
        raise ValueError("The two points are the same")
    scale = float(real_length_mm) / (distance_pt * MM_PER_PT)
    return {"scale": scale, "distance_pt": distance_pt, "distance_paper_mm": distance_pt * MM_PER_PT,
            "nearest_standard": min(_COMMON_SCALES, key=lambda s: abs(s - scale))}
