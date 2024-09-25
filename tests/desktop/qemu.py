import sys
import os
from agentdesk.runtime import QemuProvider

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))
# TODO

provider = QemuProvider()

desktop = provider.create()
