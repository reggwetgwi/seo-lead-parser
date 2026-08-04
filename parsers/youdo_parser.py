import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import List, Dict

from playwright.async_api import async_playwright

from parsers.base_parser import BaseParser


class YouDoParser(BaseParser):
    """Парсер YouDo.com - заказы на услуги (требует авторизацию)"""

    SOURCE_NAME = "youdo"

    # YouDo требует авторизацию для просмотра задач
    BASE_URL = "https://youdo.com/tasks/"
    COOKIES_FILE = Path("cookies/youdo.json")

    # Ключевые слова для фильтрации маркетинговых задач
    KEYWORDS = [
        "seo", "сео", "продвижение", "оптимизация",
        "контекст", "реклама", "яндекс директ", "google ads",
        "сайт", "лендинг", "интернет-магазин", "веб-разработка",
        "маркетинг", "smm", "соцсети", "таргет",
        "аналитика", "метрика", "google analytics"
    ]

    async def load_cookies(self, context):
        """Загрузить cookies из файла"""
        if self.COOKIES_FILE.exists():
            with open(self.COOKIES_FILE, "r", encoding="utf-8") as f:
                cookies = json.load(f)
                await context.add_cookies(cookies)
                self.logger.info("Cookies загружены")
                return True
        else:
            self.logger.warning(f"Файл cookies не найден: {self.COOKIES_FILE}")
            self.logger.warning("Запусти: python save_cookies.py")
            return False

    async def parse(self) -> List[Dict]:
        """Основной метод парсинга"""
        all_leads = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=self.get_random_headers()["User-Agent"]
            )

            # Загрузить cookies для авторизации
            cookies_loaded = await self.load_cookies(context)
            if not cookies_loaded:
                self.logger.error("Не удалось загрузить cookies. Парсинг невозможен.")
                await browser.close()
                return all_leads

            page = await context.new_page()

            try:
                self.logger.info(f"Парсинг страницы: {self.BASE_URL}")
                leads = await self.parse_page(page)
                all_leads.extend(leads)
            except Exception as e:
                self.logger.error(f"Ошибка парсинга YouDo: {e}")

            await browser.close()

        self.logger.info(f"Всего собрано лидов: {len(all_leads)}")
        return all_leads

    async def parse_page(self, page) -> List[Dict]:
        """Парсить страницу с задачами"""
        leads = []

        try:
            await page.goto(self.BASE_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)

            # Прокрутить страницу для загрузки контента
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2)

            # Найти все ссылки на задачи
            task_links = await page.query_selector_all('a[href*="/tasks/"]')

            if not task_links:
                self.logger.warning(f"Задачи не найдены на {self.BASE_URL}")
                return leads

            self.logger.info(f"Найдено {len(task_links)} ссылок на задачи")

            # Собрать уникальные URL задач (только с ID, не /tasks/new и т.д.)
            task_urls = set()
            for link in task_links:
                href = await link.get_attribute("href")
                if href and "/tasks/" in href:
                    # Пропускаем служебные страницы
                    if "/tasks/new" in href or "/tasks/category" in href:
                        continue
                    # Проверяем что есть ID (цифры в URL)
                    if not any(char.isdigit() for char in href):
                        continue
                    if not href.startswith("http"):
                        href = f"https://youdo.com{href}"
                    task_urls.add(href)

            self.logger.info(f"Уникальных задач: {len(task_urls)}")

            # Парсить каждую задачу
            for task_url in list(task_urls)[:30]:  # Ограничение 30 задач
                try:
                    lead_data = await self.parse_task_page(page, task_url)
                    if lead_data and self.is_relevant(lead_data):
                        leads.append(lead_data)
                    await self.random_delay(2, 4)
                except Exception as e:
                    self.logger.debug(f"Ошибка парсинга задачи {task_url}: {e}")
                    continue

        except Exception as e:
            self.logger.error(f"Ошибка загрузки страницы: {e}")

        return leads

    async def parse_task_page(self, page, task_url: str) -> Dict:
        """Парсить страницу отдельной задачи"""
        try:
            await page.goto(task_url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(2)

            # Заголовок
            title_elem = await page.query_selector("h1, .task-title, [data-testid='task-title']")
            title = await title_elem.inner_text() if title_elem else "Без названия"
            title = title.strip()

            # Описание
            desc_elem = await page.query_selector(".task-description, .description, [data-testid='task-description']")
            description = await desc_elem.inner_text() if desc_elem else ""
            description = description.strip()

            # Извлечь ID из URL
            external_id = ""
            match = re.search(r'/tasks/(\d+)', task_url)
            if match:
                external_id = match.group(1)

            # Бюджет
            budget_elem = await page.query_selector(".task-price, .price, [data-testid='task-price']")
            budget = await budget_elem.inner_text() if budget_elem else "не указан"
            budget = budget.strip()

            # Город
            city_elem = await page.query_selector(".task-location, .location, [data-testid='task-location']")
            city = await city_elem.inner_text() if city_elem else None
            if city:
                city = city.strip()

            # Дата
            date_elem = await page.query_selector(".task-date, .date, time, [data-testid='task-date']")
            date_text = await date_elem.inner_text() if date_elem else ""
            published_at = self.parse_date(date_text)

            # Валидация
            if not title or title == "Без названия":
                return None
            if not external_id:
                return None

            return {
                "source": self.SOURCE_NAME,
                "external_id": external_id,
                "title": title[:500],
                "description": description[:5000],
                "budget": budget,
                "url": task_url,
                "city": city,
                "published_at": published_at,
            }

        except Exception as e:
            self.logger.debug(f"Ошибка парсинга страницы задачи: {e}")
            return None

    def is_relevant(self, lead_data: Dict) -> bool:
        """Проверить релевантность задачи по ключевым словам"""
        text = f"{lead_data['title']} {lead_data['description']}".lower()

        for keyword in self.KEYWORDS:
            if keyword in text:
                return True

        return False

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

        # "N часов назад", "N минут назад"
        hours_match = re.search(r'(\d+)\s*час', date_text)
        if hours_match:
            hours = int(hours_match.group(1))
            return datetime.now() - timedelta(hours=hours)

        minutes_match = re.search(r'(\d+)\s*минут', date_text)
        if minutes_match:
            minutes = int(minutes_match.group(1))
            return datetime.now() - timedelta(minutes=minutes)

        return datetime.now()


# Для тестирования
async def main():
    parser = YouDoParser()
    saved, duplicates = await parser.run()
    print(f"Сохранено: {saved}, дублей: {duplicates}")


if __name__ == "__main__":
    from datetime import timedelta
    asyncio.run(main())
