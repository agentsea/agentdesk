import subprocess
import psutil

# Start a new process
process_name = "your_process_name"
process = subprocess.Popen(["command", "arg1", "arg2"], executable=process_name)

# List all processes and find yours
for proc in psutil.process_iter(["pid", "name"]):
    if proc.info["name"] == process_name:
        print(f"Found process: {proc.info['pid']}")

# Terminate the process
process.terminate()
