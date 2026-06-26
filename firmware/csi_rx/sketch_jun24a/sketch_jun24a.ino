/*
 * ============================================================================
 *  CSI Link - RECEIVER (RX)   <-- LOG THIS BOARD'S USB SERIAL
 *  Board: Seeed Studio XIAO ESP32-C3   (Arduino core 3.x / ESP-IDF 5.x)
 * ============================================================================
 *
 *  ROLE
 *    SoftAP on a FIXED 2.4 GHz channel. Enables CSI (LLTF only -> consistent
 *    64-subcarrier / 128-value vectors) and prints ONE line per received
 *    packet over USB serial.
 *
 *  CLEAN DATA
 *    When a station (the TX) connects, the RX auto-locks CSI capture to that
 *    station's MAC, so the dataset isn't polluted by other Wi-Fi traffic.
 *    Set FILTER_MODE 0 to log everything while debugging.
 *
 *  OUTPUT SCHEMA (one line per packet):
 *    CSI_DATA,idx,mac,rssi,rate,sig_mode,mcs,bw,noise_floor,channel,local_us,len,"[I0,Q0,I1,Q1,...]"
 *    (see firmware/README.md for the field table)
 * ============================================================================
 */

#include <WiFi.h>
#include <WiFiUdp.h>
#include "esp_wifi.h"
#include <string.h>

// ----------------------- CONFIG (match csi_tx.ino) -------------------------
static const char*    WIFI_SSID    = "csi_link";
static const char*    WIFI_PASS    = "csi_password_123";
static const int      WIFI_CHANNEL = 6;        // fixed 2.4 GHz channel (1/6/11)
static const uint16_t UDP_PORT     = 5000;

// 1 = auto-lock CSI to the first STA that connects (recommended, clean data).
// 0 = log every received packet (debug only).
#define FILTER_MODE 1
// ---------------------------------------------------------------------------

WiFiUDP  udp;
uint32_t csi_idx = 0;
uint8_t  udp_buf[64];
volatile bool have_target = false;
uint8_t  target_mac[6];

static inline bool mac_match(const uint8_t* a, const uint8_t* b) {
  return memcmp(a, b, 6) == 0;
}

void onWiFiEvent(WiFiEvent_t event, WiFiEventInfo_t info) {
  if (event == ARDUINO_EVENT_WIFI_AP_STACONNECTED) {
    memcpy(target_mac, info.wifi_ap_staconnected.mac, 6);
    have_target = true;
    Serial.printf("STA connected, locking CSI to %02x:%02x:%02x:%02x:%02x:%02x\n",
                  target_mac[0], target_mac[1], target_mac[2],
                  target_mac[3], target_mac[4], target_mac[5]);
  } else if (event == ARDUINO_EVENT_WIFI_AP_STADISCONNECTED) {
    Serial.println("STA disconnected (keeping CSI lock).");
  }
}

void wifi_csi_cb(void* ctx, wifi_csi_info_t* info) {
  if (!info || !info->buf || info->len == 0) return;
#if FILTER_MODE
  if (!have_target) return;
  if (!mac_match(info->mac, target_mac)) return;
#endif

  const wifi_pkt_rx_ctrl_t* rx = &info->rx_ctrl;

  Serial.print("CSI_DATA,");
  Serial.print(csi_idx++);            Serial.print(',');
  Serial.printf("%02x:%02x:%02x:%02x:%02x:%02x,",
                info->mac[0], info->mac[1], info->mac[2],
                info->mac[3], info->mac[4], info->mac[5]);
  Serial.print(rx->rssi);             Serial.print(',');
  Serial.print(rx->rate);             Serial.print(',');
  Serial.print(rx->sig_mode);         Serial.print(',');
  Serial.print(rx->mcs);              Serial.print(',');
  Serial.print(rx->cwb);              Serial.print(',');   // bandwidth
  Serial.print(rx->noise_floor);      Serial.print(',');
  Serial.print(rx->channel);          Serial.print(',');
  Serial.print(rx->timestamp);        Serial.print(',');   // local microseconds
  Serial.print(info->len);            Serial.print(',');

  Serial.print('"');
  Serial.print('[');
  const int8_t* d = info->buf;
  const int len = info->len;
  for (int i = 0; i < len; i++) {
    Serial.print(d[i]);
    if (i < len - 1) Serial.print(',');
  }
  Serial.print(']');
  Serial.print('"');
  Serial.print('\n');
}

void setup() {
  Serial.begin(921600);
  delay(200);
  Serial.println("\n== CSI Link RX ==");

  WiFi.onEvent(onWiFiEvent);
  WiFi.mode(WIFI_AP);
  WiFi.setSleep(false);

  bool ok = WiFi.softAP(WIFI_SSID, WIFI_PASS, WIFI_CHANNEL, 0, 4);
  Serial.printf("SoftAP '%s' ch %d: %s, IP %s\n",
                WIFI_SSID, WIFI_CHANNEL, ok ? "OK" : "FAILED",
                WiFi.softAPIP().toString().c_str());

  uint8_t mac[6];
  esp_wifi_get_mac(WIFI_IF_AP, mac);
  Serial.printf("RX(AP) MAC: %02x:%02x:%02x:%02x:%02x:%02x\n",
                mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);

  udp.begin(UDP_PORT);   // drain UDP so the stack doesn't reply "unreachable"

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

  Serial.println("CSI enabled. Waiting for TX to connect...");
}

void loop() {
  int n = udp.parsePacket();
  if (n > 0) udp.read(udp_buf, sizeof(udp_buf));
}
