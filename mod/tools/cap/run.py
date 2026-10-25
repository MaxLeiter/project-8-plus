r"""Plain entry script for the CAP companion (the installer's addons\pyrun.py runs scripts, not packages):

    python addons\pyrun.py <addons> <addons>\cap\run.py --session <livelink.json> --mods <mods root> [--project DIR]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))     # the folder holding cap/, editor/, modkit/

from cap.companion import main  # noqa: E402

sys.exit(main())
