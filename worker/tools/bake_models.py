"""Operator entry point for the model baker (ROADMAP 0A names it
tools/bake_models.py). The baker itself lives in models/bake.py because the
image build context excludes tools/ (worker/.dockerignore, INTERFACES.md §1);
this forwards to it so both spellings work from a checkout:

    python tools/bake_models.py --check
    python tools/bake_models.py --image vision --dest /tmp/models
"""

import os
import runpy
import sys

if __name__ == "__main__":
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.argv[0] = os.path.join(here, "models", "bake.py")
    runpy.run_path(sys.argv[0], run_name="__main__")
