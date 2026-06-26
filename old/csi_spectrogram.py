import serial
import numpy as np
import ast
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from collections import deque
import threading
import time

SERIAL_PORT = 'COM11'
BAUD_RATE = 921600
SUBCARRIERS = 64
HISTORY_SIZE = 200

waterfall_data = deque(maxlen=HISTORY_SIZE)
for _ in range(HISTORY_SIZE):
    waterfall_data.append(np.zeros(SUBCARRIERS))

def parse_csi_line(line):
    try:
        line_str = line.decode('utf-8', errors='ignore').strip()
        if not line_str.startswith("CSI_DATA"):
            return None

        start_idx = line_str.find('[')
        end_idx = line_str.rfind(']')
        
        if start_idx == -1 or end_idx == -1:
            return None
            
        array_str = line_str[start_idx:end_idx+1]
        csi_vals = np.array(ast.literal_eval(array_str))
        
        if len(csi_vals) == 0:
            return None

        real = csi_vals[0::2]
        imag = csi_vals[1::2]
        min_len = min(len(real), len(imag))
        amplitude = np.sqrt(real[:min_len]**2 + imag[:min_len]**2)
        
        if len(amplitude) > SUBCARRIERS:
            amplitude = amplitude[:SUBCARRIERS]
        elif len(amplitude) < SUBCARRIERS:
            amplitude = np.pad(amplitude, (0, SUBCARRIERS - len(amplitude)), 'constant')
            
        return amplitude
    except Exception:
        return None

def read_serial_loop():
    try:
        print(f"Connecting to {SERIAL_PORT} at {BAUD_RATE} baud...")
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
        print("Connected! Spectrogram starting...")
        
        while True:
            if ser.in_waiting > 0:
                raw_line = ser.readline()
                amp = parse_csi_line(raw_line)
                
                if amp is not None:
                    if len(amp) > 33:
                        amp[31:34] = (amp[30] + amp[34]) / 2
                    
                    waterfall_data.append(amp)
            else:
                time.sleep(0.001) 
                
    except Exception as e:
        print(f"Serial Error: {e}")

thread = threading.Thread(target=read_serial_loop)
thread.daemon = True
thread.start()

fig, ax = plt.subplots(figsize=(10, 6))

# 'aspect="auto"' stretches the pixels to fill the window
# 'cmap="jet"' gives us the classic blue-to-red heat map
heatmap = ax.imshow(np.array(waterfall_data), aspect='auto', cmap='jet', animated=True, vmin=0, vmax=60)

ax.set_title(f"Real-Time Wi-Fi Spectrogram ({BAUD_RATE} baud)")
ax.set_ylabel("Time History")
ax.set_xlabel("Subcarrier Index")
plt.colorbar(heatmap, label="Signal Strength")

def update(frame):
    data_snapshot = np.array(waterfall_data)
    
    heatmap.set_array(data_snapshot)
    
    if len(data_snapshot) > 0:
        current_max = np.percentile(data_snapshot, 99)
        current_min = np.min(data_snapshot)
        if current_max > current_min: 
             heatmap.set_clim(vmin=current_min, vmax=current_max)
    
    return heatmap,

ani = animation.FuncAnimation(fig, update, interval=50, blit=False)
plt.show()