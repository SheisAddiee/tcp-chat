"""
Automated tests. Each test starts a real server on a free port and talks to it
over real TCP sockets.

Run from the project folder:  python -m unittest -v
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server as server_module                     # noqa: E402
from protocol import FrameReader, encode           # noqa: E402
from server import ChatServer                      # noqa: E402


class RawClient:
    """Minimal test client that speaks the protocol over a raw TCP socket."""

    def __init__(self, port: int):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=3)
        self.reader = FrameReader(self.sock)

    def send(self, msg: dict) -> None:
        self.sock.sendall(encode(msg))

    def recv(self) -> dict | None:
        return self.reader.read()

    def recv_type(self, kind: str) -> dict:
        """Read until a message of this type arrives (skips join/leave notices)."""
        while True:
            msg = self.recv()
            if msg is None:
                raise AssertionError(f"connection closed while waiting for {kind}")
            if msg["type"] == kind:
                return msg

    def join(self, name: str) -> dict:
        self.send({"type": "join", "username": name})
        return self.recv()

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class ChatServerTests(unittest.TestCase):
    def setUp(self):
        self.server = ChatServer("127.0.0.1", 0)
        self.port = self.server.bind()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.clients: list[RawClient] = []

    def tearDown(self):
        for c in self.clients:
            c.close()
        self.server.shutdown()
        self.thread.join(timeout=3)

    def client(self, name: str | None = None) -> RawClient:
        c = RawClient(self.port)
        self.clients.append(c)
        if name:
            reply = c.join(name)
            self.assertEqual(reply["type"], "join_ok", reply)
        return c

    # ---- identification ----

    def test_join_returns_online_users(self):
        self.client("alice")
        bob = RawClient(self.port)
        self.clients.append(bob)
        reply = bob.join("bob")
        self.assertEqual(reply["type"], "join_ok")
        self.assertEqual(reply["users"], ["alice", "bob"])

    def test_duplicate_username_rejected_case_insensitive(self):
        self.client("alice")
        dup = RawClient(self.port)
        self.clients.append(dup)
        reply = dup.join("ALICE")
        self.assertEqual(reply["code"], "username_taken")
        self.assertIsNone(dup.recv(), "server should close the rejected connection")

    def test_invalid_username_rejected(self):
        c = RawClient(self.port)
        self.clients.append(c)
        self.assertEqual(c.join("a b!")["code"], "bad_username")

    def test_must_join_first(self):
        c = RawClient(self.port)
        self.clients.append(c)
        c.send({"type": "message", "text": "hi"})
        self.assertEqual(c.recv()["code"], "join_required")

    # ---- messaging ----

    def test_join_is_announced_to_others(self):
        alice = self.client("alice")
        self.client("bob")
        note = alice.recv_type("system")
        self.assertEqual((note["event"], note["username"]), ("join", "bob"))

    def test_group_message_reaches_everyone(self):
        alice, bob, carol = self.client("alice"), self.client("bob"), self.client("carol")
        alice.send({"type": "message", "text": "hello all"})
        for c in (alice, bob, carol):
            msg = c.recv_type("message")
            self.assertEqual((msg["from"], msg["text"]), ("alice", "hello all"))

    def test_private_message_only_reaches_target(self):
        alice, bob, carol = self.client("alice"), self.client("bob"), self.client("carol")
        alice.send({"type": "private", "to": "bob", "text": "secret"})
        self.assertEqual(bob.recv_type("private")["text"], "secret")
        self.assertEqual(alice.recv_type("private")["to"], "bob")   # sender's echo
        # carol must not get it: everything carol receives before the public message
        alice.send({"type": "message", "text": "public"})
        seen = []
        while True:
            msg = carol.recv()
            seen.append(msg["type"])
            if msg["type"] == "message":
                break
        self.assertNotIn("private", seen)

    def test_private_to_unknown_user(self):
        alice = self.client("alice")
        alice.send({"type": "private", "to": "ghost", "text": "hi"})
        self.assertEqual(alice.recv_type("error")["code"], "no_such_user")

    def test_users_command(self):
        alice = self.client("alice")
        self.client("bob")
        alice.send({"type": "users"})
        self.assertEqual(alice.recv_type("user_list")["users"], ["alice", "bob"])

    def test_ping_pong(self):
        alice = self.client("alice")
        alice.send({"type": "ping"})
        self.assertEqual(alice.recv_type("pong")["type"], "pong")

    # ---- TCP byte-stream framing ----

    def test_message_split_across_several_tcp_segments(self):
        """One message delivered in pieces must be reassembled."""
        alice, bob = self.client("alice"), self.client("bob")
        frame = encode({"type": "message", "text": "split message"})
        for i in range(0, len(frame), 5):
            alice.sock.sendall(frame[i:i + 5])
            time.sleep(0.01)
        self.assertEqual(bob.recv_type("message")["text"], "split message")

    def test_several_messages_in_one_send(self):
        """Several messages arriving in one recv() must be separated."""
        alice, bob = self.client("alice"), self.client("bob")
        alice.sock.sendall(b"".join(encode({"type": "message", "text": f"m{i}"}) for i in range(3)))
        self.assertEqual([bob.recv_type("message")["text"] for _ in range(3)], ["m0", "m1", "m2"])

    def test_newline_inside_text_does_not_break_framing(self):
        alice, bob = self.client("alice"), self.client("bob")
        alice.send({"type": "message", "text": "line1\nline2"})
        self.assertEqual(bob.recv_type("message")["text"], "line1\nline2")

    def test_invalid_json_gets_error_and_connection_survives(self):
        alice = self.client("alice")
        alice.sock.sendall(b"{not json}\n")
        self.assertEqual(alice.recv_type("error")["code"], "bad_message")
        alice.send({"type": "ping"})
        self.assertEqual(alice.recv_type("pong")["type"], "pong")

    def test_other_malformed_inputs_do_not_crash_server(self):
        alice = self.client("alice")
        bad_frames = [
            b"[1, 2, 3]\n",                      # valid JSON but not an object
            b'{"text": "no type field"}\n',       # missing type
            b'{"type": 42}\n',                    # type is not a string
            b"\xff\xfe\xfd\n",                    # invalid UTF-8
        ]
        for frame in bad_frames:
            alice.sock.sendall(frame)
            self.assertEqual(alice.recv_type("error")["code"], "bad_message", frame)
        alice.send({"type": "private", "to": "alice"})          # missing text
        self.assertEqual(alice.recv_type("error")["code"], "empty")
        alice.send({"type": "message", "text": ["not", "a", "string"]})
        self.assertEqual(alice.recv_type("error")["code"], "empty")
        alice.send({"type": "dance"})                           # unknown type
        self.assertEqual(alice.recv_type("error")["code"], "unknown_type")
        # server is still healthy and serving new users
        self.client("bob")

    def test_oversized_frame_disconnects(self):
        alice = self.client("alice")
        alice.sock.sendall(b"x" * 40_000)   # no newline, larger than MAX_FRAME_BYTES
        self.assertEqual(alice.recv_type("error")["code"], "too_large")

    # ---- validation ----

    def test_empty_and_too_long_messages_rejected(self):
        alice = self.client("alice")
        alice.send({"type": "message", "text": "   "})
        self.assertEqual(alice.recv_type("error")["code"], "empty")
        alice.send({"type": "message", "text": "x" * 2001})
        self.assertEqual(alice.recv_type("error")["code"], "too_long")

    def test_rate_limit(self):
        alice = self.client("alice")
        for i in range(25):
            alice.send({"type": "message", "text": str(i)})
        self.assertEqual(alice.recv_type("error")["code"], "rate_limited")

    # ---- disconnection ----

    def test_graceful_leave_is_announced(self):
        alice, bob = self.client("alice"), self.client("bob")
        bob.send({"type": "leave"})
        note = alice.recv_type("system")
        while note["event"] != "leave":
            note = alice.recv_type("system")
        self.assertEqual((note["username"], note["reason"]), ("bob", "left"))

    def test_abrupt_disconnect_is_detected(self):
        """Client closes the socket without sending 'leave' (like a crash)."""
        alice, bob = self.client("alice"), self.client("bob")
        bob.sock.close()
        note = alice.recv_type("system")
        while note["event"] != "leave":
            note = alice.recv_type("system")
        self.assertEqual(note["username"], "bob")
        alice.send({"type": "users"})
        self.assertEqual(alice.recv_type("user_list")["users"], ["alice"])

    def test_username_reusable_after_disconnect(self):
        bob = self.client("bob")
        bob.send({"type": "leave"})
        time.sleep(0.2)
        self.client("bob")   # asserts join_ok

    def test_reconnect_while_three_clients_stay_connected(self):
        """Brief requirement: >= 3 simultaneous clients, one reconnects while others stay."""
        alice, bob, carol = self.client("alice"), self.client("bob"), self.client("carol")
        bob.sock.close()                                   # bob drops abruptly
        for c in (alice, carol):
            note = c.recv_type("system")
            while not (note["event"] == "leave" and note["username"] == "bob"):
                note = c.recv_type("system")

        bob2 = self.client("bob")                          # bob reconnects (new socket, new port)
        for c in (alice, carol):
            note = c.recv_type("system")
            self.assertEqual((note["event"], note["username"]), ("join", "bob"))

        bob2.send({"type": "message", "text": "I'm back"})
        for c in (alice, carol, bob2):
            self.assertEqual(c.recv_type("message")["text"], "I'm back")
        alice.send({"type": "message", "text": "welcome back"})
        for c in (alice, carol, bob2):
            self.assertEqual(c.recv_type("message")["text"], "welcome back")

    def test_idle_client_times_out(self):
        old = server_module.IDLE_TIMEOUT
        server_module.IDLE_TIMEOUT = 0.5
        done = threading.Event()
        try:
            alice, bob = self.client("alice"), self.client("bob")
            alice.sock.settimeout(5)

            def keep_alice_alive():
                while not done.wait(0.1):
                    alice.send({"type": "ping"})
            threading.Thread(target=keep_alice_alive, daemon=True).start()

            note = alice.recv_type("system")       # bob joined
            while note["event"] != "leave":
                note = alice.recv_type("system")   # bob says nothing -> times out
            self.assertEqual(note["username"], "bob")
            self.assertIn("timed out", note["reason"])
        finally:
            done.set()
            server_module.IDLE_TIMEOUT = old

    def test_server_shutdown_notifies_clients(self):
        alice = self.client("alice")
        self.server.shutdown()
        note = alice.recv_type("system")
        self.assertEqual(note["event"], "shutdown")
        self.assertIsNone(alice.recv())

    # ---- concurrency ----

    def test_many_concurrent_clients(self):
        clients = [self.client(f"user{i}") for i in range(30)]
        clients[0].send({"type": "message", "text": "hi everyone"})
        for c in clients:
            self.assertEqual(c.recv_type("message")["text"], "hi everyone")


if __name__ == "__main__":
    unittest.main()
