"""Regenerate app/data/geo.json from the ISO 3166 data shipped in pycountry.

pycountry packages Debian's iso-codes database (ISO 3166-1 / ISO 3166-2).
It is only needed at development time:

    pip install pycountry
    python scripts/gen_countries.py
"""

import json
from pathlib import Path

import pycountry

# Subdivisions are used to recognise "City, ST" style locations.
SUBDIVISION_COUNTRIES = ["IN", "US", "CA", "AU", "DE", "GB", "AE", "NL", "IE", "CH"]


def main() -> None:
    countries = []
    for c in sorted(pycountry.countries, key=lambda c: c.name):
        countries.append(
            {
                "code": c.alpha_2,
                "alpha3": c.alpha_3,
                "name": getattr(c, "common_name", None) or c.name,
                "official_name": getattr(c, "official_name", "") or "",
                "iso_name": c.name,
            }
        )
    subdivisions: dict[str, list[dict]] = {}
    for cc in SUBDIVISION_COUNTRIES:
        items = []
        for s in pycountry.subdivisions.get(country_code=cc) or []:
            if s.parent_code:  # skip second-level entries (districts, etc.)
                continue
            items.append({"code": s.code.split("-", 1)[1], "name": s.name, "type": s.type})
        subdivisions[cc] = sorted(items, key=lambda x: x["name"])
    out = {
        "source": f"pycountry {pycountry.__version__ if hasattr(pycountry, '__version__') else ''} (Debian iso-codes)",
        "countries": countries,
        "subdivisions": subdivisions,
    }
    path = Path(__file__).resolve().parent.parent / "app" / "data" / "geo.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"Wrote {len(countries)} countries to {path}")


if __name__ == "__main__":
    main()
