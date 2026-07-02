/*
 * CSI Link - TRANSMITTER (SoftAP broadcaster)   [multi-RX study firmware v2]
 * Board: Seeed XIAO ESP32-C3
 *
 * Creates a SoftAP on a fixed channel and BROADCASTS a UDP packet at a fixed
 * rate. Every receiver that associates to this AP hears the broadcasts (and
 * beacons) and computes CSI from them -> supports 1..N receivers.
 *
 * Pair with csi_rx_sta.ino on each receiver. This replaces the promiscuous
 * sniffer approach (raw injection didn't radiate on the C3).
 */
#include <WiFi.h>
#include <WiFiUdp.h>
#include "esp_wifi.h"

static const char*    SSID = "csi_link";
static const char*    PASS = "csi_password_123";
static const int      CH   = 6;
static const uint16_t PORT = 5000;
static const IPAddress BCAST(192, 168, 4, 255);     // SoftAP subnet broadcast
static const uint32_t INTERVAL_US = 10000;          // 100 Hz

WiFiUDP  udp;
uint32_t seq = 0, next_us = 0, sent = 0;

void setup() {
  Serial.begin(921600);
  delay(200);
  Serial.println("\n== CSI Link TX (SoftAP broadcaster) ==");
  WiFi.mode(WIFI_AP);
  // Fixed AP MAC so promiscuous receivers can filter us deterministically.
  static uint8_t apmac[6] = {0x02, 0x00, 0x00, 0x00, 0x00, 0x0A};
  esp_err_t me = esp_wifi_set_mac(WIFI_IF_AP, apmac);
  if (me != ESP_OK) Serial.printf("WARN set_mac(AP) failed %d (use printed BSSID)\n", me);
  WiFi.softAP(SSID, PASS, CH, 0, 4);                 // up to 4 receivers
  esp_wifi_set_ps(WIFI_PS_NONE);
  // Force OFDM tx rate (6 Mbps) so our frames carry the LTF that CSI needs.
  // Default beacons/broadcasts go out as 1 Mbps DSSS -> no CSI.
  esp_err_t re = esp_wifi_config_80211_tx_rate(WIFI_IF_AP, WIFI_PHY_RATE_6M);
  if (re != ESP_OK) Serial.printf("WARN tx_rate failed %d\n", re);

  uint8_t mac[6];
  esp_wifi_get_mac(WIFI_IF_AP, mac);
  Serial.printf("SoftAP '%s' ch %d  BSSID %02x:%02x:%02x:%02x:%02x:%02x  IP %s\n",
                SSID, CH, mac[0], mac[1], mac[2], mac[3], mac[4], mac[5],
                WiFi.softAPIP().toString().c_str());
  udp.begin(PORT);
  next_us = micros();
}

void loop() {
  uint32_t now = micros();
  if ((int32_t)(now - next_us) >= 0) {
    next_us += INTERVAL_US;
    if ((int32_t)(micros() - next_us) > (int32_t)INTERVAL_US)
      next_us = micros() + INTERVAL_US;
    udp.beginPacket(BCAST, PORT);
    udp.write((uint8_t*)&seq, sizeof(seq));
    udp.endPacket();
    seq++; sent++;
  }
  static uint32_t t = 0;
  if (millis() - t > 2000) {
    t = millis();
    uint8_t m[6]; esp_wifi_get_mac(WIFI_IF_AP, m);
    uint8_t pri = 0; wifi_second_chan_t sec;
    esp_wifi_get_channel(&pri, &sec);
    Serial.printf("[diag] sent=%lu stations=%d bssid=%02x:%02x:%02x:%02x:%02x:%02x ch=%d\n",
                  (unsigned long)sent, WiFi.softAPgetStationNum(),
                  m[0], m[1], m[2], m[3], m[4], m[5], pri);
  }
}
