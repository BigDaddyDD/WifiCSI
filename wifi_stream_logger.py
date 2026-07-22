#!/usr/bin/env python3
"""
Wireless "Terminal A" -- the multi-source replacement for stream_logger.py.

Connects to one or more RX boards over TCP (firmware/csi_rx_wifi or
csi_rx_wifi2) instead of a USB serial port, and writes EACH source's CSI
lines to its own file in the exact same format stream_logger.py always used
(`<pc_time>\\t<raw CSI_DATA line>`), so collect_gui.py's reading/slicing code
works completely unchanged -- it just gets handed one stream file per source
now instead of one file total.

Each RX board joins your home Wi-Fi directly and is reachable by mDNS hostname
(AP+STA -- see CLAUDE.md 4f) -- NO second Wi-Fi adapter needed on the laptop,
for one antenna OR two. socket.create_connection() resolves hostnames the
same as IPs, so just point each --source at csi-rx.local / csi-rx2.local.

Usage (two antennas, both wireless):
  python wifi_stream_logger.py --source taoglas=csi-rx.local:3333 \\
                                --source stock=csi-rx2.local:3333

Usage (one antenna, wireless):
  python wifi_stream_logger.py --source taoglas=csi-rx.local:3333

Then run collect_blocks.py etc. with --sources taoglas,stock (see
collect_gui.py) -- it derives each stream file path from the tag using the
same convention this script writes to: data/study/_live_stream_<tag>.tsv
"""
import argparse
import os
import socket
import sys
import threading
import time

DEFAULT_DIR = os.path.join('data', 'study')


def stream_path(tag):
    return os.path.join(DEFAULT_DIR, f'_live_stream_{tag}.tsv')


class SourceLogger:
    """One TCP source -> one output file. Runs on its own thread, reconnects
    on drop (e.g. walked out of range) -- mirrors live_monitor_gui.CSIStream."""
    def __init__(self, tag, host, port):
        self.tag, self.host, self.port = tag, host, port
        self.out_path = stream_path(tag)
        self.count = 0
        self.connected = False
        self._stop = False
        self._sock = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop = True
        try:
            if self._sock:
                self._sock.close()
        except OSError:
            pass

    def _run(self):
        buf = b''
        with open(self.out_path, 'w', buffering=1, newline='') as f:
            while not self._stop:
                try:
                    self._sock = socket.create_connection((self.host, self.port), timeout=5)
                    self._sock.settimeout(1.0)
                    self.connected = True
                    sys.stderr.write(f"[{self.tag}] CONNECTED {self.host}:{self.port}\n")
                    sys.stderr.flush()
                    buf = b''
                    while not self._stop:
                        try:
                            chunk = self._sock.recv(4096)
                        except socket.timeout:
                            continue
                        if not chunk:
                            break
                        buf += chunk
                        while b'\n' in buf:
                            line, buf = buf.split(b'\n', 1)
                            s = line.decode('utf-8', 'ignore').strip()
                            if s.startswith('CSI_DATA'):
                                f.write(f"{time.time():.6f}\t{s}\n")
                                self.count += 1
                except OSError as e:
                    sys.stderr.write(f"[{self.tag}] ERROR {e}\n"); sys.stderr.flush()
                self.connected = False
                if not self._stop:
                    time.sleep(2.0)


def parse_source(spec):
    """'tag=host:port' -> (tag, host, port)"""
    tag, _, hostport = spec.partition('=')
    host, _, port = hostport.partition(':')
    if not tag or not host or not port:
        raise argparse.ArgumentTypeError(
            f"bad --source '{spec}', expected tag=host:port (e.g. taoglas=192.168.4.1:3333)")
    return tag, host, int(port)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--source', action='append', required=True, metavar='tag=host:port',
                    help='repeatable -- one per RX board, e.g. taoglas=192.168.4.1:3333')
    args = ap.parse_args()

    os.makedirs(DEFAULT_DIR, exist_ok=True)
    loggers = [SourceLogger(*parse_source(s)) for s in args.source]
    for lg in loggers:
        sys.stderr.write(f"[{lg.tag}] -> {lg.out_path}  (connecting to {lg.host}:{lg.port})\n")
        lg.start()
    sys.stderr.flush()

    hb = time.time()
    try:
        while True:
            time.sleep(0.5)
            if time.time() - hb > 2:
                hb = time.time()
                stats = '  '.join(f"{lg.tag}: {'UP' if lg.connected else 'DOWN'} "
                                  f"read={lg.count}" for lg in loggers)
                sys.stderr.write(f"HB  {stats}\n"); sys.stderr.flush()
    except KeyboardInterrupt:
        pass
    finally:
        for lg in loggers:
            lg.stop()


if __name__ == '__main__':
    main()
