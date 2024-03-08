import sys
import os
from agentdesk.vm import QemuProvider

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))
# TODO

provider = QemuProvider()

desktop = provider.create()
