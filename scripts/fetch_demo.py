"""At container start, download private demo runs into COLDOPEN_RUNS.

Why: the Space is public but the demo run contains the client's scripts, so it
cannot live in the Space repo or the image. Set two Space secrets:
  DEMO_RUNS_URL   a direct link to a .zip of run folders (e.g. a file in a
                  private Hugging Face dataset: https://huggingface.co/datasets/<org>/<name>/resolve/main/runs.zip)
  HF_TOKEN        a read token for that private dataset (sent as a Bearer header)
Viewing the runs then still requires DEMO_ACCESS_CODE. No secrets: no-op.
"""

from __future__ import annotations

import io
import os
import sys
import urllib.request
import zipfile
from pathlib import Path


def main() -> int:
    url = os.environ.get("DEMO_RUNS_URL")
    if not url:
        print("fetch_demo: DEMO_RUNS_URL not set, starting without demo runs")
        return 0
    dest = Path(os.environ.get("COLDOPEN_RUNS", "runs"))
    dest.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url)
    token = os.environ.get("HF_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        data = urllib.request.urlopen(req, timeout=120).read()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for m in z.namelist():
                if m.startswith("/") or ".." in Path(m).parts:
                    raise ValueError(f"unsafe path in zip: {m}")
            z.extractall(dest)
    except Exception as e:  # noqa: BLE001 - the app should still start
        print(f"fetch_demo: could not load demo runs ({type(e).__name__}); starting without them", file=sys.stderr)
        return 0
    print(f"fetch_demo: demo runs ready in {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
