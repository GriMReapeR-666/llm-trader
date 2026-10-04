"""MT5 bridge server — runs under *Windows* Python next to the terminal.

Pairs with the ``MetaTrader5`` shim in this directory.  Start it inside the
MetaTrader 5 Wine prefix on a Mac (``scripts/mac/mt5_wine.sh serve``) or on a
Windows VM/VPS:

    python mt5_bridge_server.py --key-file mt5_bridge.key [--port 18812]
                                [--terminal "C:\\Program Files\\MetaTrader 5\\terminal64.exe"]

Stdlib + the official ``MetaTrader5`` package only.  Requests are pickled over
``multiprocessing.connection`` with HMAC authentication (the shared key), so
only holders of the key can talk to it.  It binds to 127.0.0.1 by default;
reach a remote box through an SSH tunnel rather than exposing the port.

Every client keeps the real package's per-process semantics: API calls fail
with "No IPC connection" until that client calls ``initialize()``, and the
terminal is only shut down when the last attached client calls
``shutdown()``.  All MT5 calls are serialised behind one lock.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
from multiprocessing.connection import Listener

log = logging.getLogger("mt5_bridge")

NO_IPC = (-10004, "No IPC connection")

# Lifecycle calls handled specially; everything else must be a public callable
# of the real package (never an arbitrary attribute).
_LIFECYCLE = {"initialize", "shutdown", "last_error"}


def encode(value):
    """Turn MT5 results into plain, version-independent picklable data."""
    # The real package returns C struct sequences (AccountInfo, SymbolInfo, ...):
    # tuple subclasses with _asdict() but no _fields.
    if isinstance(value, tuple) and hasattr(value, "_asdict"):
        items = value._asdict()
        return ("__nt__", type(value).__name__, tuple(items),
                [encode(v) for v in items.values()])
    if isinstance(value, tuple):
        return ("__tuple__", [encode(v) for v in value])
    if isinstance(value, list):
        return [encode(v) for v in value]
    if isinstance(value, dict):
        return {k: encode(v) for k, v in value.items()}
    if type(value).__module__ == "numpy":
        if hasattr(value, "dtype") and hasattr(value, "shape") and hasattr(value, "tobytes"):
            if value.shape == ():  # numpy scalar
                return encode(value.item())
            dt = value.dtype
            descr = [tuple(f) for f in dt.descr] if dt.names else dt.str
            return ("__nd__", descr, tuple(value.shape), value.tobytes())
    return value


class Bridge:
    def __init__(self, mt5, terminal_path: str | None = None):
        self.mt5 = mt5
        self.terminal_path = terminal_path
        self.lock = threading.Lock()
        self.attached: set[int] = set()
        self.functions = sorted(
            n for n in dir(mt5)
            if not n.startswith("_") and n.islower() and callable(getattr(mt5, n))
        )
        self.constants = {
            n: getattr(mt5, n) for n in dir(mt5)
            if n.isupper() and isinstance(getattr(mt5, n), int)
        }

    def _last_error(self):
        try:
            return tuple(self.mt5.last_error())
        except Exception:
            return None

    def call(self, client_id: int, name: str, args, kwargs):
        """Returns (status, value, last_error)."""
        if name == "initialize":
            if self.terminal_path and "path" not in kwargs and not args:
                kwargs = dict(kwargs, path=self.terminal_path)
            path = kwargs.get("path")
            if path and not os.path.exists(path):
                # A macOS path from the client is meaningless here.
                log.warning("ignoring initialize(path=%r): not found on bridge host", path)
                kwargs = {k: v for k, v in kwargs.items() if k != "path"}
            with self.lock:
                ok = bool(self.mt5.initialize(*args, **kwargs))
                err = self._last_error()
            if ok:
                self.attached.add(client_id)
            return "ok", ok, err
        if name == "shutdown":
            return "ok", self.detach(client_id), None
        if name not in self.functions or name in _LIFECYCLE:
            return "error", f"unknown MetaTrader5 function {name!r}", None
        if client_id not in self.attached:
            return "ok", None, NO_IPC
        with self.lock:
            result = getattr(self.mt5, name)(*args, **kwargs)
            err = self._last_error()
        return "ok", encode(result), err

    def detach(self, client_id: int):
        with self.lock:
            was = client_id in self.attached
            self.attached.discard(client_id)
            if was and not self.attached:
                log.info("last client detached; shutting the terminal session down")
                self.mt5.shutdown()
        return None

    def serve_client(self, conn, client_id: int):
        log.info("client %d connected", client_id)
        try:
            while True:
                try:
                    msg = conn.recv()
                except (EOFError, OSError):
                    break
                op = msg[0]
                try:
                    if op == "hello":
                        reply = ("ok", {"functions": self.functions,
                                        "constants": self.constants,
                                        "python": sys.version}, None)
                    elif op == "call":
                        _, name, args, kwargs = msg
                        reply = self.call(client_id, name, tuple(args), dict(kwargs))
                    else:
                        reply = ("error", f"unknown op {op!r}", None)
                except Exception as exc:  # never let one bad call kill the bridge
                    log.exception("call failed")
                    reply = ("error", f"{type(exc).__name__}: {exc}", None)
                try:
                    conn.send(reply)
                except (EOFError, OSError):
                    break  # client went away mid-reply (e.g. its call timed out)
        finally:
            self.detach(client_id)
            conn.close()
            log.info("client %d disconnected", client_id)


def serve(mt5, host: str, port: int, key: bytes, terminal_path: str | None = None,
          ready: threading.Event | None = None, stop: threading.Event | None = None):
    bridge = Bridge(mt5, terminal_path)
    listener = Listener((host, port), family="AF_INET", authkey=key)
    log.info("mt5 bridge listening on %s:%d (%d functions)", host, port, len(bridge.functions))
    if ready is not None:
        ready.set()
    next_id = 0
    try:
        while stop is None or not stop.is_set():
            try:
                conn = listener.accept()
            except Exception as exc:  # bad key / aborted handshake
                log.warning("rejected connection: %s", exc)
                continue
            next_id += 1
            threading.Thread(target=bridge.serve_client, args=(conn, next_id), daemon=True).start()
    finally:
        listener.close()


def _read_key(args) -> bytes:
    key = os.environ.get("MT5_BRIDGE_KEY", "")
    if not key and args.key_file:
        with open(args.key_file, encoding="utf-8") as fh:
            key = fh.read().strip()
    if not key:
        sys.exit("mt5 bridge: no key (use --key-file or MT5_BRIDGE_KEY)")
    return key.encode("utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=os.environ.get("MT5_BRIDGE_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("MT5_BRIDGE_PORT", "18812")))
    ap.add_argument("--key-file", default=os.environ.get("MT5_BRIDGE_KEY_FILE"))
    ap.add_argument("--terminal", default=os.environ.get("MT5_TERMINAL_PATH"),
                    help="terminal64.exe to attach when the client passes no path")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    # This file sits next to the macOS shim package; keep its directory off
    # sys.path so `import MetaTrader5` finds the real Windows package.
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.curdir) != here]
    try:
        import MetaTrader5 as mt5
    except ImportError:
        sys.exit("mt5 bridge: the MetaTrader5 package is not installed in this (Windows) Python")
    if getattr(mt5, "BRIDGE", False):
        sys.exit("mt5 bridge: imported the bridge shim, not the real package — run this under Windows Python")
    serve(mt5, args.host, args.port, _read_key(args), args.terminal)


if __name__ == "__main__":
    main()
