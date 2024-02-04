import sys
import os
import time

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))
# TODO

from agentdesk.vm import GCEProvider
from agentdesk.vm.img import JAMMY

provider = GCEProvider()

desktop = provider.create("test1", image=JAMMY.gce)

desktop.view()
