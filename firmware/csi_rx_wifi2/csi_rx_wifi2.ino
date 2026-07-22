/*
 * ============================================================================
 *  CSI Link - RECEIVER (RX), WIRELESS VARIANT -- SECOND PAIR (antenna #2)
 *  Board: Seeed Studio XIAO ESP32-C3   (Arduino core 2.0.17)
 * ============================================================================
 *
 *  Same AP+STA redesign as csi_rx_wifi.ino (2026-07-22) -- see that file for
 *  the full rationale (a whole session of laptop dual-Wi-Fi-adapter instability
 *  led to this: RX joins the home Wi-Fi directly instead of the laptop joining
 *  RX's isolated hotspot). This pair keeps its own distinct SSID/channel/AP
 *  subnet so it can run AT THE SAME TIME as pair 1 without colliding:
 *
 *    Pair 1 (e.g. Taoglas): SSID "csi_link",  channel 11, AP 192.168.4.1, mDNS csi-rx
 *    Pair 2 (e.g. stock):   SSID "csi_link2", channel 6,  AP 192.168.5.1, mDNS csi-rx2  <- this one
 *
 *  The laptop now reaches BOTH boards over its ONE normal Wi-Fi connection
 *  (csi-rx.local / csi-rx2.local or their printed IPs) -- no second adapter
 *  needed at all anymore, which is what made the dual-antenna rig fragile.
 * ============================================================================
 */

#include <WiFi.h>
#include <WiFiUdp.h>
#include <ESPmDNS.h>
#include "esp_wifi.h"
#include <string.h>

// ----------------------- CONFIG (match csi_tx2.ino) -------------------------
static const char*    WIFI_SSID    = "csi_link2";
static const char*    WIFI_PASS    = "csi_password_123";
static const int      WIFI_CHANNEL = 6;        // different from pair 1's channel 11
static const uint16_t UDP_PORT     = 5000;
static const uint16_t TCP_PORT     = 3333;     // laptop connects here
static const IPAddress AP_IP(192, 168, 5, 1);       // different subnet from pair 1
static const IPAddress AP_GATEWAY(192, 168, 5, 1);
static const IPAddress AP_SUBNET(255, 255, 255, 0);

// ---- Home Wi-Fi (STA side) -- so the laptop never has to join csi_link2 ----
static const char*    HOME_SSID    = "DhaOne_2GEXT";   // must be 2.4 GHz
static const char*    HOME_PASS    = "Internetloki@2023";
static const char*    MDNS_NAME    = "csi-rx2";         // reach it at csi-rx2.local

// 1 = auto-lock CSI to the first STA that connects (recommended, clean data).
// 0 = log every received packet (debug only).
#define FILTER_MODE 1

// 2026-07-22 FIX (see csi_rx_wifi.ino for the full story): printing every
// ~97 Hz CSI line to Serial starved the Arduino loop() task badly enough
// that it never accepted a waiting TCP client. Serial CSI-line output is now
// OFF by default; turn on only for USB-tethered debugging.
#define SERIAL_CSI_OUTPUT 0
// ---------------------------------------------------------------------------

WiFiUDP    udp;
WiFiServer tcpServer(TCP_PORT);
WiFiClient tcpClient;                 // one laptop client at a time
uint32_t   csi_idx = 0;
uint8_t    udp_buf[64];
volatile bool have_target = false;
uint8_t    target_mac[6];

// ---- SPSC ring buffer: wifi_csi_cb (producer) only ever writes here; -----
// ---- loop() (consumer) is the only thing that ever touches the socket. ---
// (See csi_rx_wifi.ino for the full story: calling tcpClient.write() directly
// inside wifi_csi_cb stalls the Wi-Fi driver's own task when the write
// blocks, which is what caused throughput to decay over time under AP+STA.)
struct CSILine { int len; char buf[900]; };
static const uint32_t RING_SIZE = 8;
CSILine        ring[RING_SIZE];
volatile uint32_t ring_head = 0;      // next slot the producer will fill
volatile uint32_t ring_tail = 0;      // next slot the consumer will send

static inline bool mac_match(const uint8_t* a, const uint8_t* b) {
  return memcmp(a, b, 6) == 0;
}

void onWiFiEvent(WiFiEvent_t event, WiFiEventInfo_t info) {
  if (event == ARDUINO_EVENT_WIFI_AP_STACONNECTED) {
    memcpy(target_mac, info.wifi_ap_staconnected.mac, 6);
    have_target = true;
    Serial.printf("TX connected to our AP, locking CSI to %02x:%02x:%02x:%02x:%02x:%02x\n",
                  target_mac[0], target_mac[1], target_mac[2],
                  target_mac[3], target_mac[4], target_mac[5]);
  } else if (event == ARDUINO_EVENT_WIFI_AP_STADISCONNECTED) {
    Serial.println("TX disconnected from our AP (keeping CSI lock).");
  } else if (event == ARDUINO_EVENT_WIFI_STA_GOT_IP) {
    Serial.printf("Joined home Wi-Fi '%s' -- reachable at %s or %s.local\n",
                  HOME_SSID, WiFi.localIP().toString().c_str(), MDNS_NAME);
  } else if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
    Serial.println("Lost home Wi-Fi connection -- retrying...");
  }
}

void wifi_csi_cb(void* ctx, wifi_csi_info_t* info) {
  if (!info || !info->buf || info->len == 0) return;
#if FILTER_MODE
  if (!have_target) return;
  if (!mac_match(info->mac, target_mac)) return;
#endif

  const wifi_pkt_rx_ctrl_t* rx = &info->rx_ctrl;
  bool have_client = tcpClient && tcpClient.connected();
#if SERIAL_CSI_OUTPUT
  const bool want_serial = true;
#else
  const bool want_serial = false;
  if (!have_client) { csi_idx++; return; }   // nowhere to send it -- skip the work
#endif

  // Build the whole line directly into the next ring slot (producer side of
  // the SPSC ring -- see loop() for the consumer). No network I/O here, so
  // this callback can never block the Wi-Fi stack.
  CSILine& slot = ring[ring_head % RING_SIZE];
  int p = snprintf(slot.buf, sizeof(slot.buf),
                   "CSI_DATA,%lu,%02x:%02x:%02x:%02x:%02x:%02x,%d,%d,%d,%d,%d,%d,%d,%d,%d,\"[",
                   (unsigned long)csi_idx,
                   info->mac[0], info->mac[1], info->mac[2],
                   info->mac[3], info->mac[4], info->mac[5],
                   rx->rssi, rx->rate, rx->sig_mode, rx->mcs, rx->cwb,
                   rx->noise_floor, rx->channel, rx->timestamp, info->len);
  csi_idx++;

  const int8_t* d = info->buf;
  const int len = info->len;
  for (int i = 0; i < len && p < (int)sizeof(slot.buf) - 8; i++) {
    p += snprintf(slot.buf + p, sizeof(slot.buf) - p, i < len - 1 ? "%d," : "%d", d[i]);
  }
  if (p < (int)sizeof(slot.buf) - 4) {
    slot.buf[p++] = ']'; slot.buf[p++] = '"'; slot.buf[p++] = '\n'; slot.buf[p] = '\0';
  }
  slot.len = p;

  if (want_serial) Serial.write((uint8_t*)slot.buf, p);

  if (have_client) {
    uint32_t next_head = ring_head + 1;
    if (next_head - ring_tail > RING_SIZE) {
      ring_tail = next_head - RING_SIZE;   // ring full -- drop the oldest unsent line
    }
    ring_head = next_head;                // publish this slot to the consumer
  }
}

void setup() {
  Serial.begin(921600);
  delay(200);
  Serial.println("\n== CSI Link RX #2 (wireless) ==");

  WiFi.onEvent(onWiFiEvent);
  WiFi.mode(WIFI_AP_STA);               // AP for TX + STA for the home network
  WiFi.setSleep(false);
  WiFi.softAPConfig(AP_IP, AP_GATEWAY, AP_SUBNET);

  bool ok = WiFi.softAP(WIFI_SSID, WIFI_PASS, WIFI_CHANNEL, 0, 4);
  Serial.printf("SoftAP '%s' ch %d: %s, IP %s\n",
                WIFI_SSID, WIFI_CHANNEL, ok ? "OK" : "FAILED",
                WiFi.softAPIP().toString().c_str());

  uint8_t mac[6];
  esp_wifi_get_mac(WIFI_IF_AP, mac);
  Serial.printf("RX(AP) MAC: %02x:%02x:%02x:%02x:%02x:%02x\n",
                mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);

  WiFi.begin(HOME_SSID, HOME_PASS);
  Serial.printf("Joining home Wi-Fi '%s' (STA) ...\n", HOME_SSID);
  if (MDNS.begin(MDNS_NAME)) {
    MDNS.addService("csi", "tcp", TCP_PORT);
    Serial.printf("mDNS responder started: %s.local\n", MDNS_NAME);
  } else {
    Serial.println("ERR mDNS.begin failed -- connect via the STA IP printed "
                   "above instead of the .local name.");
  }

  udp.begin(UDP_PORT);
  tcpServer.begin();
  tcpServer.setNoDelay(true);
  Serial.printf("TCP CSI stream on port %d -- connect your laptop here once "
                "it joins home Wi-Fi (see IP/hostname above).\n", TCP_PORT);

  // ---- Enable CSI: LLTF only -> consistent 64 subcarriers (128 values) ----
  wifi_csi_config_t csi_config;
  memset(&csi_config, 0, sizeof(csi_config));
  csi_config.lltf_en           = true;
  csi_config.htltf_en          = false;
  csi_config.stbc_htltf2_en    = false;
  csi_config.ltf_merge_en      = true;
  csi_config.channel_filter_en = true;
  csi_config.manu_scale        = false;

  esp_err_t r;
  r = esp_wifi_set_csi_config(&csi_config);
  if (r != ESP_OK) Serial.printf("ERR esp_wifi_set_csi_config: %d\n", r);
  r = esp_wifi_set_csi_rx_cb(&wifi_csi_cb, NULL);
  if (r != ESP_OK) Serial.printf("ERR esp_wifi_set_csi_rx_cb: %d\n", r);
  r = esp_wifi_set_csi(true);
  if (r != ESP_OK) Serial.printf("ERR esp_wifi_set_csi: %d\n", r);

  Serial.println("CSI enabled. Waiting for TX + laptop to connect...");
}

void loop() {
  int n = udp.parsePacket();
  if (n > 0) udp.read(udp_buf, sizeof(udp_buf));

  if (tcpServer.hasClient()) {
    WiFiClient incoming = tcpServer.available();
    if (tcpClient) tcpClient.stop();
    tcpClient = incoming;
    tcpClient.setTimeout(20);   // bound how long a write() can block, ms
    ring_tail = ring_head;      // don't replay the old backlog to a fresh client
    Serial.println("Laptop TCP client connected.");
  }

  // Consumer side of the ring buffer: send whatever wifi_csi_cb queued up.
  while (ring_tail != ring_head) {
    if (!tcpClient || !tcpClient.connected()) { ring_tail = ring_head; break; }
    CSILine& slot = ring[ring_tail % RING_SIZE];
    tcpClient.write((uint8_t*)slot.buf, slot.len);
    ring_tail++;
  }
}
