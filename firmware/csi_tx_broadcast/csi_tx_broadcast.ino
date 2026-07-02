/*
 * ============================================================================
 *  CSI Link - TRANSMITTER (broadcast injector)   [multi-RX study firmware]
 *  Board: Seeed XIAO ESP32-C3
 * ============================================================================
 *
 *  Broadcasts a small 802.11 frame at a fixed rate on a fixed channel, with a
 *  fixed source MAC. NO association: any number of receiver "sniffers" can
 *  capture these frames and compute CSI in parallel. This replaces the old
 *  associated-link firmware so we can scale to 1/2/3 receivers with no change
 *  in capture method (avoids a mid-study confound).
 *
 *  Pair with csi_rx_sniffer.ino on each receiver (same WIFI_CHANNEL).
 *
 *  NOTE: raw injection + promiscuous CSI is a different ESP-IDF path than the
 *  old associated firmware -> re-run the Phase-0 rate/drop check before
 *  collecting a real dataset.
 * ============================================================================
 */
#include <WiFi.h>
#include "esp_wifi.h"

static const int      WIFI_CHANNEL     = 6;        // must match the receivers
static const uint32_t SEND_INTERVAL_US = 10000;    // 10 ms = 100 Hz
static const int8_t   TX_POWER_QDBM    = 60;       // 60 * 0.25 = 15 dBm

// Minimal 802.11 data frame. A1 = broadcast, A2/A3 = our fixed source MAC
// (02:00:00:00:00:01) which the receivers filter on.
static uint8_t frame[] = {
  0x08, 0x00, 0x00, 0x00,
  0xff, 0xff, 0xff, 0xff, 0xff, 0xff,     // A1 destination = broadcast
  0x02, 0x00, 0x00, 0x00, 0x00, 0x01,     // A2 source
  0x02, 0x00, 0x00, 0x00, 0x00, 0x01,     // A3 BSSID
  0x00, 0x00,                             // sequence control
  'C', 'S', 'I', '-', 'L', 'I', 'N', 'K'  // payload
};

static uint32_t next_us = 0;
static uint32_t sent = 0;
static esp_err_t last_e = ESP_OK;

void setup() {
  Serial.begin(921600);
  delay(200);
  Serial.println("\n== CSI Link TX (broadcast injector) ==");

  WiFi.mode(WIFI_STA);                    // starts Wi-Fi; we do NOT connect
  esp_wifi_set_ps(WIFI_PS_NONE);
  esp_wifi_set_promiscuous(true);         // monitor mode: required for raw injection to radiate
  esp_wifi_set_channel(WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE);
  esp_wifi_set_max_tx_power(TX_POWER_QDBM);

  Serial.printf("Broadcasting on ch %d at ~%lu Hz (src 02:00:00:00:00:01)\n",
                WIFI_CHANNEL, 1000000UL / SEND_INTERVAL_US);
  next_us = micros();
}

void loop() {
  uint32_t now = micros();
  if ((int32_t)(now - next_us) >= 0) {
    next_us += SEND_INTERVAL_US;
    if ((int32_t)(micros() - next_us) > (int32_t)SEND_INTERVAL_US)
      next_us = micros() + SEND_INTERVAL_US;      // resync if we fell behind
    last_e = esp_wifi_80211_tx(WIFI_IF_STA, frame, sizeof(frame), true);  // en_sys_seq=true
    sent++;
  }

  static uint32_t t = 0;
  if (millis() - t > 2000) {
    t = millis();
    // last_tx_err should be 0 (ESP_OK). Non-zero => injection is being rejected.
    Serial.printf("[diag] sent=%lu last_tx_err=%d\n", (unsigned long)sent, (int)last_e);
  }
}
