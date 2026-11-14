"""
Terminal chat client.

Usage:
    python client.py --host <server-ip> --port 5000 --username nas

Commands inside the chat:
    <text>              send to everyone
    /msg <user> <text>  private message
    /users              list who is online
    /help               show commands
    /quit               leave the chat
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
from datetime import datetime

from protocol import FrameReader, ProtocolError, encode

PING_INTERVAL = 20  # seconds; keeps the server's idle timer from expiring


class ChatClient:
    def __init__(self, host: str, port: int, username: str):
        self.host, self.port, self.username = host, port, username
        self.sock: socket.socket | None = None
        self.send_lock = threading.Lock()   # input thread and ping thread both send
        self.stopped = threading.Event()

    # ---- connection ----

    def connect(self) -> bool:
        try:
            # create_connection() does the TCP three-way handshake (SYN, SYN-ACK, ACK)
            self.sock = socket.create_connection((self.host, self.port), timeout=10)
        except OSError as exc:
            print(f"Could not connect to {self.host}:{self.port}: {exc}")
            return False
        self.sock.settimeout(None)
        local = self.sock.getsockname()
        print(f"Connected: local {local[0]}:{local[1]} -> server {self.host}:{self.port}")

        self.reader = FrameReader(self.sock)
        self.send({"type": "join", "username": self.username})
        try:
            reply = self.reader.read()
        except (OSError, ProtocolError):
            reply = None
        if not reply:
            print("Server closed the connection during join.")
            return False
        if reply["type"] == "error":
            print(f"Join refused: {reply.get('text')}")
            return False
        print(f"Joined as {reply['username']}. Online: {', '.join(reply['users'])}")
        print("Type /help for commands.\n")
        return True

    def send(self, message: dict) -> bool:
        try:
            with self.send_lock:
                self.sock.sendall(encode(message))
            return True
        except (OSError, ProtocolError) as exc:
            if not self.stopped.is_set():
                print(f"! send failed: {exc}")
            return False

    # ---- background threads ----

    def receive_loop(self) -> None:
        while not self.stopped.is_set():
            try:
                msg = self.reader.read()
            except ProtocolError:
                continue
            except OSError:
                msg = None
            if msg is None:
                if not self.stopped.is_set():
                    print("\n! Disconnected from server.")
                    self.stopped.set()
                    os._exit(1)   # input() is blocking the main thread; exit the process
                return
            self.show(msg)

    def ping_loop(self) -> None:
        while not self.stopped.wait(PING_INTERVAL):
            self.send({"type": "ping"})

    # ---- display ----

    @staticmethod
    def _time(msg: dict) -> str:
        try:
            return datetime.fromisoformat(msg["ts"]).astimezone().strftime("%H:%M")
        except (KeyError, ValueError):
            return datetime.now().strftime("%H:%M")

    def show(self, msg: dict) -> None:
        t = self._time(msg)
        kind = msg["type"]
        if kind == "message":
            who = "you" if msg["from"] == self.username else msg["from"]
            print(f"[{t}] {who}: {msg['text']}")
        elif kind == "private":
            if msg["from"] == self.username:
                print(f"[{t}] (private to {msg['to']}) you: {msg['text']}")
            else:
                print(f"[{t}] (private) {msg['from']}: {msg['text']}")
        elif kind == "system":
            print(f"[{t}] * {msg.get('text')}")
        elif kind == "user_list":
            print(f"Online ({len(msg['users'])}): {', '.join(msg['users'])}")
        elif kind == "error":
            print(f"! {msg.get('text')}")
        # pong: nothing to show

    # ---- main loop ----

    def run(self) -> None:
        if not self.connect():
            sys.exit(1)
        threading.Thread(target=self.receive_loop, daemon=True).start()
        threading.Thread(target=self.ping_loop, daemon=True).start()
        try:
            while not self.stopped.is_set():
                line = input().strip()
                if line:
                    self.handle_input(line)
        except (EOFError, KeyboardInterrupt):
            pass
        self.quit()

    def handle_input(self, line: str) -> None:
        if not line.startswith("/"):
            self.send({"type": "message", "text": line})
            return
        cmd, _, rest = line.partition(" ")
        if cmd == "/msg":
            to, _, text = rest.partition(" ")
            if not to or not text:
                print("Usage: /msg <user> <text>")
            else:
                self.send({"type": "private", "to": to, "text": text})
        elif cmd == "/users":
            self.send({"type": "users"})
        elif cmd == "/quit":
            self.stopped.set()
        elif cmd == "/help":
            print(__doc__.split("Commands inside the chat:")[1])
        else:
            print("Unknown command. Type /help")

    def quit(self) -> None:
        self.stopped.set()
        self.send({"type": "leave"})
        try:
            self.sock.shutdown(socket.SHUT_RDWR)  # sends FIN: orderly close
        except OSError:
            pass
        self.sock.close()
        print("Bye.")


def main() -> None:
    parser = argparse.ArgumentParser(description="TCP chat client")
    parser.add_argument("--host", default=os.getenv("CHAT_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("CHAT_PORT", "5000")))
    parser.add_argument("--username", "-u")
    args = parser.parse_args()
    username = args.username or input("Username: ").strip()
    ChatClient(args.host, args.port, username).run()


if __name__ == "__main__":
    main()
