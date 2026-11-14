# Test log

The brief requires a test log covering: simultaneous users, reconnects, malformed input, split/combined messages and cloud access.

## Part A: Automated tests (local)

Re-run and save the output before submitting:

```bash
python -m unittest discover -s tests -v 2>&1 | tee results/test_log_automated.txt
```

| # | Requirement | Test(s) | Result |
|---|---|---|---|
| A1 | ≥ 3 simultaneous clients | `test_group_message_reaches_everyone`, `test_many_concurrent_clients` (30 clients) | Pass |
| A2 | Join/reconnect while others stay connected | `test_reconnect_while_three_clients_stay_connected`, `test_username_reusable_after_disconnect` | Pass |
| A3 | Message split across several TCP reads | `test_message_split_across_several_tcp_segments` | Pass |
| A4 | Several messages combined in one read | `test_several_messages_in_one_send` | Pass |
| A5 | Newline inside message text | `test_newline_inside_text_does_not_break_framing` | Pass |
| A6 | Malformed JSON / wrong shape / bad UTF-8 / unknown type | `test_invalid_json_gets_error_and_connection_survives`, `test_other_malformed_inputs_do_not_crash_server` | Pass |
| A7 | Oversized input | `test_oversized_frame_disconnects` | Pass |
| A8 | Username validation and uniqueness | `test_invalid_username_rejected`, `test_duplicate_username_rejected_case_insensitive`, `test_must_join_first` | Pass |
| A9 | Message validation and rate limit | `test_empty_and_too_long_messages_rejected`, `test_rate_limit` | Pass |
| A10 | Group and private messaging | `test_group_message_reaches_everyone`, `test_private_message_only_reaches_target`, `test_private_to_unknown_user` | Pass |
| A11 | Graceful leave / abrupt disconnect / idle timeout | `test_graceful_leave_is_announced`, `test_abrupt_disconnect_is_detected`, `test_idle_client_times_out` | Pass |
| A12 | Server shutdown notifies clients | `test_server_shutdown_notifies_clients` | Pass |

25 tests in total; all passed on 10 Nov 2026 (Python 3.13, Linux).

## Part B: Manual tests on the deployed server

Fill these in after deploying. Attach a screenshot for each row (name the files `evidence/B1.png`, etc.).

Server: `<PUBLIC_IP>:5000`   Date: ________   Tester(s): ________

| # | Test | Steps | Expected | Actual | Pass? |
|---|---|---|---|---|---|
| B1 | Cloud access | `python client.py --host <PUBLIC_IP> --port 5000 -u alice` from laptop 1 | "Joined as alice" | | |
| B2 | Second user, different network | Same from laptop 2 / phone hotspot as `bob` | alice sees "bob joined" | | |
| B3 | Two-way messaging | alice → group, bob replies | Both see both messages, same order | | |
| B4 | Third simultaneous user | `carol` joins from a third machine/terminal | All three see each other's messages | | |
| B5 | Private message | alice: `/msg bob hi` | Only bob (and alice's echo) shows it; carol doesn't | | |
| B6 | Reconnect | bob closes terminal, then reconnects as `bob` | Others see "bob disconnected", then "bob joined"; chat continues | | |
| B7 | Duplicate username | Second client tries `alice` while alice is online | "Username 'alice' is already in use" | | |
| B8 + B9 | Split message and malformed input over the internet | `python experiments\tcp_bench.py split --host <PUBLIC_IP>` | `join_ok` arrives only after the second half; `hello` gets `bad_message`; server keeps running | | |
| B10 | Abrupt termination | `python experiments/tcp_bench.py abort --host <PUBLIC_IP>` | Server log: "crasher ... connection reset" | | |
| B11 | Server restart | `sudo systemctl restart chat-server` | Clients get "Server is shutting down" then "Disconnected"; reconnect works | | |
| B12 | Server log | `sudo journalctl -u chat-server --since today` (or `LOG_FILE`) | Shows connects, joins, rejects, malformed input, disconnect reasons with IP:port | | |

Windows users: run the B-tests from PowerShell with `python`. Run the B11/B12 commands inside the SSH session on the VM.

On Linux/macOS, B8/B9 can also be done by hand:
```bash
( printf '{"type":"jo'; sleep 2; printf 'in","username":"nc1"}\n'; sleep 2 ) | nc <PUBLIC_IP> 5000
```
