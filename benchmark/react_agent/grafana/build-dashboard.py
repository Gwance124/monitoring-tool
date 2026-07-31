#!/usr/bin/env python3
"""
Assemble each dashboard JSON from its template + separate hero-panel source
files (HTML, CSS, JS).

Usage:
    python build-dashboard.py            # writes both dashboard JSON files
    python build-dashboard.py --check    # exits non-zero if either is stale
"""
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

# Each dashboard is a (hero-panel source dir, template, output) triple. All
# three share the same placeholder names, resolved independently per build.
BUILDS = (
    (
        SCRIPT_DIR / "hero-panel",
        SCRIPT_DIR / "dashboard-template.json.tmpl",
        SCRIPT_DIR / "react-serving-benchmark.json",
    ),
    (
        SCRIPT_DIR / "hero-panel-cache",
        SCRIPT_DIR / "dashboard-template-cache.json.tmpl",
        SCRIPT_DIR / "react-serving-benchmark-cache.json",
    ),
)


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


def render(hero_dir: Path, template: Path) -> str:
    placeholders = {
        "__HERO_HTML__": hero_dir / "panel.html",
        "__HERO_CSS__": hero_dir / "panel.css",
        "__HERO_JS__": hero_dir / "panel.js",
    }
    replacements = {}
    for placeholder, path in placeholders.items():
        if not path.exists():
            print(f"ERROR: missing {path}", file=sys.stderr)
            sys.exit(1)
        replacements[placeholder] = path.read_text()

    dashboard = inject(json.loads(template.read_text()), replacements)
    return json.dumps(dashboard, indent=2, ensure_ascii=False) + "\n"


def main():
    check_only = "--check" in sys.argv
    stale = False

    for hero_dir, template, output in BUILDS:
        rendered = render(hero_dir, template)

        if check_only:
            if not output.exists():
                print(f"Output file missing: {output}")
                stale = True
                continue
            if output.read_text() != rendered:
                print(f"{output.name} is stale — run: python build-dashboard.py")
                stale = True
                continue
            print(f"{output.name} is up to date.")
            continue

        output.write_text(rendered)
        print(f"Wrote {output.name} ({len(rendered)} bytes)")

    if check_only and stale:
        sys.exit(1)


if __name__ == "__main__":
    main()
