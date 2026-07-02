# diagnostics/

Standalone serial/link debug tools from bringing the ESP32-C3 rig up. Not part
of the analysis pipeline; run individually when the capture link misbehaves.

- `read_serial.py` — read one or more COM ports and print CSI throughput.
- `test_poll.py` — mirror the GUI main-thread poll read (in_waiting) to check rate.
- `test_ab.py` — A/B whether per-line parse+write stalls the reader.
- `test_diag.py` — count raw CSI_DATA lines vs. successfully parsed lines.

Run from the workspace root, e.g. `python diagnostics/test_poll.py COM17 30`.
(`test_ab.py`/`test_diag.py` add the repo root to sys.path so `log_csi` imports.)
