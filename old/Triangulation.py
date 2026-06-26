import serial
import numpy as np
import ast
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from collections import deque
import threading
import time

PORT_A = 'COM11'       # Receiver 1
PORT_B = 'COM16'       # Receiver 2 
BAUD_RATE = 921600     
SUBCARRIERS = 64
HISTORY_SIZE = 200

waterfall_A = deque(maxlen=HISTORY_SIZE)
waterfall_B = deque(maxlen=HISTORY_SIZE)

for _ in range(HISTORY_SIZE):
    waterfall_A.append(np.zeros(SUBCARRIERS))
    waterfall_B.append(np.zeros(SUBCARRIERS))

def parse_csi_line(line):
    try:
        line_str = line.decode('utf-8', errors='ignore').strip()
        if not line_str.startswith("CSI_DATA"):
            return None

        start_idx = line_str.find('[')
        end_idx = line_str.rfind(']')
        if start_idx == -1 or end_idx == -1: return None
            
        array_str = line_str[start_idx:end_idx+1]
        csi_vals = np.array(ast.literal_eval(array_str))
        if len(csi_vals) == 0: return None

        real = csi_vals[0::2]
        imag = csi_vals[1::2]
        min_len = min(len(real), len(imag))
        amplitude = np.sqrt(real[:min_len]**2 + imag[:min_len]**2)
        
        # Resize to fixed 64
        if len(amplitude) > SUBCARRIERS: amplitude = amplitude[:SUBCARRIERS]
        elif len(amplitude) < SUBCARRIERS: amplitude = np.pad(amplitude, (0, SUBCARRIERS - len(amplitude)), 'constant')
            
        # Filter DC spike
        if len(amplitude) > 33:
            amplitude[31:34] = (amplitude[30] + amplitude[34]) / 2

        return amplitude
    except Exception:
        return None

def serial_worker(port, baud, output_deque):
    try:
        print(f"Connecting to {port}...")
        ser = serial.Serial(port, baud, timeout=1)
        print(f"Connected to {port}!")
        
        while True:
            if ser.in_waiting > 0:
                raw_line = ser.readline()
                amp = parse_csi_line(raw_line)
                if amp is not None:
                    output_deque.append(amp)
            else:
                time.sleep(0.001)
    except Exception as e:
        print(f"Error on {port}: {e}")

# We launch two separate threads, one for each USB port
t1 = threading.Thread(target=serial_worker, args=(PORT_A, BAUD_RATE, waterfall_A))
t1.daemon = True
t1.start()

t2 = threading.Thread(target=serial_worker, args=(PORT_B, BAUD_RATE, waterfall_B))
t2.daemon = True
t2.start()

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8), sharex=True)
plt.subplots_adjust(hspace=0.3)

# Plot A
img_A = ax1.imshow(np.array(waterfall_A), aspect='auto', cmap='jet', animated=True, vmin=0, vmax=60)
ax1.set_title(f"Receiver A ({PORT_A})")
ax1.set_ylabel("Time")

# Plot B
img_B = ax2.imshow(np.array(waterfall_B), aspect='auto', cmap='jet', animated=True, vmin=0, vmax=60)
ax2.set_title(f"Receiver B ({PORT_B})")
ax2.set_xlabel("Subcarrier Index")
ax2.set_ylabel("Time")

def update(frame):
    # Update A
    data_A = np.array(waterfall_A)
    img_A.set_array(data_A)
    if len(data_A) > 0:
        ma = np.percentile(data_A, 99)
        mi = np.min(data_A)
        if ma > mi: img_A.set_clim(vmin=mi, vmax=ma)

    # Update B
    data_B = np.array(waterfall_B)
    img_B.set_array(data_B)
    if len(data_B) > 0:
        ma = np.percentile(data_B, 99)
        mi = np.min(data_B)
        if ma > mi: img_B.set_clim(vmin=mi, vmax=ma)
        
    return img_A, img_B

ani = animation.FuncAnimation(fig, update, interval=50, blit=False)
print("Dual-Spectrogram running. Close window to stop.")
plt.show()