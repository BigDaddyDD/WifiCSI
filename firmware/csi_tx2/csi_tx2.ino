/*
 * CSI Link - TRANSMITTER (STA metronome) -- SECOND PAIR (antenna #2)
 * Board: Seeed XIAO ESP32-C3   (built with esp32 core 2.0.x)
 *
 * Identical to firmware/csi_tx/csi_tx.ino except it pairs with csi_rx_wifi2
 * (SSID "csi_link2", RX at 192.168.5.1) instead of the first pair, so both
 * TX/RX pairs can run at the same time in the same room without colliding
 * -- see csi_rx_wifi2.ino for why the SSID/channel/subnet all differ.
 */
#include <WiFi.h>
#include <WiFiUdp.h>
#include "esp_wifi.h"

static const char*    SSID = "csi_link2";
static const char*    PASS = "csi_password_123";
static const IPAddress RX_IP(192, 168, 5, 1);
static const uint16_t PORT = 5000;
static const uint32_t INTERVAL_US = 10000;          // 100 Hz

WiFiUDP  udp;
uint32_t seq = 0, next_us = 0;
volatile int last_reason = 0;

void onWiFiEvent(WiFiEvent_t e, WiFiEventInfo_t info) {
  if (e == ARDUINO_EVENT_WIFI_STA_DISCONNECTED)
    last_reason = info.wifi_sta_disconnected.reason;
}

void setup() {
  Serial.begin(921600);
  delay(200);
  Serial.println("\n== CSI Link TX #2 (STA metronome, core 2.0.x) ==");
  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);
  WiFi.onEvent(onWiFiEvent);
  WiFi.begin(SSID, PASS);
  udp.begin(PORT);
  next_us = micros();
}

void loop() {
  if (WiFi.status() == WL_CONNECTED) {
    uint32_t now = micros();
    if ((int32_t)(now - next_us) >= 0) {
      next_us += INTERVAL_US;
      if ((int32_t)(micros() - next_us) > (int32_t)INTERVAL_US)
        next_us = micros() + INTERVAL_US;
      udp.beginPacket(RX_IP, PORT);
      udp.write((uint8_t*)&seq, sizeof(seq));
      udp.endPacket();
      seq++;
    }
  }
  static uint32_t t = 0;
  if (millis() - t > 2000) {
    t = millis();
    Serial.printf("[diag] wifi=%d seq=%lu rssi=%d last_reason=%d\n",
                  WiFi.status() == WL_CONNECTED, (unsigned long)seq,
                  (int)WiFi.RSSI(), last_reason);
  }
}
