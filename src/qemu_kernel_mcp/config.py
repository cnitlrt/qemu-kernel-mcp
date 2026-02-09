from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
KERNELS_DIR = PROJECT_ROOT / "kernels"
STATE_DIR = PROJECT_ROOT / ".state"
QEMU_LOG_PATH = STATE_DIR / "qemu.log"
POC_TARGET = SCRIPTS_DIR / "core" / "exp"

DEFAULT_KERNEL_REPO = "https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git"

