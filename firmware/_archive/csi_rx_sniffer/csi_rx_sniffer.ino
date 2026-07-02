/*
 * CSI Link - RECEIVER (promiscuous sniffer) + DIAGNOSTICS
 * Board: Seeed XIAO ESP32-C3   <-- LOG THIS BOARD'S USB SERIAL
 *
 * Sniffs broadcast frames from csi_tx_broadcast.ino and prints one CSI_DATA
 * line per frame from the TX. Also prints a [diag] heartbeat every 2 s:
 *   csi_seen = total CSI callbacks (ANY sender)   -> is promiscuous CSI firing?
 *   matched  = CSI from our TX MAC                 -> are TX frames arriving?
 *   last_src = MAC of the most recent CSI frame    -> what are we actually hearing?
 *
 * Interpreting the heartbeat:
 *   csi_seen = 0            -> promiscuous CSI not firing at all (deeper issue)
 *   csi_seen > 0, matched=0 -> CSI works, but not from our TX (check TX / MAC / channel)
 *   matched  > 0            -> working; CSI_DATA lines should be streaming
 */
#include <WiFi.h>
#include "esp_wifi.h"
#include <string.h>

static const int     WIFI_CHANNEL  = 6;
static const uint8_t TARGET_MAC[6] = {0x02, 0x00, 0x00, 0x00, 0x00, 0x0A};  // = csi_tx_ap AP MAC

volatile uint32_t g_all = 0, g_match = 0;
volatile uint8_t  last_mac[6] = {0};
uint32_t csi_idx = 0;

void wifi_csi_cb(void* ctx, wifi_csi_info_t* info) {
  if (!info || !info->buf || info->len == 0) return;
  g_all++;
  memcpy((void*)last_mac, info->mac, 6);
  if (memcmp(info->mac, TARGET_MAC, 6) != 0) return;
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

// A dummy promiscuous RX callback — some IDF builds only deliver frames (and
// thus CSI) once the promiscuous engine has a registered callback.
void promisc_cb(void* buf, wifi_promiscuous_pkt_type_t type) { (void)buf; (void)type; }

void setup() {
  Serial.begin(921600);
  delay(200);
  Serial.println("\n== CSI Link RX (promiscuous sniffer) + diag ==");

  WiFi.mode(WIFI_STA);
  esp_wifi_set_ps(WIFI_PS_NONE);

  wifi_promiscuous_filter_t filt = {.filter_mask = WIFI_PROMIS_FILTER_MASK_ALL};
  esp_wifi_set_promiscuous_filter(&filt);
  esp_wifi_set_promiscuous_rx_cb(&promisc_cb);
  esp_wifi_set_promiscuous(true);
  esp_wifi_set_channel(WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE);

  wifi_csi_config_t c;
  memset(&c, 0, sizeof(c));
  c.lltf_en = true; c.htltf_en = false; c.stbc_htltf2_en = false;
  c.ltf_merge_en = true; c.channel_filter_en = true; c.manu_scale = false;

  esp_err_t r;
  r = esp_wifi_set_csi_config(&c);             if (r != ESP_OK) Serial.printf("ERR csi_config %d\n", r);
  r = esp_wifi_set_csi_rx_cb(&wifi_csi_cb, NULL); if (r != ESP_OK) Serial.printf("ERR csi_cb %d\n", r);
  r = esp_wifi_set_csi(true);                  if (r != ESP_OK) Serial.printf("ERR csi_en %d\n", r);

  Serial.printf("Sniffing CSI on ch %d, filter src 02:00:00:00:00:01\n", WIFI_CHANNEL);
}

void loop() {
  static uint32_t t = 0;
  if (millis() - t > 2000) {
    t = millis();
    uint8_t pri = 0; wifi_second_chan_t sec;
    esp_wifi_get_channel(&pri, &sec);
    Serial.printf("[diag] csi_seen=%lu matched=%lu ch=%d last_src=%02x:%02x:%02x:%02x:%02x:%02x\n",
                  (unsigned long)g_all, (unsigned long)g_match, pri,
                  last_mac[0], last_mac[1], last_mac[2],
                  last_mac[3], last_mac[4], last_mac[5]);
  }
}
