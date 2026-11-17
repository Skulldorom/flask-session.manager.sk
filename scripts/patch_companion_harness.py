"""Add a browser selector to the pinned companion harness in its CI checkout.

The React repository is read-only input. This guarded, ephemeral adaptation
keeps its test implementation authoritative while allowing Firefox coverage.
"""

from __future__ import annotations

import argparse
from pathlib import Path

OLD = (
    '    const { chromium } = require(path.join(appRoot, "node_modules", "playwright"));\n'
    "    const browser = await chromium.launch({ headless: true });\n"
    "    try {\n"
    '      await exerciseLifecycle(browser, apiUrl, { name: "same-origin", apiUrl });\n'
    '      await exerciseLifecycle(browser, crossOrigin, { name: "cross-site", apiUrl });\n'
    "    } finally {\n"
    "      await browser.close();\n"
    "    }\n"
)

NEW = (
    '    const browserName = process.env.COMPANION_BROWSER || "chromium";\n'
    '    const browserType = require(path.join(appRoot, "node_modules", "playwright"))[browserName];\n'
    "    if (!browserType) throw new Error(`Unsupported COMPANION_BROWSER: ${browserName}`);\n"
    "    const browser = await browserType.launch({ headless: true });\n"
    "    try {\n"
    "      await exerciseLifecycle(browser, apiUrl, { name: `${browserName} same-origin`, apiUrl });\n"
    '      if (browserName === "chromium") {\n'
    '        await exerciseLifecycle(browser, crossOrigin, { name: "chromium cross-site", apiUrl });\n'
    "      }\n"
    "    } finally {\n"
    "      await browser.close();\n"
    "    }\n"
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("harness", type=Path)
    harness = parser.parse_args().harness
    content = harness.read_text()
    if content.count(OLD) != 1:
        raise SystemExit(
            "pinned companion harness changed; review and update "
            "this compatibility patch"
        )
    harness.write_text(content.replace(OLD, NEW))
    print(f"Added an ephemeral browser selector to {harness}.")


if __name__ == "__main__":
    main()
