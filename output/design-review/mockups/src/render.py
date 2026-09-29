import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
SRC = Path(__file__).parent
OUT = SRC.parent
PAGES = [
    ("01-documents", "documents.html"),
    ("01b-documents-selected", "documents.html?sel=1"),
    ("01c-documents-light", "documents.html?theme=light"),
    ("02-document-extracted", "document.html"),
    ("02b-document-preparing", "document.html?state=preparing"),
    ("02c-document-failed", "document.html?state=failed"),
    ("03-results", "results.html"),
    ("04-ask-genie", "genie.html"),
    ("05-schemas", "schemas.html"),
    ("05b-schema-edit", "schemas.html?state=edit"),
]
THEMES = ["green", "blue", "navy"]
args = sys.argv[1:]
themes = [a for a in args if a in THEMES] or THEMES
only = {a for a in args if a not in THEMES}
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    for theme in themes:
      (OUT / theme).mkdir(exist_ok=True)
      for name, path in PAGES:
        if only and name not in only:
            continue
        if "theme=light" in path and theme != "green":
            continue
        file, _, query = path.partition("?")
        params = "&".join(x for x in [query, "" if theme == "green" or "theme=" in query else f"theme={theme}"] if x)
        pg.goto((SRC / file).as_uri() + ("?" + params if params else ""))
        pg.wait_for_load_state("networkidle")
        pg.evaluate("document.fonts.ready")
        pg.wait_for_timeout(300)
        pg.screenshot(path=str(OUT / theme / f"{name}.png"), full_page=False)
        print("rendered", theme, name)
    b.close()
