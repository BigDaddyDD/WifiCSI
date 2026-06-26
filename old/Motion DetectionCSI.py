import serial
import numpy as np
import ast
import time
from collections import deque

SERIAL_PORT = 'COM11'  
BAUD_RATE = 921600     

WINDOW_SIZE = 20       # How many packets to analyze at once 
MOTION_THRESHOLD = 6.0 # Variance threshold. Below = Empty, Above = Motion.
                       

# Buffer to store the last 'N' amplitudes for the sliding window
amplitude_window = deque(maxlen=WINDOW_SIZE)

def parse_csi_line(line):
    try:
        line_str = line.decode('utf-8', errors='ignore').strip()
        if not line_str.startswith("CSI_DATA"):
            return None

        # Extract the list part "[...]"
        start_idx = line_str.find('[')
        end_idx = line_str.rfind(']')
        
        if start_idx == -1 or end_idx == -1:
            return None
            
        array_str = line_str[start_idx:end_idx+1]
        
        csi_vals = np.array(ast.literal_eval(array_str))
        
        if len(csi_vals) == 0:
            return None

        # Amplitude sqrt(Real^2 + Imag^2)
        real = csi_vals[0::2]
        imag = csi_vals[1::2]
        min_len = min(len(real), len(imag))
        amplitude = np.sqrt(real[:min_len]**2 + imag[:min_len]**2)
        
        return amplitude

    except Exception:
        return None

try:
    print(f"Connecting to {SERIAL_PORT}...")
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.1)
    print("System Armed. Waiting for data...")
    print("-" * 40)

    while True:
        if ser.in_waiting > 0:
            raw_line = ser.readline()
            amp = parse_csi_line(raw_line)

            if amp is not None:
                # Add new packet to our sliding window
                amplitude_window.append(amp)

                # Only analyze if we have a full window 
                if len(amplitude_window) == WINDOW_SIZE:
                    
                    #Convert window to a 2D matrix
                    window_matrix = np.array(amplitude_window)
                    
                    #Calculate Variance across time for each subcarrier
                    variances = np.var(window_matrix, axis=0)
                    
                    # Take the MEAN variance to get a single Score
                    stability_score = np.mean(variances)
                    
                    #DECISION LOGIC
                    status = "ROOM EMPTY"
                    color = "\033[92m" # Green text
                    
                    if stability_score > MOTION_THRESHOLD:
                        status = "!!! MOTION DETECTED !!!"
                        color = "\033[91m"

                    # \r returns cursor to start of line
                    print(f"\r{color}Status: {status} | Sensitivity Score: {stability_score:.2f} \033[0m", end="")

except KeyboardInterrupt:
    print("\nSystem stopped.")
    ser.close()
except Exception as e:
    print(f"\nError: {e}")