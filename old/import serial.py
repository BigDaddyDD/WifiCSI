import serial
import numpy as np
import matplotlib.pyplot as plt
import ast  # Safely evaluates string lists like "[1,2,3]"
import time

# --- CONFIGURATION ---
SERIAL_PORT = 'COM11'  # <--- CHANGE THIS to your Receiver's port (e.g., COM3 on Windows, /dev/ttyUSB0 on Linux/Mac)
BAUD_RATE = 96000   # Ensure this matches your Arduino code (usually 115200, 921600, or 460800)

# --- SETUP PLOTTING ---
plt.ion() # Interactive mode on
fig, ax = plt.subplots()
line_plot, = ax.plot([], [], 'b-') # Blue line

# Initial graph limits (We will auto-adjust, but this sets the start)
ax.set_ylim(0, 60)
ax.set_xlim(0, 64) # Typical number of subcarriers for ESP32/Standard Wi-Fi
ax.set_title("Real-Time Wi-Fi CSI Amplitude")
ax.set_xlabel("Subcarrier Index")
ax.set_ylabel("Amplitude")
ax.grid(True)

def process_csi_line(raw_line):
    try:
        line_str = raw_line.decode('utf-8').strip()
        
        # Filter: Only process lines that look like CSI data
        if not line_str.startswith("CSI_DATA"):
            return None

        # The data format is CSV. The last element is the string list "[...]"
        # We split by double quotes to find the list easily
        parts = line_str.split('"')
        
        if len(parts) < 2:
            return None
            
        # Extract the list string: [0,0,0,4,-5,4,-5...]
        array_str = parts[1] 
        
        # Convert string list to actual python list
        # data_values will be [R, I, R, I, R, I...] (Real, Imaginary pairs)
        data_values = ast.literal_eval(array_str)
        data_values = np.array(data_values)

        # Handle Empty or Malformed packets
        if len(data_values) == 0:
            return None

        # --- MATH: CALCULATE AMPLITUDE ---
        # CSI data comes as pairs: Real part (even indices) and Imaginary part (odd indices)
        real_part = data_values[0::2]
        imag_part = data_values[1::2]
        
        # Ensure lengths match (sometimes packets get cut off)
        min_len = min(len(real_part), len(imag_part))
        real_part = real_part[:min_len]
        imag_part = imag_part[:min_len]

        # Amplitude = Sqrt(Real^2 + Imaginary^2)
        amplitude = np.sqrt(real_part**2 + imag_part**2)
        
        return amplitude

    except Exception as e:
        # Serial data is noisy; ignore corrupted lines
        # print(f"Error parsing line: {e}") 
        return None

# --- MAIN LOOP ---
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
                # Update the plot
                line_plot.set_xdata(np.arange(len(amplitude)))
                line_plot.set_ydata(amplitude)
                
                # Dynamic scaling if spikes occur
                if np.max(amplitude) > ax.get_ylim()[1]:
                    ax.set_ylim(0, np.max(amplitude) + 10)
                
                # Render
                fig.canvas.draw()
                fig.canvas.flush_events()

except KeyboardInterrupt:
    print("\nStopping...")
    ser.close()
    print("Port closed.")

except serial.SerialException:
    print(f"\nError: Could not open {SERIAL_PORT}. Check if the Arduino IDE Serial Monitor is still open!")