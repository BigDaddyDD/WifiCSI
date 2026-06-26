import serial
import time
import datetime

SERIAL_PORT = 'COM11'   
BAUD_RATE = 921600     
RECORD_TIME = 15       
timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
filename = f"csi_data_{timestamp}.csv"

try:
    print(f"Connecting to {SERIAL_PORT}...")
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
    print(f"Connected! Recording data to {filename} for {RECORD_TIME} seconds...")
    
    with open(filename, "w") as f:
        f.write("timestamp,raw_data\n")
        
        start_time = time.time()
        packet_count = 0
        
        while (time.time() - start_time) < RECORD_TIME:
            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                
                if line.startswith("CSI_DATA"):
                    f.write(f"{time.time()},{line}\n")
                    packet_count += 1
                    
                    if packet_count % 50 == 0:
                        print(f"Collected {packet_count} packets...")


except Exception as e:
    print(f"Error: {e}")