# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI of the review comments: the "X" command, the balloon in the 3D view, the comment dialog."""

import os

import FreeCAD as App
import FreeCADGui as Gui

from . import review

ICON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Resources", "icons",
                    "MCP_Comment.svg")
SURROUNDINGS_PAPER_MM = 20.0  # radius of the surroundings, on paper


def surroundings_radius(doc):
    from .handlers.draft2d import annotation_scale

    return SURROUNDINGS_PAPER_MM * annotation_scale(doc)


class ViewProviderReviewComment:
    """A dot in the status colour and "X<n>: text", the same size on screen at any zoom."""

    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        from pivy import coin

        self.Object = vobj.Object
        self.root = coin.SoSeparator()
        self.trans = coin.SoTranslation()
        self.color = coin.SoBaseColor()
        coords = coin.SoCoordinate3()
        coords.point.setValue(0, 0, 0)
        marker = coin.SoMarkerSet()
        marker.markerIndex = Gui.getMarkerIndex("CIRCLE_FILLED", 15)
        font = coin.SoFont()
        font.name = "Arial:Bold"
        font.size = 14
        self.text = coin.SoText2()
        for node in (self.trans, self.color, coords, marker, font, self.text):
            self.root.addChild(node)
        vobj.addDisplayMode(self.root, "Default")
        self.updateData(vobj.Object, "Status")

    def getDisplayModes(self, vobj):
        return ["Default"]

    def getDefaultDisplayMode(self):
        return "Default"

    def setDisplayMode(self, mode):
        return mode

    def updateData(self, obj, prop):
        if not hasattr(self, "text") or prop not in ("Status", "Text", "Number", "Position"):
            return
        p = obj.Position
        self.trans.translation.setValue(p.x, p.y, p.z + 1.0)  # just above the drawing plane
        self.color.rgb.setValue(*review.COLORS.get(obj.Status, review.COLORS[review.OPEN]))
        short = obj.Text if len(obj.Text) <= 40 else obj.Text[:37] + "..."
        status = "" if obj.Status == review.OPEN else f" [{obj.Status.lower()}]"
        self.text.string.setValue(f"   X{obj.Number}{status}: {short}")

    def onChanged(self, vobj, prop):
        pass

    def getIcon(self):
        return ICON

    def doubleClicked(self, vobj):
        CommentDialog(vobj.Object).exec()
        return True

    def dumps(self):
        return None

    def loads(self, state):
        return None

    __getstate__ = dumps

    def __setstate__(self, state):
        return None


class CommentDialog:
    """Shows a comment and its history; edits the text, marks it fixed or reopens it."""

    def __init__(self, obj):
        from PySide import QtWidgets

        self.obj = obj
        self.dialog = QtWidgets.QDialog(Gui.getMainWindow())
        self.dialog.setWindowTitle(f"Comentário X{obj.Number} - {obj.Status}")
        layout = QtWidgets.QVBoxLayout(self.dialog)
        target = obj.Target.Label if obj.Target else "nenhum objeto sob o clique"
        layout.addWidget(QtWidgets.QLabel(f"Sobre: {target}"))
        self.text = QtWidgets.QPlainTextEdit(obj.Text)
        layout.addWidget(self.text)
        layout.addWidget(QtWidgets.QLabel("Histórico:"))
        history = QtWidgets.QListWidget()
        history.addItems(list(obj.History))
        layout.addWidget(history)
        buttons = QtWidgets.QHBoxLayout()
        for caption, handler, enabled in (
                ("Salvar texto", self.save, True),
                ("Marcar corrigido", self.fix, obj.Status != review.FIXED),
                ("Reabrir", self.reopen, obj.Status == review.FIXED),
                ("Fechar", self.dialog.reject, True)):
            button = QtWidgets.QPushButton(caption)
            button.setEnabled(enabled)
            button.clicked.connect(handler)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.dialog.resize(520, 420)

    def exec(self):
        return self.dialog.exec()

    def _change(self, title, change):
        doc = self.obj.Document
        doc.openTransaction(title)
        change()
        doc.commitTransaction()
        doc.recompute()
        self.dialog.accept()

    def save(self):
        text = self.text.toPlainText().strip()
        if text and text != self.obj.Text:
            self._change("Editar comentário", lambda: review.set_text(self.obj, text))
        else:
            self.dialog.accept()

    def fix(self):
        self._change("Comentário corrigido", lambda: review.set_status(self.obj, review.FIXED))

    def reopen(self):
        from PySide import QtWidgets

        reason, ok = QtWidgets.QInputDialog.getMultiLineText(
            self.dialog, f"Reabrir X{self.obj.Number}", "Por que reabrir?")
        if ok:
            self._change("Reabrir comentário", lambda: review.set_status(self.obj, review.REOPENED, reason.strip()))


class _PointPicker:
    """Waits for one click in the 3D view, then asks for the comment text."""

    def __init__(self):
        from PySide import QtCore, QtWidgets

        self.doc = App.ActiveDocument
        self.view = Gui.ActiveDocument.ActiveView
        self.click_cb = self.view.addEventCallback("SoMouseButtonEvent", self.on_click)
        self.key_cb = self.view.addEventCallback("SoKeyboardEvent", self.on_key)
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CrossCursor)
        App.Console.PrintMessage("Comentário: clique no ponto da planta (Esc cancela)\n")

    def finish(self):
        self.view.removeEventCallback("SoMouseButtonEvent", self.click_cb)
        self.view.removeEventCallback("SoKeyboardEvent", self.key_cb)
        from PySide import QtWidgets

        QtWidgets.QApplication.restoreOverrideCursor()

    def on_key(self, event):
        if event["State"] == "DOWN" and event["Key"] == "ESCAPE":
            self.finish()

    def on_click(self, event):
        if event["State"] != "DOWN" or event["Button"] != "BUTTON1":
            return
        pos = event["Position"]
        info = self.view.getObjectInfo(pos)
        target = None
        if info:
            point = App.Vector(info["x"], info["y"], info["z"])
            target = self.doc.getObject(info["Object"])
        else:
            point = self._on_drawing_plane(pos)
        self.finish()
        from PySide import QtCore

        # Ask after the Coin event returns: a modal dialog inside the callback blocks the view.
        QtCore.QTimer.singleShot(0, lambda: self.ask(point, target))

    def _on_drawing_plane(self, pos):
        """The clicked point on the plane z = 0 (the plane of 2D plans)."""
        point = self.view.getPoint(pos)
        direction = self.view.getViewDirection()
        if abs(direction.z) > 1e-6:
            point = point + direction * (-point.z / direction.z)
        return point

    def ask(self, point, target):
        from PySide import QtWidgets

        target = review.meaningful_target(target)
        about = f"Sobre: {target.Label}\n" if target else ""
        number = max((c.Number for c in review.comments(self.doc)), default=0) + 1
        text, ok = QtWidgets.QInputDialog.getMultiLineText(
            Gui.getMainWindow(), f"Comentário X{number}", f"{about}O que deve ser corrigido?")
        text = text.strip()
        if not ok or not text:
            return
        with _Busy(f"Comentário X{number}",
                   "Registrando o comentário: verificando o que há em volta do ponto...") as busy:
            self.doc.openTransaction("Comentário de revisão")
            review.make_comment(self.doc, point, text, target, radius=surroundings_radius(self.doc),
                                progress=busy.progress)
            self.doc.commitTransaction()
            self.doc.recompute()


class _Busy:
    """Wait cursor, and a progress bar if the work takes more than a moment (big drawings)."""

    def __init__(self, title, message):
        self.title, self.message = title, message

    def __enter__(self):
        from PySide import QtCore, QtWidgets

        self.app = QtWidgets.QApplication
        self.app.setOverrideCursor(QtCore.Qt.WaitCursor)
        Gui.getMainWindow().statusBar().showMessage(self.message)
        self.dialog = QtWidgets.QProgressDialog(self.message, "", 0, 100, Gui.getMainWindow())
        self.dialog.setWindowTitle(self.title)
        self.dialog.setCancelButton(None)  # the work cannot be interrupted halfway
        self.dialog.setWindowModality(QtCore.Qt.WindowModal)
        self.dialog.setMinimumDuration(300)  # shown only if it is not over by then
        self.dialog.setValue(0)
        return self

    def progress(self, done, total):
        self.dialog.setMaximum(max(total, 1))
        self.dialog.setValue(done)
        self.app.processEvents()

    def __exit__(self, *exc):
        self.dialog.reset()
        self.dialog.close()
        self.dialog.deleteLater()
        Gui.getMainWindow().statusBar().clearMessage()
        self.app.restoreOverrideCursor()
        return False


class AddCommentCommand:
    def GetResources(self):
        return {"Pixmap": ICON, "MenuText": "X - Comentário de revisão",
                "ToolTip": "Clique aqui e depois num ponto da planta para deixar um comentário "
                           "(o Claude percorre os comentários e corrige)"}

    def IsActive(self):
        return App.ActiveDocument is not None and Gui.ActiveDocument is not None \
            and hasattr(Gui.ActiveDocument.ActiveView, "addEventCallback")

    def Activated(self):
        _PointPicker()


class _Manipulator:
    """Adds the command to every workbench: the File toolbar and the Tools menu."""

    def modifyToolBars(self):
        return [{"append": "MCP_AddComment", "toolBar": "File"}]

    def modifyMenuBar(self):
        return [{"append": "MCP_AddComment", "menuItem": "Std_DlgParameter"}]


def install():
    Gui.addCommand("MCP_AddComment", AddCommentCommand())
    Gui.addWorkbenchManipulator(_Manipulator())
