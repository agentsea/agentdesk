import sys
import os
from agentdesk.runtime.gce import GCEProvider
from agentdesk.runtime.img import JAMMY

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))
# TODO

provider = GCEProvider()

desktop = provider.create(image=JAMMY.gce)

desktop.view()
