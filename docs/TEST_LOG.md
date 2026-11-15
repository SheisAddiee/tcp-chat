# Test log

Full details, screenshots and log excerpts are in the lab report (Sections 4 and 8).

## Automated tests

```powershell
python -m unittest discover -s tests -v 2>&1 | Tee-Object results\test_log_automated.txt
```

25 tests in `tests/test_chat.py`, each starting a real server and connecting over TCP. Saved output: [`results/test_log_automated.txt`](../results/test_log_automated.txt).

| Requirement | Test(s) |
|---|---|
| ≥ 3 simultaneous clients | `test_group_message_reaches_everyone`, `test_many_concurrent_clients` (30 clients) |
| Reconnect while others stay connected | `test_reconnect_while_three_clients_stay_connected`, `test_username_reusable_after_disconnect` |
| Message split across several reads | `test_message_split_across_several_tcp_segments` |
| Several messages in one read | `test_several_messages_in_one_send` |
| Newline inside message text | `test_newline_inside_text_does_not_break_framing` |
| Malformed JSON / wrong shape / bad UTF-8 / unknown type | `test_invalid_json_gets_error_and_connection_survives`, `test_other_malformed_inputs_do_not_crash_server` |
| Oversized input | `test_oversized_frame_disconnects` |
| Username rules | `test_invalid_username_rejected`, `test_duplicate_username_rejected_case_insensitive`, `test_must_join_first` |
| Message validation and rate limit | `test_empty_and_too_long_messages_rejected`, `test_rate_limit` |
| Group and private messages | `test_group_message_reaches_everyone`, `test_private_message_only_reaches_target`, `test_private_to_unknown_user` |
| Leave / abrupt disconnect / idle timeout | `test_graceful_leave_is_announced`, `test_abrupt_disconnect_is_detected`, `test_idle_client_times_out` |
| Server shutdown | `test_server_shutdown_notifies_clients` |

## Tests on the deployed server (102.37.218.84:5000)

| ID | Test | Expected | Result | Evidence (report) |
|---|---|---|---|---|
| TC-01 | Three users connected at once | All joined, told apart by source port | PASS | Section 4, server log |
| TC-02 | Private message and unknown recipient | Only the recipient receives it; error for unknown user | PASS | Section 4, client output |
| TC-03 | Graceful close with `/quit` | FIN/ACK in both directions, user removed | PASS | Figure 4.2 |
| SEC-01 | Unknown host connects but never joins | Rejected after the join timeout | PASS | Section 4, server log |
| EXP-02 | Three messages in one send; one message in 2-byte pieces (`test_framing.py`) | All four messages read correctly | PASS | Section 3.2 |
| TC-04 | Client leaves and reconnects with the same name | Name freed after cleanup; rejoin succeeds | PASS (live); three-client case by automated test | Figure 4.6 |
| TC-05 | Duplicate username | `username_taken` error | PASS (automated test only) | Section 8.1 |
| TC-06 | Server restart (`sudo systemctl restart chat-server`) | Clients get a shutdown notice; server listening again | PASS | Figures 8.1, 8.2 |
| TC-07 | Abrupt client exit (`tcp_bench.py abort`) | Server logs `connection reset` and removes the user | PASS | Figures 4.5, 4.7 |

## Experiments

| ID | Experiment | Capture | Report |
|---|---|---|---|
| — | Handshake and teardown (Experiment 1) | `captures/handshake_teardown.pcapng` | Figures 2.1, 4.2 |
| EXP-03 | TCP vs UDP, baseline / 10% loss / 50 ms delay | – | Table 4.1, Figure 4.1 |
| EXP-04 | Retransmission under 10% loss (Experiment 2) | `captures/exp2_loss.pcap` | Figure 4.3 |
| EXP-05 | Throughput and flow control (Experiment 4) | `captures/exp4_flow.pcap` | Figure 4.4 |
| EXP-06 | Abrupt close with RST (Experiment 3) | `captures/exp3_term.pcap` | Figures 4.5, 4.7 |
