import importlib.util
import os
import sys
import tempfile
from pathlib import Path

os.environ["QWENPAW_WORKING_DIR"] = tempfile.mkdtemp(prefix="qpai-test-")
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("qpai", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
module = importlib.util.module_from_spec(spec)
sys.modules["qpai"] = module
spec.loader.exec_module(module)
