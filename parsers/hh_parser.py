"""
HH.ru парсер через Playwright (обход DDoS-Guard)
"""
import asyncio
import re
from datetime import datetime, timedelta
from typing import List, Dict

from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

from parsers.base_parser import BaseParser


class HHParser(BaseParser):
    """Парсер HH.ru через Playwright (обход DDoS-Guard)"""

    SOURCE_NAME = "hh"

    SEARCH_QUERIES = [
        "SEO продвижение",
        "контекстная реклама",
        "разработка сайта",
        "интернет-маркетинг",
        "SMM",
    ]

    BASE_URL = "https://hh.ru/search/vacancy"

    async def parse(self) -> List[Dict]:
        """Основной метод парсинга"""
        all_leads = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=self.get_random_headers()["User-Agent"]
            )
            page = await context.new_page()

            for query in self.SEARCH_QUERIES:
                try:
                    self.logger.info(f"Парсинг запроса: '{query}'")
                    leads = await self.parse_query(page, query)
                    all_leads.extend(leads)
                    await self.random_delay(5, 10)
                except Exception as e:
                    self.logger.error(f"Ошибка парсинга запроса '{query}': {e}")
                    continue

            await browser.close()

        self.logger.info(f"Всего собрано лидов: {len(all_leads)}")
        return all_leads

    async def parse_query(self, page, query: str) -> List[Dict]:
        """Парсить один поисковый запрос"""
        leads = []

        # Параметры поиска
        params = {
            "text": query,
            "area": "113",  # Россия
            "order_by": "publication_time",
            "search_period": "1",  # За последний день
        }

        # Формируем URL
        url = f"{self.BASE_URL}?" + "&".join([f"{k}={v}" for k, v in params.items()])

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)

            # Прокрутка для загрузки контента
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2)

            # Получить HTML
            html = await page.content()
            soup = BeautifulSoup(html, "lxml")

            # Найти карточки вакансий (правильный селектор)
            vacancy_cards = soup.find_all("div", {"data-qa": "vacancy-serp__vacancy"})

            if not vacancy_cards:
                self.logger.warning(f"Вакансии не найдены для запроса '{query}'")
                return leads

            self.logger.info(f"Найдено {len(vacancy_cards)} вакансий")

            for card in vacancy_cards[:20]:  # Ограничение 20 на запрос
                try:
                    lead_data = self.parse_vacancy_card(card)
                    if lead_data:
                        leads.append(lead_data)
                except Exception as e:
                    self.logger.debug(f"Ошибка парсинга карточки: {e}")
                    continue

        except Exception as e:
            self.logger.error(f"Ошибка загрузки страницы: {e}")

        return leads

    def parse_vacancy_card(self, card) -> Dict:
        """Парсить карточку вакансии"""
        try:
            # Заголовок и ссылка
            title_elem = card.find("a", {"data-qa": "serp-item__title"})
            if not title_elem:
                return None

            title = title_elem.get_text(strip=True)
            url = title_elem.get("href", "")

            # ID из URL
            external_id = ""
            if url:
                match = re.search(r'/vacancy/(\d+)', url)
                if match:
                    external_id = match.group(1)

            # Компания
            company_elem = card.find("a", {"data-qa": "vacancy-serp__vacancy-employer"})
            company = company_elem.get_text(strip=True) if company_elem else "Не указана"

            # Зарплата
            salary_elem = card.find("span", {"data-qa": "vacancy-serp__vacancy-compensation"})
            salary = salary_elem.get_text(strip=True) if salary_elem else "не указана"

            # Город
            city_elem = card.find("div", {"data-qa": "vacancy-serp__vacancy-address"})
            city = city_elem.get_text(strip=True) if city_elem else None

            # Описание (краткое) - добавим компанию в описание
            desc_elem = card.find("div", {"data-qa": "vacancy-serp__vacancy_snippet_responsibility"})
            description = desc_elem.get_text(strip=True) if desc_elem else ""
            description = f"Компания: {company}\n\n{description}"

            # Валидация
            if not title or not external_id:
                return None

            return {
                "source": self.SOURCE_NAME,
                "external_id": external_id,
                "title": title[:500],
                "description": description[:5000],
                "budget": salary,
                "url": url if url.startswith("http") else f"https://hh.ru{url}",
                "city": city,
                "published_at": datetime.now(),
            }

        except Exception as e:
            self.logger.debug(f"Ошибка парсинга карточки: {e}")
            return None


# Для тестирования
async def main():
    parser = HHParser()
    saved, duplicates = await parser.run()
    print(f"Сохранено: {saved}, дублей: {duplicates}")


if __name__ == "__main__":
    asyncio.run(main())
