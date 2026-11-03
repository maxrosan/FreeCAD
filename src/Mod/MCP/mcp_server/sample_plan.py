# SPDX-License-Identifier: LGPL-2.1-or-later

"""Write a small vector floor plan PDF with known sizes, for tests and demos.

The plan is drawn at 1:100 on an A4 landscape page:

* outer walls: 10.00 m x 6.00 m outside, 150 mm thick;
* one inner wall, 150 mm thick, whose centre is at x = 6.00 m;
* a door opening of 800 mm in the inner wall, with a quarter-circle swing;
* the room names "SALA" and "QUARTO" and the note "ESCALA 1:100".

Real coordinates (mm) have their origin at the outer bottom-left corner.
"""

import sys

PT_PER_MM = 72 / 25.4
SCALE = 100
ORIGIN_PT = (100.0, 120.0)  # page position of the plan origin
PAGE = (841.89, 595.28)  # A4 landscape
KAPPA = 0.5522847498  # cubic Bezier factor for a quarter circle

OUTER = (10000, 6000)
THICK = 150
INNER_WALL_X = 6000
DOOR = (2000, 2800)  # door opening along the inner wall, y from/to (800 mm)


def to_pt(x_mm, y_mm):
    """Real mm -> page points."""
    return (ORIGIN_PT[0] + x_mm / SCALE * PT_PER_MM, ORIGIN_PT[1] + y_mm / SCALE * PT_PER_MM)


def _plan_ops():
    ops = ["0 0 0 RG", "0.7 w"]
    w, h = OUTER
    t = THICK
    x0 = INNER_WALL_X - t / 2
    x1 = INNER_WALL_X + t / 2

    def rect(xa, ya, xb, yb):
        (pa, qa), (pb, qb) = to_pt(xa, ya), to_pt(xb, yb)
        ops.append(f"{pa:.4f} {qa:.4f} {pb - pa:.4f} {qb - qa:.4f} re S")

    def line(xa, ya, xb, yb):
        (pa, qa), (pb, qb) = to_pt(xa, ya), to_pt(xb, yb)
        ops.append(f"{pa:.4f} {qa:.4f} m {pb:.4f} {qb:.4f} l S")

    rect(0, 0, w, h)  # outside face of the outer walls
    rect(t, t, w - t, h - t)  # inside face
    # inner wall faces, interrupted by the door opening
    line(x0, t, x0, DOOR[0])
    line(x0, DOOR[1], x0, h - t)
    line(x1, t, x1, DOOR[0])
    line(x1, DOOR[1], x1, h - t)
    line(x0, DOOR[0], x1, DOOR[0])  # jambs
    line(x0, DOOR[1], x1, DOOR[1])

    # door leaf and swing (thinner line), hinged at (x1, DOOR[0]), opening to +x
    ops.append("0.25 w")
    r = DOOR[1] - DOOR[0]
    line(x1, DOOR[0], x1 + r, DOOR[0])
    (sx, sy), (ex, ey) = to_pt(x1 + r, DOOR[0]), to_pt(x1, DOOR[1])
    rp = r / SCALE * PT_PER_MM
    (cx, cy) = to_pt(x1, DOOR[0])
    ops.append(f"{sx:.4f} {sy:.4f} m {sx:.4f} {cy + KAPPA * rp:.4f} "
               f"{cx + KAPPA * rp:.4f} {ey:.4f} {ex:.4f} {ey:.4f} c S")

    def text(x_mm, y_mm, s, size=10):
        px, py = to_pt(x_mm, y_mm)
        ops.append(f"BT /F1 {size} Tf {px:.4f} {py:.4f} Td ({s}) Tj ET")

    text(2500, 3000, "SALA")
    text(7600, 3000, "QUARTO")
    text(0, -1200, "ESCALA 1:100", 9)
    return "\n".join(ops)


def write(path):
    content = _plan_ops().encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE[0]} {PAGE[1]}] "
         f"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>").encode(),
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    with open(path, "wb") as f:
        f.write(out)
    return path


if __name__ == "__main__":
    print(write(sys.argv[1] if len(sys.argv) > 1 else "sample_plan.pdf"))
