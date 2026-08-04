import asyncio
from datetime import datetime
from typing import List, Dict

from playwright.async_api import async_playwright, Page
from loguru import logger

from parsers.base_parser import BaseParser
from config import settings


class FLParser(BaseParser):
    """Парсер FL.ru через Playwright"""

    SOURCE_NAME = "fl"

    CATEGORIES = [
        ("SEO и реклама", "https://www.fl.ru/projects/?kind=1&subcategory=17"),
        ("Создание сайтов", "https://www.fl.ru/projects/?kind=1&subcategory=38"),
        ("Дизайн", "https://www.fl.ru/projects/?kind=1&subcategory=3"),
    ]

    MAX_PAGES_PER_CATEGORY = 3

    async def parse_project_card(self, card) -> Dict:
        """Извлечь данные из карточки проекта"""
        try:
            # Заголовок
            title_elem = await card.query_selector(".b-post__title a")
            title = await title_elem.inner_text() if title_elem else "Без названия"
            url = await title_elem.get_attribute("href") if title_elem else ""
            if url and not url.startswith("http"):
                url = f"https://www.fl.ru{url}"

            # Извлечь ID из URL
            external_id = ""
            if url and "/projects/" in url:
                external_id = url.split("/projects/")[1].split("/")[0]

            if not external_id:
                external_id = f"fl_{hash(url)}"

            # Описание
            desc_elem = await card.query_selector(".b-post__txt")
            description = await desc_elem.inner_text() if desc_elem else ""
            description = description[:500]  # Ограничение

            # Бюджет
            budget_elem = await card.query_selector(".b-post__price")
            budget = await budget_elem.inner_text() if budget_elem else None

            # Дата публикации
            date_elem = await card.query_selector(".text-gray-opacity-4")
            date_text = await date_elem.inner_text() if date_elem else None

            # Парсинг относительной даты
            published_at = self.parse_relative_date(date_text) if date_text else datetime.utcnow()

            return {
                "source": self.SOURCE_NAME,
                "external_id": external_id,
                "title": title.strip(),
                "description": description.strip(),
                "budget": budget.strip() if budget else None,
                "url": url,
                "city": None,
                "published_at": published_at,
            }

        except Exception as e:
            self.logger.error(f"Ошибка парсинга карточки: {e}")
            return None

    def parse_relative_date(self, date_text: str) -> datetime:
        """Парсинг относительной даты (например, '3 часа 19 минут назад')"""
        try:
            from datetime import timedelta
            now = datetime.utcnow()

            date_text = date_text.lower().strip()

            if "минут" in date_text:
                # Извлечь число минут
                parts = date_text.split()
                for i, part in enumerate(parts):
                    if part.isdigit() and i + 1 < len(parts) and "минут" in parts[i + 1]:
                        minutes = int(part)
                        return now - timedelta(minutes=minutes)

            if "час" in date_text:
                # Извлечь число часов
                parts = date_text.split()
                for i, part in enumerate(parts):
                    if part.isdigit() and i + 1 < len(parts) and "час" in parts[i + 1]:
                        hours = int(part)
                        return now - timedelta(hours=hours)

            if "день" in date_text or "дня" in date_text or "дней" in date_text:
                parts = date_text.split()
                for i, part in enumerate(parts):
                    if part.isdigit():
                        days = int(part)
                        return now - timedelta(days=days)

            if "вчера" in date_text:
                return now - timedelta(days=1)

            if "сегодня" in date_text:
                return now

            return now

        except:
            return datetime.utcnow()

    async def parse_page(self, page: Page) -> List[Dict]:
        """Парсить одну страницу"""
        try:
            # Дождаться загрузки карточек
            await page.wait_for_selector(".b-post", timeout=10000)

            # Получить все карточки
            cards = await page.query_selector_all(".b-post")
            self.logger.debug(f"Найдено карточек: {len(cards)}")

            leads = []
            for card in cards:
                lead_data = await self.parse_project_card(card)
                if lead_data:
                    leads.append(lead_data)

            return leads

        except Exception as e:
            self.logger.error(f"Ошибка парсинга страницы: {e}")
            return []

    async def parse_category(self, category_name: str, category_url: str, page: Page) -> List[Dict]:
        """Парсить одну категорию"""
        self.logger.info(f"Парсинг категории: {category_name}")
        all_leads = []

        try:
            # Парсить страницы
            for page_num in range(1, self.MAX_PAGES_PER_CATEGORY + 1):
                try:
                    # Формировать URL с пагинацией
                    if page_num == 1:
                        url = category_url
                    else:
                        separator = "&" if "?" in category_url else "?"
                        url = f"{category_url}{separator}page={page_num}"

                    self.logger.debug(f"Переход на страницу {page_num}: {url}")

                    # Перейти на страницу
                    response = await page.goto(url, wait_until="networkidle", timeout=30000)

                    # Проверить на captcha или блокировку
                    if response and response.status != 200:
                        self.logger.warning(f"HTTP {response.status} для {url}")
                        break

                    # Проверить наличие captcha
                    captcha = await page.query_selector("form[action*='captcha']")
                    if captcha:
                        self.logger.warning(f"Обнаружена captcha на {url}, пропускаем")
                        break

                    # Парсить страницу
                    leads = await self.parse_page(page)
                    all_leads.extend(leads)
                    self.logger.info(f"Страница {page_num}: собрано {len(leads)} лидов")

                    # Задержка между страницами
                    await self.random_delay(4, 8)

                except Exception as e:
                    self.logger.error(f"Ошибка на странице {page_num}: {e}")
                    break

        except Exception as e:
            self.logger.error(f"Ошибка парсинга категории '{category_name}': {e}")

        # Задержка между категориями
        await self.random_delay(10, 20)

        self.logger.info(f"Категория '{category_name}': собрано {len(all_leads)} лидов")
        return all_leads

    async def parse(self) -> List[Dict]:
        """Основной метод парсинга"""
        all_leads = []

        try:
            async with async_playwright() as p:
                # Настройки запуска браузера
                launch_options = {"headless": True}

                # Добавить прокси при запуске браузера
                if settings.proxy_server:
                    launch_options["proxy"] = {"server": settings.proxy_server}

                # Запустить браузер один раз для всех категорий
                browser = await p.chromium.launch(**launch_options)

                # Создать контекст с User-Agent
                context = await browser.new_context(
                    user_agent=self.get_random_headers()["User-Agent"],
                    viewport={"width": 1920, "height": 1080},
                )

                page = await context.new_page()

                # Парсить все категории в одном браузере
                for category_name, category_url in self.CATEGORIES:
                    try:
                        leads = await self.parse_category(category_name, category_url, page)
                        all_leads.extend(leads)
                    except Exception as e:
                        self.logger.error(f"Ошибка категории '{category_name}': {e}")
                        continue

                await browser.close()

        except Exception as e:
            self.logger.error(f"Ошибка запуска браузера: {e}")

        self.logger.info(f"Всего собрано лидов: {len(all_leads)}")
        return all_leads


# Для тестирования
async def main():
    parser = FLParser()
    saved, duplicates = await parser.run()
    print(f"Сохранено: {saved}, дублей: {duplicates}")


if __name__ == "__main__":
    asyncio.run(main())
