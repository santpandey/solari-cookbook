"""Where audit artifacts live. AUDITS_DIR overrides the default ./audits —
on Railway it's a mounted volume (e.g. /data/audits) so reports, the job
journal and the history DB survive redeploys."""

import os
import pathlib


def audits_dir():
    d = os.environ.get("AUDITS_DIR")
    return pathlib.Path(d).resolve() if d else pathlib.Path("audits").resolve()
