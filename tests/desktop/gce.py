import sys
import os
import time

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
# TODO

from agentdesk.desktop import GCEProvider

provider = GCEProvider()

desktop = provider.create("test1")
