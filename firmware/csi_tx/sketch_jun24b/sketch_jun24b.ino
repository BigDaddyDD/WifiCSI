/*
 * ============================================================================
 *  CSI Link - TRANSMITTER (TX)
 *  Board: Seeed Studio XIAO ESP32-C3   (Arduino core 3.x / ESP-IDF 5.x)
 * ============================================================================
 *
 *  ROLE
 *    Connects to the RX board's SoftAP as a station (STA) and sends a tiny
 *    UDP packet at a FIXED rate. CSI is computed on the RX; this board is just
 *    a steady packet source ("metronome").
 *
 *    No custom MAC needed - the RX auto-detects this board's MAC when it
 *    connects and locks its CSI capture to it.
 *
 *  DIAGNOSTICS
 *    Prints Wi-Fi events (incl. disconnect reason codes) and, if it can't
 *    connect within 10 s, scans and lists visible networks so we can see
 *    whether the RX's SSID is even on the air.
 *
 *  Flash this to ONE board, csi_rx.ino to the OTHER. SSID/PASS must match.
 * ============================================================================
 */

#include <WiFi.h>
#include <WiFiUdp.h>
#include "esp_wifi.h"

// ----------------------- CONFIG (match csi_rx.ino) -------------------------
static const char*     WIFI_SSID        = "csi_link";
static const char*     WIFI_PASS        = "csi_password_123";   // >= 8 chars
static const IPAddress RX_IP(192, 168, 4, 1);                   // SoftAP default
static const uint16_t  UDP_PORT         = 5000;

// 10000 us = 10 ms = 100 Hz. Raise to 20000 (50 Hz) if Phase 0 shows drops.
static const uint32_t  SEND_INTERVAL_US = 10000;

// TX power in 0.25 dBm units (8..84). 60 -> 15 dBm. Fixed for reproducibility.
static const int8_t    TX_POWER_QDBM    = 60;
// ---------------------------------------------------------------------------

WiFiUDP       udp;
uint32_t      seq          = 0;
uint32_t      next_send_us = 0;
unsigned long connect_start = 0;
bool          scanned       = false;

void onWiFiEvent(WiFiEvent_t event, WiFiEventInfo_t info) {
  switch (event) {
    case ARDUINO_EVENT_WIFI_STA_CONNECTED:
      Serial.println("\n[evt] associated to AP");
      break;
    case ARDUINO_EVENT_WIFI_STA_GOT_IP:
      Serial.print("[evt] got IP ");
      Serial.println(WiFi.localIP());
      break;
    case ARDUINO_EVENT_WIFI_STA_DISCONNECTED:
      // reason codes: 15=4way handshake timeout (bad password),
      // 201=no AP found, 205=connection fail, 2=auth expire, ...
      Serial.printf("[evt] disconnected, reason=%d\n",
                    info.wifi_sta_disconnected.reason);
      break;
    default:
      break;
  }
}

void startConnect() {
  WiFi.disconnect(true, true);   // clear any stale state
  delay(100);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  connect_start = millis();
  scanned = false;
  Serial.print("Connecting to ");
  Serial.println(WIFI_SSID);
}

void sendAtRate() {
  uint32_t now = micros();
  if ((int32_t)(now - next_send_us) >= 0) {
    next_send_us += SEND_INTERVAL_US;
    if ((int32_t)(micros() - next_send_us) > (int32_t)SEND_INTERVAL_US) {
      next_send_us = micros() + SEND_INTERVAL_US;   // resync if we fell behind
    }
    udp.beginPacket(RX_IP, UDP_PORT);
    udp.write((uint8_t*)&seq, sizeof(seq));
    udp.endPacket();
    seq++;
  }
}

void setup() {
  Serial.begin(921600);
  delay(200);
  Serial.println("\n== CSI Link TX ==");

  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.onEvent(onWiFiEvent);
  startConnect();
}

void loop() {
  if (WiFi.status() == WL_CONNECTED) {
    static bool announced = false;
    if (!announced) {
      esp_wifi_set_max_tx_power(TX_POWER_QDBM);
      udp.begin(UDP_PORT);
      next_send_us = micros();
      uint8_t mac[6];
      esp_wifi_get_mac(WIFI_IF_STA, mac);
      Serial.printf("Connected. TX MAC %02x:%02x:%02x:%02x:%02x:%02x, sending ~%lu Hz\n",
                    mac[0], mac[1], mac[2], mac[3], mac[4], mac[5],
                    (unsigned long)(1000000UL / SEND_INTERVAL_US));
      announced = true;
    }
    sendAtRate();
  } else {
    static unsigned long last_dot = 0;
    if (millis() - last_dot > 500) { Serial.print('.'); last_dot = millis(); }

    if (!scanned && millis() - connect_start > 10000) {
      scanned = true;
      Serial.println("\nStill not connected. Scanning for networks...");
      int nn = WiFi.scanNetworks();
      if (nn <= 0) {
        Serial.println("  (no networks found - is the RX powered and booted?)");
      }
      for (int i = 0; i < nn; i++) {
        Serial.printf("  %2d: %-20s ch%2d rssi%4d %s\n", i,
                      WiFi.SSID(i).c_str(), WiFi.channel(i), WiFi.RSSI(i),
                      WiFi.SSID(i) == String(WIFI_SSID) ? "<-- target" : "");
      }
      WiFi.scanDelete();
      startConnect();
    }
  }
}
