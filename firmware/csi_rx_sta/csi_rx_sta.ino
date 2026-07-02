/*
 * CSI Link - RECEIVER (STA)   [multi-RX study firmware v2]   <-- LOG THIS BOARD
 * Board: Seeed XIAO ESP32-C3
 *
 * Associates to the csi_tx_ap SoftAP and computes CSI from the AP's broadcast
 * frames (+ beacons), filtered to the AP's BSSID. Flash to EACH receiver; log
 * all receivers on the same PC. Same CSI_DATA format as before, so log_csi.py /
 * collect_scripted.py work unchanged.
 *
 * [diag] every 2 s: wifi (connected?), csi_seen (any sender), matched (from AP),
 * last_src. matched climbing => working.
 */
#include <WiFi.h>
#include <WiFiUdp.h>
#include "esp_wifi.h"
#include <string.h>

static const char*    SSID = "csi_link";
static const char*    PASS = "csi_password_123";
static const uint16_t PORT = 5000;

WiFiUDP  udp;
uint8_t  udpbuf[64];
uint8_t  bssid[6];
bool     have_bssid = false;
volatile uint32_t g_all = 0, g_match = 0;
volatile uint8_t  last_mac[6] = {0};
uint32_t csi_idx = 0;

void wifi_csi_cb(void* ctx, wifi_csi_info_t* info) {
  if (!info || !info->buf || info->len == 0) return;
  g_all++;
  memcpy((void*)last_mac, info->mac, 6);
  if (have_bssid && memcmp(info->mac, bssid, 6) != 0) return;
  g_match++;

  const wifi_pkt_rx_ctrl_t* rx = &info->rx_ctrl;
  Serial.print("CSI_DATA,");
  Serial.print(csi_idx++);       Serial.print(',');
  Serial.printf("%02x:%02x:%02x:%02x:%02x:%02x,",
                info->mac[0], info->mac[1], info->mac[2],
                info->mac[3], info->mac[4], info->mac[5]);
  Serial.print(rx->rssi);        Serial.print(',');
  Serial.print(rx->rate);        Serial.print(',');
  Serial.print(rx->sig_mode);    Serial.print(',');
  Serial.print(rx->mcs);         Serial.print(',');
  Serial.print(rx->cwb);         Serial.print(',');
  Serial.print(rx->noise_floor); Serial.print(',');
  Serial.print(rx->channel);     Serial.print(',');
  Serial.print(rx->timestamp);   Serial.print(',');
  Serial.print(info->len);       Serial.print(',');
  Serial.print('"'); Serial.print('[');
  const int8_t* d = info->buf; const int len = info->len;
  for (int i = 0; i < len; i++) { Serial.print(d[i]); if (i < len - 1) Serial.print(','); }
  Serial.print(']'); Serial.print('"'); Serial.print('\n');
}

void setup() {
  Serial.begin(921600);
  delay(200);
  Serial.println("\n== CSI Link RX (STA) ==");
  WiFi.mode(WIFI_STA);
  esp_wifi_set_ps(WIFI_PS_NONE);
  WiFi.begin(SSID, PASS);
  Serial.print("Connecting");
  while (WiFi.status() != WL_CONNECTED) { delay(200); Serial.print("."); }
  Serial.println(" connected.");

  uint8_t* b = WiFi.BSSID();
  if (b) { memcpy(bssid, b, 6); have_bssid = true; }
  Serial.printf("AP BSSID (filter) %02x:%02x:%02x:%02x:%02x:%02x  my IP %s\n",
                bssid[0], bssid[1], bssid[2], bssid[3], bssid[4], bssid[5],
                WiFi.localIP().toString().c_str());
  udp.begin(PORT);

  wifi_csi_config_t c;
  memset(&c, 0, sizeof(c));
  c.lltf_en = true; c.htltf_en = false; c.stbc_htltf2_en = false;
  c.ltf_merge_en = true; c.channel_filter_en = true; c.manu_scale = false;
  esp_err_t r;
  r = esp_wifi_set_csi_config(&c);             if (r != ESP_OK) Serial.printf("ERR csi_config %d\n", r);
  r = esp_wifi_set_csi_rx_cb(&wifi_csi_cb, NULL); if (r != ESP_OK) Serial.printf("ERR csi_cb %d\n", r);
  r = esp_wifi_set_csi(true);                  if (r != ESP_OK) Serial.printf("ERR csi_en %d\n", r);
  Serial.println("CSI enabled.");
}

void loop() {
  int n = udp.parsePacket();
  if (n > 0) udp.read(udpbuf, sizeof(udpbuf));

  static uint32_t t = 0;
  if (millis() - t > 2000) {
    t = millis();
    Serial.printf("[diag] wifi=%d csi_seen=%lu matched=%lu last_src=%02x:%02x:%02x:%02x:%02x:%02x\n",
                  WiFi.status() == WL_CONNECTED,
                  (unsigned long)g_all, (unsigned long)g_match,
                  last_mac[0], last_mac[1], last_mac[2],
                  last_mac[3], last_mac[4], last_mac[5]);
  }
}
