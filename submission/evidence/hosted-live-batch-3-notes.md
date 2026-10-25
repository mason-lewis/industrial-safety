# Live sample video testing, batch 3 — 24 October 2026

## Outcome: NOT PASSED; batch stopped on an ambiguous charge during video 14

Six of nine videos were attempted in numeric order on the same Wi-Fi network as batch 2, with the operator's explicit acceptance that an ambiguous charge might stop the batch. Videos 18, 19 and warehouse-forklifts were not reached. Coverage was complete for three videos (06, 09, 11). No video passed accuracy acceptance, and none has independent human labels.

The stop: generation reservation 748a7e4c-e63f-4851-b0f4-bb21234a5571 (video 14 observer, client request 93183349-962d-4037-9124-8e27b5d59853, 2026-10-24 23:57:39 UTC). The request body completed locally and the connection broke while awaiting response headers with ReadError / BrokenResourceError / BrokenPipeError and no TLS alert. That cannot be proven unsent, so $0.009028 stays reserved as unknown. No generation was resent. All jobs are terminal and the batch is closed.

## Budget

New calculated usage plus the new unknown reservation: $0.418211 batch committed. Combined with the $0.167405 prior commitments (which include the two earlier unknown reservations), the total is $0.585616 committed under the $1 cap, leaving $0.414384 unspent. These are local accounting figures, not provider invoice amounts.

## Connection handling during the batch

362 paid generation requests were made. Three failed with a provider-sent bad_record_mac alert, were proven unsent, settled at $0 and were resent once; under the previous policy each would have stopped the batch. One ambiguous broken-pipe failure stopped it. During video 05, an ~80-second TCP connection outage made four windows' free token counts fail after three attempts. No generation was dispatched for them and no charge was created. The per-video free network gate tolerated this network's sporadic ~4% upload failures but would stop on an outage.

## Per video

- 05 warehouse pallet handling: 4/10 intervals. Four windows were lost to the connection outage (no charge) and two to observer output rejection (a required category check was omitted). No live backpressure skips. 0 cases. $0.025004.
- 06 truck pallet unloading: 20/20 intervals, complete coverage. 37 cases. 7 specialist reviews completed; 30 were blocked by the $0.15 per-video cap and remain unassessed. $0.143672.
- 09 construction balcony: 2/2 intervals, all three specialist reviews completed with no errors (first error-free completed hosted balcony run). A suspected struck-by case cites 5.2–5.6 s, but describes a board rising near a bending worker and does not confirm contact. Falling-material/head-contact recognition remains failed. $0.028876.
- 11 restaurant kitchen fire: 8/8 intervals, complete coverage, 6 specialist reviews completed. The observer noticed flames flaring beside the worker at 39.0 s and 44.4 s but could only file them under struck_by: the screening checklist has no fire/burn category. $0.083159.
- 13 warehouse rack collapse: 6/7 intervals (one observer output rejection). Rack collapse and spilled goods recognized as suspected unstable stacking; no person in the fall zone identified. 9 specialist reviews completed. $0.095473.
- 14 store shelf collapse: 7/11 intervals before the stop. Falling merchandise and display failure recognized as suspected struck-by/unstable stacking. $0.042027 including the unknown reservation.

Recognition statements are assistant spot checks of saved outputs against the footage contact sheets, not human expert labels.

## Next engineer

P0: Run paid tests from a reliable network (wired, or a different network that passes `scripts/review_transport_probe.py --count 100` with zero failures). The unprovable broken-pipe variant still stops batches on this Wi-Fi.
P1: Observer output rejections (missing category checks) lost three windows. Consider a deterministic fill of omitted categories as unknown rather than rejecting the packet.
P1: Add a fire/burn screening category; fire is currently forced into struck_by.
P1: The $0.15 per-video cap leaves most specialist reviews unassessed on busy videos (06: 30 of 37).
P2: Independent human labels for an accuracy study; the balcony falling-material recognition is still failed.
