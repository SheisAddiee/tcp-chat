"""
Multi-user TCP chat server.

Concurrency model
-----------------
- Main thread: accept() loop on the listening socket.
- Per client, two threads:
    reader thread  -> reads frames, validates them, routes them
    writer thread  -> drains that client's outbound queue with sendall()
  Broadcasting only puts messages on queues, so one slow or stuck client
  can never block the server from delivering to everyone else.

Run:  python server.py            (host/port from .env or environment)
      python server.py --port 5000
"""

from __future__ import annotations

import argparse
import logging
import os
import queue
import re
import signal
import socket
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Dict, Optional

from protocol import FrameReader, FrameTooLarge, ProtocolError, encode

# ---------------------------------------------------------------- settings ---

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{2,20}$")
MAX_TEXT_CHARS = 2000
MAX_CLIENTS = 100
JOIN_TIMEOUT = 10          # seconds a new connection has to send "join"
IDLE_TIMEOUT = 60          # drop clients silent for this long (clients ping every 20s)
OUTBOUND_QUEUE_SIZE = 200  # if a client falls this far behind, drop it
RATE_LIMIT = (20, 10.0)    # max 20 messages per 10 seconds per client

log = logging.getLogger("chat-server")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_dotenv(path: str = ".env") -> None:
    """Tiny .env loader so we need no third-party packages."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def enable_keepalive(sock: socket.socket) -> None:
    """
    Ask the OS to probe idle connections. If the other machine vanishes
    (power cut, network dropped) without sending FIN or RST, keepalive
    probes eventually fail and recv() raises an error instead of waiting forever.
    """
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    for name, value in (("TCP_KEEPIDLE", 30), ("TCP_KEEPINTVL", 10), ("TCP_KEEPCNT", 3)):
        if hasattr(socket, name):  # Linux options; skipped on other OSes
            sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, name), value)


# ------------------------------------------------------------------ client ---

class ClientConnection:
    """One connected TCP client: its socket, identity and outbound queue."""

    def __init__(self, sock: socket.socket, addr: tuple):
        self.sock = sock
        self.addr = addr                       # (ip, port) of the client
        self.username: Optional[str] = None
        self.outbox: "queue.Queue[Optional[bytes]]" = queue.Queue(OUTBOUND_QUEUE_SIZE)
        self.closed = threading.Event()
        self.recent = deque()                  # timestamps for rate limiting
        self.writer = threading.Thread(target=self._write_loop, daemon=True)
        self.writer.start()

    @property
    def label(self) -> str:
        name = self.username or "?"
        return f"{name}@{self.addr[0]}:{self.addr[1]}"

    def send(self, message: dict) -> bool:
        """Queue a message for this client. Returns False if it was dropped."""
        if self.closed.is_set():
            return False
        try:
            self.outbox.put_nowait(encode(message))
            return True
        except queue.Full:
            log.warning("%s is too slow (queue full), disconnecting", self.label)
            self.close(force=True)
            return False

    def _write_loop(self) -> None:
        while True:
            data = self.outbox.get()
            if data is None:          # sentinel: flush done, close socket
                break
            try:
                self.sock.sendall(data)
            except OSError:
                break
        self._shutdown_socket()

    def close(self, force: bool = False) -> None:
        """
        Close the connection. Normally pending messages are flushed first
        (e.g. an error reply), then the writer thread closes the socket.
        """
        if self.closed.is_set():
            return
        self.closed.set()
        if force:
            self._shutdown_socket()
        try:
            self.outbox.put_nowait(None)
        except queue.Full:
            self._shutdown_socket()

    def _shutdown_socket(self) -> None:
        try:
            # shutdown() sends FIN to the client and wakes our reader thread
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()

    def rate_limited(self) -> bool:
        limit, window = RATE_LIMIT
        t = time.monotonic()
        while self.recent and t - self.recent[0] > window:
            self.recent.popleft()
        if len(self.recent) >= limit:
            return True
        self.recent.append(t)
        return False


# ------------------------------------------------------------------ server ---

class ChatServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 5000):
        self.host = host
        self.port = port
        self.clients: Dict[str, ClientConnection] = {}   # lower-case username -> client
        self.lock = threading.Lock()                      # guards self.clients
        self.stopping = threading.Event()
        self.listener: Optional[socket.socket] = None
        self.connection_count = 0

    # ---- lifecycle ----

    def bind(self) -> int:
        """Create the listening socket. Returns the actual port (useful when port=0)."""
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # SOCK_STREAM = TCP
        # Allow restarting immediately even if old connections are in TIME_WAIT
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind((self.host, self.port))
        self.listener.listen(64)   # backlog: completed handshakes waiting for accept()
        self.listener.settimeout(1.0)  # so the loop can notice shutdown requests
        self.port = self.listener.getsockname()[1]
        return self.port

    def serve_forever(self) -> None:
        if self.listener is None:
            self.bind()
        log.info("Listening on %s:%d (TCP)", self.host, self.port)
        while not self.stopping.is_set():
            try:
                # accept() returns a NEW socket for this client. The listening
                # socket stays on the same port and keeps accepting others.
                # Each connection is identified by its 4-tuple:
                # (client IP, client port, server IP, server port).
                sock, addr = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            self.connection_count += 1
            log.info("TCP connection accepted from %s:%d", *addr)
            threading.Thread(target=self._handle, args=(sock, addr), daemon=True).start()
        log.info("Accept loop stopped")

    def shutdown(self) -> None:
        if self.stopping.is_set():
            return
        log.info("Shutting down, notifying clients")
        self.stopping.set()
        with self.lock:
            clients = list(self.clients.values())
        for c in clients:
            c.send({"type": "system", "event": "shutdown", "text": "Server is shutting down", "ts": now_iso()})
            c.close()
        for c in clients:
            c.writer.join(timeout=2)
        if self.listener:
            self.listener.close()

    # ---- per-client handling ----

    def _handle(self, sock: socket.socket, addr: tuple) -> None:
        enable_keepalive(sock)
        client = ClientConnection(sock, addr)
        reason = "disconnected"
        try:
            reader = self._join(client)
            if reader is None:
                return
            sock.settimeout(IDLE_TIMEOUT)
            while True:
                try:
                    msg = reader.read()
                except ProtocolError as exc:
                    log.warning("%s sent a malformed message: %s", client.label, exc)
                    client.send({"type": "error", "code": "bad_message", "text": str(exc)})
                    continue
                if msg is None:
                    reason = "connection closed"          # FIN received
                    break
                if msg["type"] == "leave":
                    reason = "left"
                    break
                self._route(client, msg)
        except socket.timeout:
            reason = "timed out"
        except FrameTooLarge:
            reason = "sent an oversized message"
            log.warning("%s exceeded the frame size limit", client.label)
            client.send({"type": "error", "code": "too_large", "text": "Message too large"})
        except (ConnectionResetError, BrokenPipeError):
            reason = "connection reset"                   # RST / crashed client
        except OSError as exc:
            reason = f"socket error ({exc.__class__.__name__})"
        finally:
            self._remove(client, reason)

    def _join(self, client: ClientConnection) -> Optional[FrameReader]:
        """
        First message must be {"type":"join","username":...}.
        Returns this connection's FrameReader on success (it may already hold
        bytes that arrived right after the join), or None if rejected.
        """
        client.sock.settimeout(JOIN_TIMEOUT)
        reader = FrameReader(client.sock)
        try:
            msg = reader.read()
        except (ProtocolError, FrameTooLarge, OSError):
            msg = None

        def reject(code: str, text: str) -> None:
            log.warning("Rejected %s:%d - %s", client.addr[0], client.addr[1], text)
            client.send({"type": "error", "code": code, "text": text})
            client.close()
            return None

        if not msg or msg.get("type") != "join":
            return reject("join_required", "First message must be a join")
        username = msg.get("username")
        if not isinstance(username, str) or not USERNAME_RE.match(username):
            return reject("bad_username", "Username must be 2-20 letters, digits or _")

        with self.lock:
            if self.stopping.is_set():
                return reject("shutting_down", "Server is shutting down")
            if len(self.clients) >= MAX_CLIENTS:
                return reject("server_full", "Server is full")
            if username.lower() in self.clients:
                return reject("username_taken", f"Username '{username}' is already in use")
            client.username = username
            self.clients[username.lower()] = client
            users = sorted(c.username for c in self.clients.values())

        log.info("%s joined (%d online)", client.label, len(users))
        client.send({"type": "join_ok", "username": username, "users": users, "ts": now_iso()})
        self._broadcast({"type": "system", "event": "join", "username": username,
                         "text": f"{username} joined the chat", "ts": now_iso()},
                        exclude=client)
        return reader

    def _route(self, client: ClientConnection, msg: dict) -> None:
        kind = msg["type"]

        if kind == "ping":
            client.send({"type": "pong", "ts": now_iso()})
            return
        if kind == "users":
            with self.lock:
                users = sorted(c.username for c in self.clients.values())
            client.send({"type": "user_list", "users": users})
            return
        if kind not in ("message", "private"):
            client.send({"type": "error", "code": "unknown_type", "text": f"Unknown type '{kind}'"})
            return

        text = msg.get("text")
        if not isinstance(text, str) or not text.strip():
            client.send({"type": "error", "code": "empty", "text": "Message text is empty"})
            return
        if len(text) > MAX_TEXT_CHARS:
            client.send({"type": "error", "code": "too_long", "text": f"Max {MAX_TEXT_CHARS} characters"})
            return
        if client.rate_limited():
            client.send({"type": "error", "code": "rate_limited", "text": "Slow down"})
            return

        if kind == "message":
            self._broadcast({"type": "message", "from": client.username, "text": text, "ts": now_iso()})
            return

        target_name = msg.get("to")
        with self.lock:
            target = self.clients.get(str(target_name).lower())
        if target is None:
            client.send({"type": "error", "code": "no_such_user", "text": f"No user '{target_name}' online"})
            return
        out = {"type": "private", "from": client.username, "to": target.username, "text": text, "ts": now_iso()}
        target.send(out)
        if target is not client:
            client.send(out)  # echo so the sender sees it was delivered to the server

    def _broadcast(self, message: dict, exclude: Optional[ClientConnection] = None) -> None:
        with self.lock:
            targets = [c for c in self.clients.values() if c is not exclude]
        for c in targets:      # send outside the lock: queues never block
            c.send(message)

    def _remove(self, client: ClientConnection, reason: str) -> None:
        removed = False
        if client.username:
            with self.lock:
                if self.clients.get(client.username.lower()) is client:
                    del self.clients[client.username.lower()]
                    removed = True
        client.close()
        log.info("%s %s", client.label, reason)
        if removed and not self.stopping.is_set():
            text = (f"{client.username} left the chat" if reason == "left"
                    else f"{client.username} disconnected ({reason})")
            self._broadcast({"type": "system", "event": "leave", "username": client.username,
                             "reason": reason, "text": text, "ts": now_iso()})


# -------------------------------------------------------------------- main ---

def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="TCP chat server")
    parser.add_argument("--host", default=os.getenv("CHAT_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("CHAT_PORT", "5000")))
    args = parser.parse_args()

    # Connection/error log: always to the console (journald on the VM),
    # and also to a file if LOG_FILE is set.
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    log_file = os.getenv("LOG_FILE")
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)

    server = ChatServer(args.host, args.port)
    server.bind()

    def stop(signum, _frame):
        log.info("Received signal %s", signal.Signals(signum).name)
        server.shutdown()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)   # systemd sends SIGTERM on stop/restart
    server.serve_forever()


if __name__ == "__main__":
    main()
