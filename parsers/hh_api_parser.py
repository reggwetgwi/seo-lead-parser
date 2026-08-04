"""
HH.ru парсер через официальный API с OAuth авторизацией
"""
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Optional

import aiohttp
from bs4 import BeautifulSoup

from parsers.base_parser import BaseParser
from config import settings


class HHAPIParser(BaseParser):
    """Парсер HH.ru через официальный API с OAuth"""

    SOURCE_NAME = "hh"

    SEARCH_QUERIES = [
        "SEO продвижение",
        "поисковое продвижение сайта",
        "контекстная реклама",
        "интернет-маркетинг",
        "разработка сайта",
        "веб-разработка",
        "SMM маркетолог",
        "digital маркетинг",
        "Яндекс Директ",
    ]

    API_BASE_URL = "https://api.hh.ru"
    OAUTH_URL = "https://hh.ru/oauth/token"

    def __init__(self):
        super().__init__()
        self.seen_ids = set()
        self.access_token = settings.hh_access_token

    async def refresh_access_token(self) -> bool:
        """Обновить access token через OAuth"""
        if not settings.hh_client_id or not settings.hh_client_secret:
            self.logger.error("HH_CLIENT_ID и HH_CLIENT_SECRET не настроены")
            return False

        data = {
            "grant_type": "client_credentials",
            "client_id": settings.hh_client_id,
            "client_secret": settings.hh_client_secret,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.OAUTH_URL,
                    data=data,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        self.access_token = result.get("access_token")
                        expires_in = result.get("expires_in", 0)
                        self.logger.info(f"Access token обновлен (истекает через {expires_in}с)")
                        return True
                    else:
                        error_text = await response.text()
                        self.logger.error(f"Ошибка OAuth: {response.status} - {error_text}")
                        return False
        except Exception as e:
            self.logger.error(f"Ошибка обновления токена: {e}")
            return False

    def get_api_headers(self) -> Dict[str, str]:
        """Заголовки для HH.ru API с OAuth"""
        headers = {
            "User-Agent": settings.hh_app_name,
            "HH-User-Agent": settings.hh_app_name,
        }

        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"

        return headers

    async def fetch_vacancies_list(self, query: str, page: int = 0) -> Dict:
        """Получить список вакансий по запросу"""
        date_from = (datetime.utcnow() - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%S")

        params = {
            "text": query,
            "area": 113,  # Россия
            "per_page": 100,
            "page": page,
            "order_by": "publication_time",
            "date_from": date_from,
        }

        url = f"{self.API_BASE_URL}/vacancies"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    params=params,
                    headers=self.get_api_headers(),
                    timeout=aiohttp.ClientTimeout(total=settings.request_timeout)
                ) as response:
                    if response.status == 200:
                        return await response.json()
                    elif response.status == 403:
                        self.logger.warning("403 Forbidden - требуется OAuth токен")
                        # Попробовать обновить токен
                        if await self.refresh_access_token():
                            # Повторить запрос с новым токеном
                            async with session.get(
                                url,
                                params=params,
                                headers=self.get_api_headers(),
                                timeout=aiohttp.ClientTimeout(total=settings.request_timeout)
                            ) as retry_response:
                                if retry_response.status == 200:
                                    return await retry_response.json()
                        return {}
                    else:
                        self.logger.warning(f"HH API вернул статус {response.status}")
                        try:
                            error_text = await response.text()
                            self.logger.debug(f"Ответ API: {error_text[:200]}")
                        except:
                            pass
                        return {}
        except Exception as e:
            self.logger.error(f"Ошибка запроса к HH API: {e}")
            return {}

    async def fetch_vacancy_details(self, vacancy_id: str) -> Dict:
        """Получить полное описание вакансии"""
        url = f"{self.API_BASE_URL}/vacancies/{vacancy_id}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=self.get_api_headers(),
                    timeout=aiohttp.ClientTimeout(total=settings.request_timeout)
                ) as response:
                    if response.status == 200:
                        return await response.json()
                    elif response.status == 403:
                        self.logger.warning(f"403 для вакансии {vacancy_id} - требуется OAuth")
                        return {}
                    else:
                        self.logger.warning(f"Не удалось получить детали вакансии {vacancy_id}")
                        return {}
        except Exception as e:
            self.logger.error(f"Ошибка получения деталей вакансии {vacancy_id}: {e}")
            return {}

    def clean_html(self, html_text: str) -> str:
        """Очистить HTML теги из текста"""
        if not html_text:
            return ""
        soup = BeautifulSoup(html_text, "lxml")
        return soup.get_text(separator=" ", strip=True)

    def format_salary(self, salary: Optional[Dict]) -> str:
        """Форматировать зарплату"""
        if not salary:
            return "не указан"

        parts = []
        if salary.get("from"):
            parts.append(f"от {salary['from']}")
        if salary.get("to"):
            parts.append(f"до {salary['to']}")

        currency = salary.get("currency", "RUR")
        if currency == "RUR":
            currency = "₽"
        elif currency == "USD":
            currency = "$"
        elif currency == "EUR":
            currency = "€"

        return " ".join(parts) + f" {currency}" if parts else "не указан"

    def parse_vacancy(self, vacancy: Dict) -> Optional[Dict]:
        """Преобразовать вакансию HH в формат Lead"""
        vacancy_id = str(vacancy.get("id", ""))

        # Пропустить дубликаты
        if vacancy_id in self.seen_ids:
            return None
        self.seen_ids.add(vacancy_id)

        # Очистить описание от HTML
        description = self.clean_html(vacancy.get("description", ""))
        if not description:
            description = vacancy.get("snippet", {}).get("requirement", "") + " " + \
                         vacancy.get("snippet", {}).get("responsibility", "")

        # Добавить информацию о компании
        employer = vacancy.get("employer", {})
        company_name = employer.get("name", "Не указана")
        description = f"Компания: {company_name}\n\n{description}"

        # Парсинг даты
        published_str = vacancy.get("published_at", "")
        try:
            published_at = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
        except:
            published_at = datetime.utcnow()

        return {
            "source": self.SOURCE_NAME,
            "external_id": vacancy_id,
            "title": vacancy.get("name", "Без названия")[:500],
            "description": description[:5000],
            "budget": self.format_salary(vacancy.get("salary")),
            "url": vacancy.get("alternate_url", ""),
            "city": vacancy.get("area", {}).get("name"),
            "published_at": published_at,
        }

    async def parse_query(self, query: str) -> List[Dict]:
        """Парсить все вакансии по одному запросу"""
        self.logger.info(f"Парсинг запроса: '{query}'")
        leads = []
        page = 0
        max_pages = 2  # Ограничение (200 вакансий на запрос)

        while page < max_pages:
            data = await self.fetch_vacancies_list(query, page)

            if not data or "items" not in data:
                break

            items = data.get("items", [])
            if not items:
                break

            self.logger.debug(f"Страница {page}: найдено {len(items)} вакансий")

            # Обработать каждую вакансию
            for vacancy in items:
                vacancy_id = str(vacancy.get("id", ""))

                # Получить полное описание
                full_vacancy = await self.fetch_vacancy_details(vacancy_id)
                if full_vacancy:
                    lead_data = self.parse_vacancy(full_vacancy)
                    if lead_data:
                        leads.append(lead_data)

                # Rate limit: 1 запрос в секунду
                await asyncio.sleep(1)

            # Проверить есть ли еще страницы
            pages_total = data.get("pages", 0)
            if page >= pages_total - 1:
                break

            page += 1
            await self.random_delay(1, 2)

        self.logger.info(f"Запрос '{query}': собрано {len(leads)} лидов")
        return leads

    async def parse(self) -> List[Dict]:
        """Основной метод парсинга"""
        # Проверить наличие токена
        if not self.access_token:
            self.logger.warning("HH_ACCESS_TOKEN не настроен, попытка получить новый")
            if not await self.refresh_access_token():
                self.logger.error("Не удалось получить access token, парсинг невозможен")
                return []

        all_leads = []

        for query in self.SEARCH_QUERIES:
            try:
                leads = await self.parse_query(query)
                all_leads.extend(leads)
            except Exception as e:
                self.logger.error(f"Ошибка парсинга запроса '{query}': {e}")
                continue

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
    parser = HHAPIParser()
    saved, duplicates = await parser.run()
    print(f"Сохранено: {saved}, дублей: {duplicates}")


if __name__ == "__main__":
    asyncio.run(main())
