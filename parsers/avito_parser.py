import asyncio
from datetime import datetime
from typing import List, Dict
import random

from playwright.async_api import async_playwright, Page
from loguru import logger

from parsers.base_parser import BaseParser
from config import settings


class AvitoParser(BaseParser):
    """Парсер Avito.ru через Playwright"""

    SOURCE_NAME = "avito"

    SEARCH_URLS = [
        "https://www.avito.ru/rossiya/uslugi/kompyuternye_uslugi-ASgBAgICAUSSA8YQ?q=seo+продвижение",
        "https://www.avito.ru/rossiya/uslugi/kompyuternye_uslugi-ASgBAgICAUSSA8YQ?q=создание+сайта",
        "https://www.avito.ru/rossiya/uslugi/kompyuternye_uslugi-ASgBAgICAUSSA8YQ?q=контекстная+реклама",
        "https://www.avito.ru/rossiya/uslugi/kompyuternye_uslugi-ASgBAgICAUSSA8YQ?q=интернет+маркетинг",
        "https://www.avito.ru/rossiya/uslugi/kompyuternye_uslugi-ASgBAgICAUSSA8YQ?q=продвижение+сайта",
    ]

    async def parse_ad_card(self, card) -> Dict:
        """Извлечь данные из карточки объявления"""
        try:
            # Заголовок и ссылка
            title_elem = await card.query_selector("[itemprop='name']")
            title = await title_elem.inner_text() if title_elem else "Без названия"

            link_elem = await card.query_selector("a[itemprop='url']")
            url = await link_elem.get_attribute("href") if link_elem else ""
            if url and not url.startswith("http"):
                url = f"https://www.avito.ru{url}"

            # Извлечь ID из URL (последний сегмент пути)
            external_id = ""
            if url:
                parts = url.rstrip("/").split("/")
                for part in reversed(parts):
                    if part and part[0].isdigit():
                        external_id = part.split("_")[-1]
                        break

            if not external_id:
                external_id = f"avito_{hash(url)}"

            # Описание
            desc_elem = await card.query_selector("[class*='description']")
            description = await desc_elem.inner_text() if desc_elem else ""
            description = description[:500]  # Ограничение

            # Цена
            price_elem = await card.query_selector("[itemprop='price']")
            price = await price_elem.get_attribute("content") if price_elem else None

            if price:
                budget = f"{price} ₽"
            else:
                budget = None

            # Город
            city_elem = await card.query_selector("[class*='geo-root']")
            city = await city_elem.inner_text() if city_elem else None

            # Дата публикации (относительная)
            date_elem = await card.query_selector("[data-marker='item-date']")
            date_text = await date_elem.inner_text() if date_elem else None
            published_at = self.parse_relative_date(date_text) if date_text else datetime.utcnow()

            return {
                "source": self.SOURCE_NAME,
                "external_id": external_id,
                "title": title.strip(),
                "description": description.strip(),
                "budget": budget,
                "url": url,
                "city": city.strip() if city else None,
                "published_at": published_at,
            }

        except Exception as e:
            self.logger.error(f"Ошибка парсинга карточки: {e}")
            return None

    def parse_relative_date(self, date_text: str) -> datetime:
        """Парсинг относительной даты"""
        try:
            from datetime import timedelta
            now = datetime.utcnow()

            date_text = date_text.lower().strip()

            if "минут" in date_text:
                minutes = int(date_text.split()[0]) if date_text.split()[0].isdigit() else 0
                return now - timedelta(minutes=minutes)
            elif "час" in date_text:
                hours = int(date_text.split()[0]) if date_text.split()[0].isdigit() else 0
                return now - timedelta(hours=hours)
            elif "вчера" in date_text:
                return now - timedelta(days=1)
            elif "сегодня" in date_text:
                return now
            else:
                return now

        except:
            return datetime.utcnow()

    async def parse_page(self, page: Page) -> List[Dict]:
        """Парсить одну страницу"""
        try:
            # Случайный скролл для имитации поведения
            scroll_amount = random.randint(200, 500)
            await page.evaluate(f"window.scrollBy(0, {scroll_amount})")
            await asyncio.sleep(random.uniform(2, 4))

            # Дождаться загрузки карточек
            await page.wait_for_selector("[data-marker='item']", timeout=15000)

            # Получить все карточки
            cards = await page.query_selector_all("[data-marker='item']")
            self.logger.debug(f"Найдено карточек: {len(cards)}")

            leads = []
            for card in cards:
                lead_data = await self.parse_ad_card(card)
                if lead_data:
                    leads.append(lead_data)

            return leads

        except Exception as e:
            self.logger.error(f"Ошибка парсинга страницы: {e}")
            return []

    async def parse_search_url(self, search_url: str) -> List[Dict]:
        """Парсить один поисковый URL (только первая страница)"""
        self.logger.info(f"Парсинг URL: {search_url}")
        leads = []

        try:
            async with async_playwright() as p:
                # Настройки запуска браузера
                launch_options = {"headless": True}

                # Добавить прокси при запуске браузера (не в контексте!)
                if settings.proxy_server:
                    launch_options["proxy"] = {"server": settings.proxy_server}
                    self.logger.info(f"Используется прокси: {settings.proxy_server}")

                # Запустить браузер с прокси
                browser = await p.chromium.launch(**launch_options)

                # Создать контекст с User-Agent
                context = await browser.new_context(
                    user_agent=self.get_random_headers()["User-Agent"],
                    viewport={"width": 1920, "height": 1080},
                )

                page = await context.new_page()

                try:
                    self.logger.debug(f"Переход на: {search_url}")

                    # Перейти на страницу
                    response = await page.goto(search_url, wait_until="networkidle", timeout=30000)

                    # Проверить статус
                    if response and response.status != 200:
                        self.logger.warning(f"HTTP {response.status} для {search_url}")
                    else:
                        # Имитация человеческого поведения
                        await asyncio.sleep(random.uniform(2, 4))

                        # Случайный скролл
                        for _ in range(random.randint(2, 4)):
                            await page.evaluate(f"window.scrollBy(0, {random.randint(200, 500)})")
                            await asyncio.sleep(random.uniform(0.5, 1.5))

                        # Проверить на блокировку/заглушку
                        page_content = await page.content()
                        if "captcha" in page_content.lower() or "доступ ограничен" in page_content.lower():
                            self.logger.warning(f"Обнаружена блокировка на {search_url}")
                        else:
                            # Парсить страницу
                            leads = await self.parse_page(page)
                            self.logger.info(f"Собрано {len(leads)} лидов")

                except Exception as e:
                    self.logger.error(f"Ошибка обработки URL {search_url}: {e}")

                await browser.close()

        except Exception as e:
            self.logger.error(f"Ошибка парсинга URL '{search_url}': {e}")

        # Задержка между URL (увеличена для избежания блокировки)
        await self.random_delay(20, 30)

        return leads

    async def parse(self) -> List[Dict]:
        """Основной метод парсинга"""
        all_leads = []

        for search_url in self.SEARCH_URLS:
            try:
                leads = await self.parse_search_url(search_url)
                all_leads.extend(leads)
            except Exception as e:
                self.logger.error(f"Ошибка URL '{search_url}': {e}")
                continue

        self.logger.info(f"Всего собрано лидов: {len(all_leads)}")
        return all_leads


# Для тестирования
async def main():
    parser = AvitoParser()
    saved, duplicates = await parser.run()
    print(f"Сохранено: {saved}, дублей: {duplicates}")


if __name__ == "__main__":
    asyncio.run(main())
