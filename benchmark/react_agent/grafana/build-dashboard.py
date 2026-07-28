#!/usr/bin/env python3
"""
Assemble react-serving-benchmark.json from a template + separate hero-panel
source files (HTML, CSS, JS).

Usage:
    python build-dashboard.py            # writes react-serving-benchmark.json
    python build-dashboard.py --check    # exits non-zero if output is stale
"""
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
HERO_DIR = SCRIPT_DIR / "hero-panel"
TEMPLATE = SCRIPT_DIR / "dashboard-template.json.tmpl"
OUTPUT = SCRIPT_DIR / "react-serving-benchmark.json"

PLACEHOLDERS = {
    "__HERO_HTML__": HERO_DIR / "panel.html",
    "__HERO_CSS__": HERO_DIR / "panel.css",
    "__HERO_JS__": HERO_DIR / "panel.js",
}


def inject(obj, replacements: dict):
    if isinstance(obj, str):
        for placeholder, content in replacements.items():
            if obj == placeholder:
                return content
        return obj
    if isinstance(obj, list):
        return [inject(item, replacements) for item in obj]
    if isinstance(obj, dict):
        return {key: inject(value, replacements) for key, value in obj.items()}
    return obj


def main():
    check_only = "--check" in sys.argv

    replacements = {}
    for placeholder, path in PLACEHOLDERS.items():
        if not path.exists():
            print(f"ERROR: missing {path}", file=sys.stderr)
            sys.exit(1)
        replacements[placeholder] = path.read_text()

    template = json.loads(TEMPLATE.read_text())
    dashboard = inject(template, replacements)
    rendered = json.dumps(dashboard, indent=2, ensure_ascii=False) + "\n"

    if check_only:
        if not OUTPUT.exists():
            print(f"Output file missing: {OUTPUT}")
            sys.exit(1)
        if OUTPUT.read_text() != rendered:
            print("Output is stale — run: python build-dashboard.py")
            sys.exit(1)
        print("Output is up to date.")
        sys.exit(0)

    OUTPUT.write_text(rendered)
    print(f"Wrote {OUTPUT.name} ({len(rendered)} bytes)")


if __name__ == "__main__":
    main()
