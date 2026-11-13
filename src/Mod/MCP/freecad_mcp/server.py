# SPDX-License-Identifier: LGPL-2.1-or-later

"""Local TCP command server.

Protocol: one JSON object per line. A request is
``{"id": ..., "method": "<command>", "params": {...}}`` and the reply is
``{"id": ..., "result": ...}`` or ``{"id": ..., "error": {"message": ...}}``.

The server only listens on 127.0.0.1. Qt delivers socket signals on the GUI
thread, so commands run there, like everything else that touches documents.
"""

import json

import FreeCAD as App
from PySide import QtCore, QtNetwork

from . import registry

_server = None


class CommandServer(QtCore.QObject):
    def __init__(self, port, parent=None):
        super().__init__(parent)
        self._buffers = {}
        self._busy = False  # a command is running (it repaints the window, so sockets may be read meanwhile)
        self._tcp = QtNetwork.QTcpServer(self)
        self._tcp.newConnection.connect(self._on_new_connection)
        if not self._tcp.listen(QtNetwork.QHostAddress(QtNetwork.QHostAddress.LocalHost), port):
            raise RuntimeError(self._tcp.errorString())

    @property
    def port(self):
        return self._tcp.serverPort()

    def close(self):
        self._tcp.close()

    def _on_new_connection(self):
        while self._tcp.hasPendingConnections():
            sock = self._tcp.nextPendingConnection()
            self._buffers[sock] = b""
            sock.readyRead.connect(lambda s=sock: self._on_ready_read(s))
            sock.disconnected.connect(lambda s=sock: self._on_disconnected(s))

    def _on_disconnected(self, sock):
        self._buffers.pop(sock, None)
        sock.deleteLater()

    def _on_ready_read(self, sock):
        self._buffers[sock] = self._buffers.get(sock, b"") + bytes(sock.readAll())
        self._drain()

    def _drain(self):
        """Run the waiting requests one at a time: a request that arrives while a command runs
        (and repaints the window) waits in the queue instead of running inside it."""
        if self._busy:
            return
        self._busy = True
        try:
            while True:
                sock = next((s for s, data in self._buffers.items() if b"\n" in data), None)
                if sock is None:
                    break
                line, self._buffers[sock] = self._buffers[sock].split(b"\n", 1)
                if not line.strip():
                    continue
                reply = handle_line(line)
                if sock in self._buffers:  # the client may have gone meanwhile
                    sock.write(reply.encode("utf-8") + b"\n")
                    sock.flush()
        finally:
            self._busy = False


def handle_line(line):
    """Parse one request line and return the JSON reply line."""
    req_id = None
    try:
        req = json.loads(line)
        req_id = req.get("id")
        reply = registry.call(req["method"], req.get("params"))
    except Exception as e:
        reply = {"error": {"message": f"Bad request: {e}"}}
    reply["id"] = req_id
    return json.dumps(reply, default=_to_json)


def _to_json(value):
    if isinstance(value, App.Vector):
        return [value.x, value.y, value.z]
    if isinstance(value, App.Units.Quantity):
        return value.Value
    return str(value)


def start(port=9875):
    global _server
    if _server is None:
        _server = CommandServer(port)
        App.Console.PrintLog(f"MCP: command server listening on 127.0.0.1:{_server.port}\n")
    return _server.port


def stop():
    global _server
    if _server is not None:
        _server.close()
        _server = None
