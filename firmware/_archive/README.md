# firmware/_archive/

Firmware experiments that were tried and **abandoned**. Kept for the record; do
not flash for data collection.

- `csi_tx_broadcast/` + `csi_rx_sniffer/` — 1 TX broadcast + promiscuous RX
  sniffer approach. On the ESP32-C3 the SoftAP broadcast/beacon is DSSS (no OFDM
  LTF), so the passive sniffer received no usable CSI. Abandoned.
- `csi_rx_sta/`, `csi_tx_ap/` — alternate STA/AP role assignments explored while
  chasing the link-flapping bug.

The working rig is `firmware/csi_rx` (SoftAP receiver) + `firmware/csi_tx` (STA
metronome), flashed with esp32 Arduino core **2.0.17**.
