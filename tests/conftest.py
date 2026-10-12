import os
import sys
import tempfile

# Settings and logs go to a throwaway folder
os.environ["CONFIG_DIR"] = tempfile.mkdtemp(prefix="setuparr-test-config-")
os.environ["LOG_OUTPUT"] = "file"
os.environ["WEB_PORT"] = "0"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
