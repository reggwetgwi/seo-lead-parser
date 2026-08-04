"""
Скрипт для сохранения cookies после авторизации на YouDo и Workspace
"""
import asyncio
import json
import os
from pathlib import Path

from playwright.async_api import async_playwright


async def save_cookies():
    # Создать директорию для cookies
    cookies_dir = Path("cookies")
    cookies_dir.mkdir(exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})

        # === YouDo ===
        print("\n=== YouDo ===")
        print("1. Сейчас откроется страница авторизации YouDo")
        print("2. Авторизуйся вручную")
        print("3. После успешной авторизации нажми Enter в консоли")

        page = await context.new_page()
        await page.goto("https://youdo.com/login/")

        input("\nНажми Enter после авторизации на YouDo...")

        # Сохранить cookies
        cookies = await context.cookies()
        with open(cookies_dir / "youdo.json", "w", encoding="utf-8") as f:
            json.dump(cookies, f, indent=2, ensure_ascii=False)

        print(f"✓ Cookies YouDo сохранены: {cookies_dir / 'youdo.json'}")

        await page.close()

        # === Workspace ===
        print("\n=== Workspace ===")
        print("1. Сейчас откроется страница авторизации Workspace")
        print("2. Авторизуйся вручную")
        print("3. После успешной авторизации нажми Enter в консоли")

        # Очистить cookies для нового сайта
        await context.clear_cookies()

        page = await context.new_page()
        await page.goto("https://workspace.ru/login/")

        input("\nНажми Enter после авторизации на Workspace...")

        # Сохранить cookies
        cookies = await context.cookies()
        with open(cookies_dir / "workspace.json", "w", encoding="utf-8") as f:
            json.dump(cookies, f, indent=2, ensure_ascii=False)

        print(f"✓ Cookies Workspace сохранены: {cookies_dir / 'workspace.json'}")

        await browser.close()

    print("\n=== Готово! ===")
    print("Cookies сохранены. Теперь парсеры смогут работать с авторизацией.")
    print("\nСледующие шаги:")
    print("1. Включи парсеры в scheduler.py (раскомментируй YouDoParser и WorkspaceParser)")
    print("2. Запусти парсер: python -m parsers.youdo_parser")


if __name__ == "__main__":
    asyncio.run(save_cookies())
