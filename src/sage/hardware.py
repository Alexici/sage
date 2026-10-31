"""What the machine can do, read from /proc and /sys. A suggestion for the setup page, never a gate.

Every read is allowed to fail: a missing file means "unknown" (None), never an exception."""
import glob
import os
from dataclasses import dataclass

GIB = 1024 ** 3


@dataclass(frozen=True)
class Hardware:
    cpu: str                  # model name, "" when unknown
    ram_gb: float | None      # GiB, one decimal
    vram_gb: float | None     # GiB of the GPU with the most video memory, None when unreadable
    gpu: str | None = None    # product name when the driver exposes one

    def to_dict(self) -> dict:
        return {"cpu": self.cpu, "ram_gb": self.ram_gb, "vram_gb": self.vram_gb, "gpu": self.gpu}


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(65536)
    except OSError:
        return None


def _cpu(proc: str) -> str:
    for line in (_read(os.path.join(proc, "cpuinfo")) or "").splitlines():
        if line.lower().startswith("model name") and ":" in line:
            return " ".join(line.split(":", 1)[1].split())[:80]
    return ""


def _ram_gb(proc: str) -> float | None:
    for line in (_read(os.path.join(proc, "meminfo")) or "").splitlines():
        if line.startswith("MemTotal:"):
            try:
                return round(int(line.split()[1]) * 1024 / GIB, 1)
            except (IndexError, ValueError):
                return None
    return None


def _gpu(drm: str) -> tuple[float | None, str | None]:
    """The card with the most VRAM. On a Ryzen 7800X3D the integrated GPU may be card0, so never take the first."""
    best, name = 0, None
    for path in sorted(glob.glob(os.path.join(glob.escape(drm), "card[0-9]*", "device", "mem_info_vram_total"))):
        try:
            size = int((_read(path) or "").strip())
        except ValueError:
            continue
        if size > best:
            best = size
            product = _read(os.path.join(os.path.dirname(path), "product_name"))
            name = " ".join(product.split())[:80] if product and product.strip() else None
    return (round(best / GIB, 1), name) if best > 0 else (None, None)


def probe(proc: str = "/proc", drm: str = "/sys/class/drm") -> Hardware:
    vram, gpu = _gpu(drm)
    return Hardware(_cpu(proc), _ram_gb(proc), vram, gpu)
