import asyncio
from datetime import datetime
from typing import List, Dict

from playwright.async_api import async_playwright, Page
from loguru import logger

from parsers.base_parser import BaseParser
from config import settings


class HHPlaywrightParser(BaseParser):
    """Парсер HH.ru через Playwright (браузер)"""

    SOURCE_NAME = "hh"

    SEARCH_URLS = [
        "https://hh.ru/search/vacancy?text=SEO+продвижение&area=113&order_by=publication_time",
        "https://hh.ru/search/vacancy?text=контекстная+реклама&area=113&order_by=publication_time",
        "https://hh.ru/search/vacancy?text=разработка+сайта&area=113&order_by=publication_time",
    ]

    MAX_PAGES_PER_QUERY = 1  # Ограничение страниц (только первая страница)

    async def parse_vacancy_card(self, card) -> Dict:
        """Извлечь данные из карточки вакансии"""
        try:
            # Заголовок и ссылка
            title_elem = await card.query_selector("[data-qa='serp-item__title']")
            if not title_elem:
                title_elem = await card.query_selector(".vacancy-serp-item__title")

            title = await title_elem.inner_text() if title_elem else "Без названия"
            url = await title_elem.get_attribute("href") if title_elem else ""

            if url and not url.startswith("http"):
                url = f"https://hh.ru{url}"

            # Извлечь ID из URL
            external_id = ""
            if url and "/vacancy/" in url:
                external_id = url.split("/vacancy/")[1].split("?")[0]

            if not external_id:
                external_id = f"hh_{hash(url)}"

            # Описание (snippet)
            desc_elem = await card.query_selector("[data-qa='vacancy-serp__vacancy_snippet_responsibility']")
            if not desc_elem:
                desc_elem = await card.query_selector(".vacancy-serp-item__snippet")

            description = await desc_elem.inner_text() if desc_elem else ""
            description = description[:500]

            # Зарплата
            salary_elem = await card.query_selector("[data-qa='vacancy-serp__vacancy-compensation']")
            if not salary_elem:
                salary_elem = await card.query_selector(".vacancy-serp-item__compensation")

            budget = await salary_elem.inner_text() if salary_elem else None

            # Город
            city_elem = await card.query_selector("[data-qa='vacancy-serp__vacancy-address']")
            if not city_elem:
                city_elem = await card.query_selector(".vacancy-serp-item__meta-info")

            city = await city_elem.inner_text() if city_elem else None
            if city:
                city = city.split(",")[0].strip()  # Только город

            # Дата публикации
            date_elem = await card.query_selector("[data-qa='vacancy-serp__vacancy-date']")
            if not date_elem:
                date_elem = await card.query_selector(".vacancy-serp-item__publication-date")

            date_text = await date_elem.inner_text() if date_elem else None
            published_at = self.parse_relative_date(date_text) if date_text else datetime.utcnow()

            return {
                "source": self.SOURCE_NAME,
                "external_id": external_id,
                "title": title.strip(),
                "description": description.strip(),
                "budget": budget.strip() if budget else None,
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
            elif "день" in date_text or "дня" in date_text or "дней" in date_text:
                days = int(date_text.split()[0]) if date_text.split()[0].isdigit() else 0
                return now - timedelta(days=days)
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
            # Дождаться загрузки карточек
            await page.wait_for_selector("[data-qa='vacancy-serp__vacancy']", timeout=10000)

            # Получить все карточки
            cards = await page.query_selector_all("[data-qa='vacancy-serp__vacancy']")
            self.logger.debug(f"Найдено карточек: {len(cards)}")

            leads = []
            for card in cards:
                lead_data = await self.parse_vacancy_card(card)
                if lead_data:
                    leads.append(lead_data)

            return leads

        except Exception as e:
            self.logger.error(f"Ошибка парсинга страницы: {e}")
            return []

    async def parse_search_url(self, search_url: str, page: Page) -> List[Dict]:
        """Парсить один поисковый запрос"""
        self.logger.info(f"Парсинг URL: {search_url}")
        all_leads = []

        try:
            # Парсить страницы
            for page_num in range(self.MAX_PAGES_PER_QUERY):
                try:
                    # Формировать URL с пагинацией
                    if page_num == 0:
                        url = search_url
                    else:
                        url = f"{search_url}&page={page_num}"

                    self.logger.debug(f"Переход на страницу {page_num}: {url}")

                    # Перейти на страницу
                    response = await page.goto(url, wait_until="networkidle", timeout=30000)

                    # Проверить статус
                    if response and response.status != 200:
                        self.logger.warning(f"HTTP {response.status} для {url}")
                        break

                    # Имитация человеческого поведения
                    await asyncio.sleep(2)

                    # Случайный скролл
                    for _ in range(3):
                        await page.evaluate(f"window.scrollBy(0, {300 + _ * 200})")
                        await asyncio.sleep(1)

                    # Проверить на captcha
                    page_content = await page.content()
                    if "captcha" in page_content.lower():
                        self.logger.warning(f"Обнаружена captcha на {url}")
                        break

                    # Парсить страницу
                    leads = await self.parse_page(page)
                    all_leads.extend(leads)
                    self.logger.info(f"Страница {page_num}: собрано {len(leads)} лидов")

                    # Задержка между страницами
                    await self.random_delay(5, 10)

                except Exception as e:
                    self.logger.error(f"Ошибка на странице {page_num}: {e}")
                    break

        except Exception as e:
            self.logger.error(f"Ошибка парсинга URL '{search_url}': {e}")

        # Задержка между запросами (увеличена для избежания captcha)
        await self.random_delay(15, 25)

        return all_leads

    async def parse(self) -> List[Dict]:
        """Основной метод парсинга"""
        all_leads = []

        try:
            async with async_playwright() as p:
                # Настройки запуска браузера
                launch_options = {"headless": True}

                # Добавить прокси если настроен
                if settings.proxy_server:
                    launch_options["proxy"] = {"server": settings.proxy_server}

                # Запустить браузер один раз для всех запросов
                browser = await p.chromium.launch(**launch_options)

                # Создать контекст с User-Agent
                context = await browser.new_context(
                    user_agent=self.get_random_headers()["User-Agent"],
                    viewport={"width": 1920, "height": 1080},
                )

                page = await context.new_page()

                # Парсить все поисковые запросы в одном браузере
                for search_url in self.SEARCH_URLS:
                    try:
                        leads = await self.parse_search_url(search_url, page)
                        all_leads.extend(leads)
                    except Exception as e:
                        self.logger.error(f"Ошибка URL '{search_url}': {e}")
                        continue

                await browser.close()

        except Exception as e:
            self.logger.error(f"Ошибка запуска браузера: {e}")

        # Дедупликация по external_id
        unique_leads = []
        seen = set()
        for lead in all_leads:
            if lead["external_id"] not in seen:
                seen.add(lead["external_id"])
                unique_leads.append(lead)

        self.logger.info(f"Всего уникальных лидов: {len(unique_leads)}")
        return unique_leads


# Для тестирования
async def main():
    parser = HHPlaywrightParser()
    saved, duplicates = await parser.run()
    print(f"Сохранено: {saved}, дублей: {duplicates}")


if __name__ == "__main__":
    asyncio.run(main())
