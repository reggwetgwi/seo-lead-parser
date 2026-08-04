import asyncio
from datetime import datetime
from typing import List, Dict

from playwright.async_api import async_playwright, Page
from loguru import logger

from parsers.base_parser import BaseParser
from config import settings


class KworkParser(BaseParser):
    """Парсер Kwork.ru через Playwright"""

    SOURCE_NAME = "kwork"

    CATEGORIES = [
        ("Сайты и интернет", "https://kwork.ru/projects?c=11"),
        ("Реклама и маркетинг", "https://kwork.ru/projects?c=22"),
        ("SEO", "https://kwork.ru/projects?c=36"),
    ]

    MAX_PAGES_PER_CATEGORY = 5

    async def parse_want_card(self, card) -> Dict:
        """Извлечь данные из карточки хотелки"""
        try:
            # Заголовок и ссылка
            title_elem = await card.query_selector(".wants-card__header-title a")
            title = await title_elem.inner_text() if title_elem else "Без названия"
            url = await title_elem.get_attribute("href") if title_elem else ""
            if url and not url.startswith("http"):
                url = f"https://kwork.ru{url}"

            # Извлечь ID из URL
            external_id = url.split("/")[-1] if url else ""

            # Описание
            desc_elem = await card.query_selector(".wants-card__description-text .breakwords")
            description = await desc_elem.inner_text() if desc_elem else ""
            description = description.replace("Показать полностью", "").strip()
            description = description[:500]  # Ограничение

            # Бюджет (желаемый)
            budget_elem = await card.query_selector(".wants-card__price")
            budget = await budget_elem.inner_text() if budget_elem else None
            if budget:
                budget = budget.replace("Желаемый бюджет: до", "").strip()

            # Дата публикации - ищем в информерах
            date_elem = await card.query_selector(".want-card__informers")
            date_text = await date_elem.inner_text() if date_elem else None

            # Парсинг относительной даты
            published_at = self.parse_relative_date(date_text) if date_text else datetime.utcnow()

            return {
                "source": self.SOURCE_NAME,
                "external_id": external_id or f"kwork_{hash(url)}",
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
        """Парсинг относительной даты (например, '2 часа назад')"""
        try:
            from datetime import timedelta
            now = datetime.utcnow()

            date_text = date_text.lower().strip()

            if "минут" in date_text:
                minutes = int(date_text.split()[0])
                return now - timedelta(minutes=minutes)
            elif "час" in date_text:
                hours = int(date_text.split()[0])
                return now - timedelta(hours=hours)
            elif "день" in date_text or "дня" in date_text or "дней" in date_text:
                days = int(date_text.split()[0])
                return now - timedelta(days=days)
            elif "неделю" in date_text or "недели" in date_text or "недель" in date_text:
                weeks = int(date_text.split()[0])
                return now - timedelta(weeks=weeks)
            else:
                return now

        except:
            return datetime.utcnow()

    async def parse_page(self, page: Page) -> List[Dict]:
        """Парсить одну страницу"""
        try:
            # Имитация скролла
            await page.evaluate("window.scrollBy(0, 300)")
            await asyncio.sleep(1)

            # Дождаться загрузки карточек
            await page.wait_for_selector(".want-card", timeout=10000)

            # Получить все карточки
            cards = await page.query_selector_all(".want-card")
            self.logger.debug(f"Найдено карточек: {len(cards)}")

            leads = []
            for card in cards:
                lead_data = await self.parse_want_card(card)
                if lead_data:
                    leads.append(lead_data)

            return leads

        except Exception as e:
            self.logger.error(f"Ошибка парсинга страницы: {e}")
            return []

    async def parse_category(self, category_name: str, category_url: str) -> List[Dict]:
        """Парсить одну категорию"""
        self.logger.info(f"Парсинг категории: {category_name}")
        all_leads = []

        try:
            async with async_playwright() as p:
                # Настройки запуска браузера
                launch_options = {"headless": True}

                # Добавить прокси при запуске браузера
                if settings.proxy_server:
                    launch_options["proxy"] = {"server": settings.proxy_server}

                # Запустить браузер с прокси
                browser = await p.chromium.launch(**launch_options)

                # Создать контекст с User-Agent
                context = await browser.new_context(
                    user_agent=self.get_random_headers()["User-Agent"],
                    viewport={"width": 1366, "height": 768},
                )

                page = await context.new_page()

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

                        # Проверить статус
                        if response and response.status != 200:
                            self.logger.warning(f"HTTP {response.status} для {url}")
                            break

                        # Парсить страницу
                        leads = await self.parse_page(page)
                        all_leads.extend(leads)
                        self.logger.info(f"Страница {page_num}: собрано {len(leads)} лидов")

                        # Проверить есть ли кнопка "Следующая страница"
                        next_button = await page.query_selector("a.pagination__next:not(.disabled)")
                        if not next_button:
                            self.logger.debug("Достигнута последняя страница")
                            break

                        # Задержка между страницами
                        await self.random_delay(5, 10)

                    except Exception as e:
                        self.logger.error(f"Ошибка на странице {page_num}: {e}")
                        break

                await browser.close()

        except Exception as e:
            self.logger.error(f"Ошибка парсинга категории '{category_name}': {e}")

        # Задержка между категориями
        await self.random_delay(15, 25)

        self.logger.info(f"Категория '{category_name}': собрано {len(all_leads)} лидов")
        return all_leads

    async def parse(self) -> List[Dict]:
        """Основной метод парсинга"""
        all_leads = []

        for category_name, category_url in self.CATEGORIES:
            try:
                leads = await self.parse_category(category_name, category_url)
                all_leads.extend(leads)
            except Exception as e:
                self.logger.error(f"Ошибка категории '{category_name}': {e}")
                continue

        self.logger.info(f"Всего собрано лидов: {len(all_leads)}")
        return all_leads


# Для тестирования
async def main():
    parser = KworkParser()
    saved, duplicates = await parser.run()
    print(f"Сохранено: {saved}, дублей: {duplicates}")


if __name__ == "__main__":
    asyncio.run(main())
