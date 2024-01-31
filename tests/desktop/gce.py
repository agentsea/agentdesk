import sys
import os
import time

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))
# TODO

from agentdesk.desktop import GCEProvider
from agentdesk.desktop.img import JAMMY

provider = GCEProvider()

desktop = provider.create("test1", image=JAMMY.gce)

desktop.view()
