from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def recalc_xlsx(file_path: Path) -> bool:
    lo = shutil.which("libreoffice") or shutil.which("soffice")
    if not lo:
        logger.warning("LibreOffice not found — skipping formula recalc")
        return False

    with tempfile.TemporaryDirectory() as tmp:
        result = subprocess.run(
            [
                lo,
                "--headless",
                "--calc",
                "--convert-to", "xlsx",
                "--outdir", tmp,
                str(file_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.error("LibreOffice recalc failed: %s", result.stderr)
            return False

        out = Path(tmp) / file_path.name
        if out.exists():
            shutil.copy2(out, file_path)
            logger.info("Recalculated formulas via LibreOffice")
            return True

    return False
