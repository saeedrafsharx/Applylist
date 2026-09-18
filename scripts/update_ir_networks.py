"""
Refresh app/i18n/ir_networks.txt from RIPE NCC's public delegation list.

The file lists every IPv4 and IPv6 block RIPE has delegated to Iran. The app
uses it to pick Farsi as the default language for visitors from Iran who
haven't chosen one. Allocations change slowly; re-run this every few months:

    python scripts/update_ir_networks.py
"""

from __future__ import annotations

import ipaddress
import sys
import urllib.request
from pathlib import Path

SOURCE = "https://ftp.ripe.net/pub/stats/ripencc/delegated-ripencc-latest"
TARGET = Path(__file__).resolve().parent.parent / "app" / "i18n" / "ir_networks.txt"


def main() -> int:
    with urllib.request.urlopen(SOURCE, timeout=120) as resp:
        lines = resp.read().decode("ascii", "replace").splitlines()

    networks: list[str] = []
    for line in lines:
        parts = line.split("|")
        if len(parts) < 7 or parts[1] != "IR" or parts[6] not in ("allocated", "assigned"):
            continue
        kind, start, size = parts[2], parts[3], parts[4]
        if kind == "ipv4":
            # IPv4 entries are a start address and a host count, not always a
            # power of two; summarise into proper CIDR blocks.
            first = ipaddress.IPv4Address(start)
            last = first + int(size) - 1
            networks.extend(str(n) for n in ipaddress.summarize_address_range(first, last))
        elif kind == "ipv6":
            networks.append(f"{start}/{size}")

    if len(networks) < 100:
        print(f"Only {len(networks)} networks found - refusing to overwrite.", file=sys.stderr)
        return 1
    TARGET.write_text(
        f"# Networks delegated to Iran, from {SOURCE}\n"
        "# Regenerate with: python scripts/update_ir_networks.py\n"
        + "\n".join(networks) + "\n"
    )
    print(f"Wrote {len(networks)} networks to {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
