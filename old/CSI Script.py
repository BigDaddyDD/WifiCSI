import serial
import numpy as np
import matplotlib.pyplot as plt
import ast
import time

SERIAL_PORT = 'COM11'
BAUD_RATE = 921600

plt.ion()
fig, ax = plt.subplots()
line_plot, = ax.plot([], [], 'b-')

ax.set_ylim(0, 60)
ax.set_xlim(0, 64)
ax.set_title("Real-Time Wi-Fi CSI Amplitude")
ax.set_xlabel("Subcarrier Index")
ax.set_ylabel("Amplitude")
ax.grid(True)

def process_csi_line(raw_line):
    try:
        line_str = raw_line.decode('utf-8').strip()
        
        if not line_str.startswith("CSI_DATA"):
            return None

        # The data format is CSV.
        # We split by double quotes to find the list easily
        parts = line_str.split('"')
        
        if len(parts) < 2:
            return None
            
        array_str = parts[1] 
        
        data_values = ast.literal_eval(array_str)
        data_values = np.array(data_values)

        if len(data_values) == 0:
            return None

        real_part = data_values[0::2]
        imag_part = data_values[1::2]
        
        # Ensure lengths match (sometimes packets get cut off)
        min_len = min(len(real_part), len(imag_part))
        real_part = real_part[:min_len]
        imag_part = imag_part[:min_len]

        amplitude = np.sqrt(real_part**2 + imag_part**2)
        
        return amplitude

    except Exception as e:
        return None

try:
    print(f"Connecting to {SERIAL_PORT} at {BAUD_RATE} baud...")
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
    print("Connected! Waiting for CSI data...")
    print("Press Ctrl+C to stop.")

    while True:
        if ser.in_waiting > 0:
            raw_line = ser.readline()
            
            amplitude = process_csi_line(raw_line)
            
            if amplitude is not None:
                line_plot.set_xdata(np.arange(len(amplitude)))
                line_plot.set_ydata(amplitude)
                
                if np.max(amplitude) > ax.get_ylim()[1]:
                    ax.set_ylim(0, np.max(amplitude) + 10)
                
                fig.canvas.draw()
                fig.canvas.flush_events()

except KeyboardInterrupt:
    print("\nStopping...")
    ser.close()
    print("Port closed.")

except serial.SerialException:
    print(f"\nError: Could not open {SERIAL_PORT}.")