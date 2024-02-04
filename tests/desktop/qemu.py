import sys
import os
import time

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))
# TODO

from agentdesk.vm import QemuProvider

provider = QemuProvider()

desktop = provider.create("test1", "~/vms/ubuntu_2204.qcow2")
