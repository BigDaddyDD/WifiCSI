#!/usr/bin/env python3
"""
Staged product-photography build of the CSI Live Monitor UI.

NOT connected to any ESP32 or model -- for PR/presentation photos of the
interface when the real hardware isn't reachable (built at the requester's
manager's request, for "how the system would look in the field" photos).
Visual layout, colors, and fonts are copied directly from live_monitor_gui.py
so photos are consistent with the real product.

Click any class row (empty/stand/sit/walk/run) on the left to set that as the
displayed state -- the whole window (big status, confidence bars, scrolling
log) updates to match, with small automatic jitter on the numbers so it reads
as live telemetry in a still photo. Numbers are representative sample values,
not derived from any real capture.

This file's own docstring and window title are the only markers of what this
is; there is no on-screen "MOCKUP" banner, so the photos themselves are clean.

Usage: python demo_state_mockup.py
"""
import random
import time
import tkinter as tk
import tkinter.font as tkfont

CLASS_COLORS = {'empty': '#455a64', 'stand': '#e08a1e', 'sit': '#c98a20',
                'walk': '#c0392b', 'run': '#8e44ad'}
CLASS_ORDER = ['empty', 'stand', 'sit', 'walk', 'run']
HEALTH_BG_OK = '#2e7d32'
JITTER_MS = 1000


class App:
    def __init__(self, root):
        self.root = root
        self.selected = 'empty'

        root.title('CSI Live Monitor (staged for photography -- see docstring)')
        root.configure(bg='#222'); root.geometry('900x680')
        f_big = tkfont.Font(family='Helvetica', size=46, weight='bold')
        f_med = tkfont.Font(family='Helvetica', size=18, weight='bold')
        f_small = tkfont.Font(family='Helvetica', size=12)

        self.health_var = tk.StringVar(value='link OK -- 98 Hz, RSSI -38 dBm')
        self.health_lbl = tk.Label(root, textvariable=self.health_var, font=f_small,
                                   fg='white', bg=HEALTH_BG_OK)
        self.health_lbl.pack(side='top', fill='x', ipady=6)

        self.status_var = tk.StringVar(value='Live monitoring')
        tk.Label(root, textvariable=self.status_var, font=f_med, fg='#ddd',
                bg='#222').pack(pady=(14, 4))

        self.big_var = tk.StringVar(value='--')
        self.big_lbl = tk.Label(root, textvariable=self.big_var, font=f_big,
                                fg='white', bg='#222')
        self.big_lbl.pack(pady=10)

        self.bars_frame = tk.Frame(root, bg='#222')
        self.bars_frame.pack(pady=6, fill='x', padx=40)
        self.bar_widgets = {}
        for cls in CLASS_ORDER:
            row = tk.Frame(self.bars_frame, bg='#222', cursor='hand2')
            row.pack(fill='x', pady=2)
            row.bind('<Button-1>', lambda e, c=cls: self.set_state(c))
            lbl = tk.Label(row, text=cls, font=f_small, fg='#ccc', bg='#222', width=8,
                          anchor='w', cursor='hand2')
            lbl.pack(side='left')
            lbl.bind('<Button-1>', lambda e, c=cls: self.set_state(c))
            canvas = tk.Canvas(row, height=18, bg='#111', highlightthickness=0, cursor='hand2')
            canvas.pack(side='left', fill='x', expand=True, padx=(6, 6))
            canvas.bind('<Button-1>', lambda e, c=cls: self.set_state(c))
            pct = tk.Label(row, text='0%', font=f_small, fg='#ccc', bg='#222', width=5)
            pct.pack(side='left')
            self.bar_widgets[cls] = (canvas, pct)

        self.log = tk.Text(root, height=8, bg='#111', fg='#8fd', font=('Consolas', 10),
                           insertbackground='white')
        self.log.pack(fill='both', expand=True, padx=14, pady=(4, 14))

        self.set_state('empty')
        self._jitter_tick()

    def _log(self, msg):
        self.log.insert('end', f'{time.strftime("%H:%M:%S")}  {msg}\n')
        self.log.see('end')
        # cap scrollback so the log doesn't grow unbounded during a long photo session
        if float(self.log.index('end-1c').split('.')[0]) > 300:
            self.log.delete('1.0', '2.0')

    def set_state(self, cls):
        self.selected = cls
        self._render()

    def _render(self):
        cls = self.selected
        presence = 'EMPTY' if cls == 'empty' else 'OCCUPIED'
        self.big_var.set(f'{presence}\n{cls.upper()}')
        self.big_lbl.config(bg=CLASS_COLORS[cls])
        self.root.configure(bg=CLASS_COLORS[cls])

        main_conf = random.uniform(0.45, 0.85)
        remainder = 1.0 - main_conf
        others = [c for c in CLASS_ORDER if c != cls]
        weights = [random.random() for _ in others]
        wsum = sum(weights) or 1.0
        proba = {cls: main_conf}
        for c, w in zip(others, weights):
            proba[c] = remainder * w / wsum

        for c in CLASS_ORDER:
            canvas, pct = self.bar_widgets[c]
            p = proba[c]
            canvas.delete('all')
            w = canvas.winfo_width() or 300
            canvas.create_rectangle(0, 0, w * p, 18, fill=CLASS_COLORS[c], width=0)
            pct.config(text=f'{p*100:.0f}%')

        rate = random.uniform(96.0, 99.5)
        rssi = random.randint(-42, -34)
        self.health_var.set(f'link OK -- {rate:.0f} Hz, RSSI {rssi} dBm')
        self._log(f'{presence:9s} {cls:6s}  conf={main_conf:.2f}')

    def _jitter_tick(self):
        self._render()
        self.root.after(JITTER_MS, self._jitter_tick)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
