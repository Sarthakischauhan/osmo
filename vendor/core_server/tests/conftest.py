from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent

for src_path in (
    ROOT / "src",
    REPO_ROOT / "core_harness" / "src",
    REPO_ROOT / "core_ai" / "src",
):
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))
