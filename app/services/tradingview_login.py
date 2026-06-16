from __future__ import annotations

import sys
from pathlib import Path


PROFILE_DIR = Path("data/tradingview_profile").resolve()


def main() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        print("Playwright is not installed.")
        print("Install it only if TradingView manual browser login is needed:")
        print("python -m pip install playwright")
        print("python -m playwright install chromium")
        sys.exit(1)

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Opening TradingView with persistent profile: {PROFILE_DIR}")
    print("Login manually in the browser window. Do not enter your password in this script or .env.")
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1366, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.tradingview.com/", wait_until="domcontentloaded")
        input("After you finish manual login, press Enter here to save the browser profile and close...")
        context.close()
    print("TradingView browser profile saved. No username or password was stored by this script.")


if __name__ == "__main__":
    main()
