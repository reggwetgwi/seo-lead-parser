import asyncio
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict

from playwright.async_api import async_playwright

from parsers.base_parser import BaseParser


class WorkspaceParser(BaseParser):
    """Парсер Workspace.ru - фриланс биржа (тендеры доступны публично)"""

    SOURCE_NAME = "workspace"

    COOKIES_FILE = Path("cookies/workspace.json")

    # Тендеры с фильтрами по категориям
    CATEGORIES = [
        ("Поисковая оптимизация", "https://workspace.ru/tenders/?service=2"),
        ("Реклама", "https://workspace.ru/tenders/?service=3"),
        ("Разработка сайтов", "https://workspace.ru/tenders/?service=1"),
        ("SMM и PR", "https://workspace.ru/tenders/?service=5"),
        ("Маркетинг", "https://workspace.ru/tenders/?service=6"),
    ]

    async def load_cookies(self, context):
        """Загрузить cookies из файла (опционально, тендеры доступны публично)"""
        if self.COOKIES_FILE.exists():
            with open(self.COOKIES_FILE, "r", encoding="utf-8") as f:
                cookies = json.load(f)
                await context.add_cookies(cookies)
                self.logger.info("Cookies загружены")
                return True
        else:
            self.logger.info("Cookies не найдены, работаем без авторизации")
            return False

    async def parse(self) -> List[Dict]:
        """Основной метод парсинга"""
        all_leads = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1366, "height": 768},
                user_agent=self.get_random_headers()["User-Agent"]
            )

            # Загрузить cookies (опционально)
            await self.load_cookies(context)

            page = await context.new_page()

            for category_name, category_url in self.CATEGORIES:
                try:
                    self.logger.info(f"Парсинг категории: {category_name}")
                    leads = await self.parse_category(page, category_url)
                    all_leads.extend(leads)

                    # Задержка между категориями
                    await self.random_delay(8, 12)
                except Exception as e:
                    self.logger.error(f"Ошибка парсинга категории {category_name}: {e}")
                    continue

            await browser.close()

        self.logger.info(f"Всего собрано лидов: {len(all_leads)}")
        return all_leads

    async def parse_category(self, page, category_url: str) -> List[Dict]:
        """Парсить одну категорию"""
        leads = []

        try:
            await page.goto(category_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)

            # Прокрутить для загрузки
            await page.evaluate("window.scrollTo(0, 800)")
            await asyncio.sleep(2)

            # Найти все ссылки на тендеры (с числовым ID в конце)
            # Формат: /tenders/slug-12345/
            all_links = await page.query_selector_all("a[href*='/tenders/']")

            # Фильтровать только ссылки на конкретные тендеры (содержат дефис и цифры)
            tender_links = []
            for link in all_links:
                href = await link.get_attribute("href")
                if href and re.search(r'/tenders/.+-\d+/', href):
                    tender_links.append(link)

            if not tender_links:
                self.logger.warning(f"Ссылки на тендеры не найдены на {category_url}")
                return leads

            self.logger.info(f"Найдено {len(tender_links)} ссылок на тендеры")

            # Обработать уникальные URL (убрать дубли)
            seen_urls = set()
            unique_links = []

            for link in tender_links:
                url = await link.get_attribute("href")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    unique_links.append(link)

            self.logger.info(f"Уникальных тендеров: {len(unique_links)}")

            for link in unique_links[:20]:  # Ограничение 20 на категорию
                try:
                    lead_data = await self.parse_link(link)
                    if lead_data:
                        leads.append(lead_data)
                except Exception as e:
                    self.logger.debug(f"Ошибка парсинга ссылки: {e}")
                    continue

        except Exception as e:
            self.logger.error(f"Ошибка загрузки категории: {e}")

        return leads

    async def parse_link(self, link) -> Dict:
        """Парсить тендер по ссылке"""
        try:
            url = await link.get_attribute("href")
            if not url:
                return None

            if not url.startswith("http"):
                url = f"https://workspace.ru{url}"

            # Заголовок из текста ссылки
            title = await link.inner_text()
            title = title.strip() if title else "Без названия"

            # Извлечь ID из URL
            external_id = ""
            match = re.search(r'/tenders/[^/]+-(\d+)/', url)
            if match:
                external_id = match.group(1)
            else:
                external_id = str(hash(url))[:10]

            # Для каталога тендеров детали обычно на странице тендера
            # Здесь собираем минимум данных
            return {
                "source": self.SOURCE_NAME,
                "external_id": external_id,
                "title": title[:500],
                "description": title,  # В каталоге описания нет, используем заголовок
                "budget": "не указан",  # Бюджет на странице тендера
                "url": url,
                "city": None,
                "published_at": datetime.now(),  # Точную дату можно получить только на странице тендера
            }

        except Exception as e:
            self.logger.debug(f"Ошибка парсинга ссылки: {e}")
            return None

    async def parse_card(self, card) -> Dict:
        """Парсить одну карточку тендера"""
        try:
            # Ссылка на тендер (приоритетный селектор)
            link_elem = await card.query_selector("a[href^='/tenders/']")

            if not link_elem:
                # Альтернативные селекторы
                link_elem = await card.query_selector("h3 a, .tender-title a, a")

            if not link_elem:
                return None

            url = await link_elem.get_attribute("href")
            if not url:
                return None

            if not url.startswith("http"):
                url = f"https://workspace.ru{url}"

            # Заголовок из ссылки
            title = await link_elem.inner_text()
            title = title.strip() if title else "Без названия"

            # Извлечь ID из URL
            external_id = ""
            match = re.search(r'/tenders/[^/]+-(\d+)/', url)
            if match:
                external_id = match.group(1)
            else:
                # Попробовать другой формат
                match = re.search(r'/tender/(\d+)', url)
                if match:
                    external_id = match.group(1)

            if not external_id:
                external_id = str(hash(url))[:10]

            # Бюджет (текст с числами)
            budget_elem = await card.query_selector(".tender-budget")
            budget = await budget_elem.inner_text() if budget_elem else "не указан"
            budget = budget.strip()

            # Описание (может отсутствовать в карточке каталога)
            desc_elem = await card.query_selector(".tender-description, .description")
            description = await desc_elem.inner_text() if desc_elem else title
            description = description.strip()

            # Дата публикации
            date_elem = await card.query_selector(".tender-dates")
            date_text = await date_elem.inner_text() if date_elem else ""
            published_at = self.parse_date(date_text)

            # Валидация
            if not title or title == "Без названия":
                return None

            return {
                "source": self.SOURCE_NAME,
                "external_id": external_id,
                "title": title[:500],
                "description": description[:5000] if description else title,
                "budget": budget,
                "url": url,
                "city": None,  # В каталоге тендеров город обычно не указан
                "published_at": published_at,
            }

        except Exception as e:
            self.logger.debug(f"Ошибка парсинга карточки: {e}")
            return None

    def parse_date(self, date_text: str) -> datetime:
        """Парсить дату из текста"""
        if not date_text:
            return datetime.now()

        date_text = date_text.lower().strip()

        # "сегодня", "вчера"
        if "сегодня" in date_text or "только что" in date_text:
            return datetime.now()
        if "вчера" in date_text:
            return datetime.now() - timedelta(days=1)

        # "N часов назад", "N дней назад"
        hours_match = re.search(r'(\d+)\s*час', date_text)
        if hours_match:
            hours = int(hours_match.group(1))
            return datetime.now() - timedelta(hours=hours)

        days_match = re.search(r'(\d+)\s*(день|дня|дней)', date_text)
        if days_match:
            days = int(days_match.group(1))
            return datetime.now() - timedelta(days=days)

        return datetime.now()


# Для тестирования
async def main():
    parser = WorkspaceParser()
    saved, duplicates = await parser.run()
    print(f"Сохранено: {saved}, дублей: {duplicates}")


if __name__ == "__main__":
    asyncio.run(main())
