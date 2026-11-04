# SPDX-License-Identifier: LGPL-2.1-or-later

"""Commands for review comments ("X" balloons): list them, look at them, mark them fixed, reopen."""

import FreeCAD as App

from .. import review
from ..registry import CommandError, command
from ..units import length, point
from .common import active_document, get_object, recompute

ASSISTANT = "Claude"
PENDING = (review.OPEN, review.REOPENED)


def _comment(doc, number):
    obj = review.find_comment(doc, number)
    if obj is None:
        raise CommandError(f"No comment X{number} in document {doc.Name}")
    return obj


@command("list_review_comments")
def list_review_comments(status="pendentes", radius=None):
    """Comments of the active document. status: "pendentes" (Aberto and Reaberto), "todos",
    or one of Aberto, Corrigido, Reaberto. Each comes with the object under the click and what
    is around it (radius in mm; default the one recorded with the comment)."""
    doc = active_document(create=False)
    found = review.comments(doc)
    if status == "pendentes":
        found = [c for c in found if c.Status in PENDING]
    elif status != "todos":
        if status not in review.STATUSES:
            raise CommandError(f"status must be 'pendentes', 'todos' or one of {review.STATUSES}")
        found = [c for c in found if c.Status == status]
    counts = {s: sum(1 for c in review.comments(doc) if c.Status == s) for s in review.STATUSES}
    return {"counts": counts, "comments": [review.describe_comment(c, radius) for c in found]}


@command("get_review_comment")
def get_review_comment(number, radius=None):
    return review.describe_comment(_comment(active_document(create=False), number), radius)


@command("focus_review_comment")
def focus_review_comment(number, size=None):
    """Top view centred on comment X<n>, `size` mm high (default: 4 x its surroundings radius, so the
    neighbourhood shows, not only the element under the X)."""
    if not App.GuiUp:
        raise CommandError("Focusing a comment needs FreeCAD with its GUI")
    from .view import _active_view

    doc = active_document(create=False)
    obj = _comment(doc, number)
    view = _active_view()
    view.viewTop()
    across = length(size, "size") if size is not None else 4.0 * (obj.Radius.Value or 1000.0)
    camera = view.getCameraNode()
    p = obj.Position
    camera.position.setValue(p.x, p.y, camera.position.getValue()[2])
    if hasattr(camera, "height"):  # orthographic camera
        camera.height.setValue(across)
    else:
        camera.heightAngle.setValue(0.7)
        camera.position.setValue(p.x, p.y, p.z + across)
    return review.describe_comment(obj)


def _set(number, status, note):
    doc = active_document(create=False)
    obj = _comment(doc, number)
    review.set_status(obj, status, note, ASSISTANT)
    recompute(doc)
    return review.describe_comment(obj)


@command("resolve_review_comment", transaction=True)
def resolve_review_comment(number, note=""):
    """Mark X<n> as fixed, saying what was done."""
    return _set(number, review.FIXED, note)


@command("reopen_review_comment", transaction=True)
def reopen_review_comment(number, reason=""):
    """Reopen a fixed comment (when the person asks)."""
    obj = _comment(active_document(create=False), number)
    if obj.Status != review.FIXED:
        raise CommandError(f"X{number} is {obj.Status}, not {review.FIXED}")
    return _set(number, review.REOPENED, reason)


@command("add_review_comment", transaction=True)
def add_review_comment(position, text, target=None, radius=1000, author=ASSISTANT):
    """Pin a comment at `position` (mm), optionally about object `target` (name or label)."""
    doc = active_document()
    obj = review.make_comment(doc, point(position, "position"), text,
                              get_object(doc, target) if target else None, author, length(radius, "radius"))
    recompute(doc)
    return review.describe_comment(obj)


@command("delete_review_comment", transaction=True)
def delete_review_comment(number):
    doc = active_document(create=False)
    obj = _comment(doc, number)
    doc.removeObject(obj.Name)
    return {"deleted": f"X{number}"}
