import sys
import os
from agentdesk.vm import GCEProvider
from agentdesk.vm.img import JAMMY

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))
# TODO

provider = GCEProvider()

desktop = provider.create(image=JAMMY.gce)

desktop.view()
