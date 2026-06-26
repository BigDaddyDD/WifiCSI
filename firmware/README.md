# CSI Link Firmware (XIAO ESP32-C3)

Two sketches that turn a pair of Seeed XIAO ESP32-C3 boards into a controlled
CSI capture rig:

- `csi_tx/csi_tx.ino` - **transmitter** (metronome). Sends UDP at a fixed rate.
- `csi_rx/csi_rx.ino` - **receiver**. Computes CSI and streams it over USB serial.

The RX **auto-locks** its CSI capture to the first station that connects (the
TX), so you don't manage MAC addresses by hand.

> The RX is the board you plug into the logging PC. Capture its serial with
> `../log_csi.py` (baud 921600).

---

## 1. One-time Arduino IDE setup

1. **Boards Manager URL** (File > Preferences):
   `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`
2. **Install** "esp32 by Espressif Systems" (v3.x) via Boards Manager.
3. **Select board**: Tools > Board > esp32 > **XIAO_ESP32C3**.
4. **Tools > USB CDC On Boot: Enabled**  <-- required so `Serial` shows over USB.

## 2. Flash

1. Open `csi_rx/csi_rx.ino`, pick the RX COM port, **Upload**. (Boot it first.)
2. Open `csi_tx/csi_tx.ino`, pick the TX COM port, **Upload**.

The `CONFIG` blocks must match (SSID/password). They already do by default.

## 3. What good output looks like

**RX serial:**
```
== CSI Link RX ==
SoftAP 'csi_link' ch 6: OK, IP 192.168.4.1
RX(AP) MAC: d4:f9:8d:05:93:c5
CSI enabled. Waiting for TX to connect...
STA connected, locking CSI to <TX MAC>          <-- TX has joined
CSI_DATA,0,<TX MAC>,-41,11,1,0,0,-96,6,1234567,128,"[12,-3,...]"
CSI_DATA,1,...
```
No `CSI_DATA` lines appear until the TX connects - that's expected.

**TX serial (healthy):**
```
== CSI Link TX ==
Connecting to csi_link
[evt] associated to AP
[evt] got IP 192.168.4.2
Connected. TX MAC <...>, sending ~100 Hz
```

**TX serial (failing):** after 10 s it prints the disconnect `reason=NN` and a
scan of visible networks. Send me that output - it pinpoints the cause:
- `reason=15` -> wrong password (SSID/PASS mismatch between sketches)
- `reason=201` / SSID not in scan -> AP not visible (RX not booted, channel)
- `reason=205`/`2` -> association rejected; we'll dig in

## 4. Output schema

```
CSI_DATA,idx,mac,rssi,rate,sig_mode,mcs,bw,noise_floor,channel,local_us,len,"[I0,Q0,I1,Q1,...]"
```

| field | meaning |
|---|---|
| idx | RX packet counter (gaps = drops) |
| mac | sender MAC (the locked TX) |
| rssi | dBm |
| rate | PHY rate code (enum, not Mbps) |
| sig_mode | 0=legacy 1=HT(11n) |
| mcs | modulation/coding index |
| bw | 0=20 MHz, 1=40 MHz |
| noise_floor | dBm |
| channel | primary channel |
| local_us | ESP timestamp (microseconds) |
| len | # int8 values (expect 128) |
| [...] | interleaved I,Q; amplitude = sqrt(I^2+Q^2) |

Drop subcarrier 0 and the band-edge nulls in post-processing.

## 5. Phase 0 acceptance test (before any dataset)

```
python ../log_csi.py --port COM17 --duration 60 --label empty --room confA --distance 3.0
python ../analyze_csi.py data/raw/empty_<timestamp>.csv
```
**Pass** = ~100 Hz mean rate, low interval std, <5% drops, `len` constant at 128.
If drops are high, set `SEND_INTERVAL_US = 20000` (50 Hz) in `csi_tx.ino`,
re-flash, and re-run with `--fs 50`.

## 6. Troubleshooting

| symptom | fix |
|---|---|
| TX stuck "Connecting" | Read the `reason=NN` + scan output it prints after 10 s (see section 3). |
| No `CSI_DATA` after TX connects | Set `#define FILTER_MODE 0` in RX, re-flash. If lines appear, the auto-lock event didn't fire - tell me. |
| Nothing on serial at all | Tools > USB CDC On Boot > Enabled; confirm baud 921600. |
| Boot-loop / `ERR esp_wifi_set_csi*` | Send me the error number + your esp32 core version (different `wifi_csi_config_t`). |
| Many `idx` gaps / drops | Lower to 50 Hz. The C3 is single-core, so serial printing in the CSI callback is the bottleneck; if 50 Hz still drops, tell me and I'll buffer it and print from the main loop. |
