import asyncio
import random
from abc import ABC, abstractmethod
from typing import List, Dict, Tuple, Optional

import aiohttp
from loguru import logger

from database import DatabaseManager


class BaseParser(ABC):
    """Базовый класс для всех парсеров"""

    SOURCE_NAME: str = ""

    USER_AGENTS = [
        # Chrome Windows
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        # Chrome Mac
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        # Firefox Windows
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
        # Firefox Mac
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) Gecko/20100101 Firefox/125.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:124.0) Gecko/20100101 Firefox/124.0",
        # Safari Mac
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
        # Edge Windows
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
        # Chrome Linux
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        # Firefox Linux
        "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
        "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
    ]

    def __init__(self):
        self.db = DatabaseManager()
        self.logger = logger.bind(parser=self.SOURCE_NAME)

    def get_random_headers(self) -> Dict[str, str]:
        """Получить случайные заголовки с User-Agent"""
        return {
            "User-Agent": random.choice(self.USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

    async def random_delay(self, min_sec: float = 2.0, max_sec: float = 5.0):
        """Случайная задержка"""
        delay = random.uniform(min_sec, max_sec)
        self.logger.debug(f"Задержка {delay:.2f} сек")
        await asyncio.sleep(delay)

    async def fetch_with_retry(
        self,
        url: str,
        max_retries: int = 3,
        timeout: int = 30,
        **kwargs
    ) -> Optional[str]:
        """
        HTTP запрос с retry логикой
        Returns: HTML content или None при ошибке
        """
        headers = kwargs.pop("headers", self.get_random_headers())

        for attempt in range(1, max_retries + 1):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        url,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=timeout),
                        **kwargs
                    ) as response:
                        if response.status == 200:
                            content = await response.text()
                            self.logger.debug(f"Успешный запрос: {url}")
                            return content
                        else:
                            self.logger.warning(f"HTTP {response.status} для {url}")

            except asyncio.TimeoutError:
                self.logger.warning(f"Timeout для {url} (попытка {attempt}/{max_retries})")
            except aiohttp.ClientError as e:
                self.logger.warning(f"Ошибка запроса {url}: {e} (попытка {attempt}/{max_retries})")
            except Exception as e:
                self.logger.error(f"Неожиданная ошибка при запросе {url}: {e}")

            # Exponential backoff
            if attempt < max_retries:
                backoff_delay = 2 ** attempt
                self.logger.debug(f"Повтор через {backoff_delay} сек")
                await asyncio.sleep(backoff_delay)

        self.logger.error(f"Не удалось получить {url} после {max_retries} попыток")
        return None

    @abstractmethod
    async def parse(self) -> List[Dict]:
        """
        Основной метод парсинга (реализуется в наследниках)
        Returns: список словарей с данными лидов
        """
        pass

    async def run(self) -> Tuple[int, int]:
        """
        Запустить парсинг и сохранить результаты в БД
        Returns: (saved_count, duplicates_count)
        """
        self.logger.info(f"=== Запуск парсера {self.SOURCE_NAME} ===")

        try:
            # Парсинг
            leads = await self.parse()
            self.logger.info(f"Спарсено лидов: {len(leads)}")

            if not leads:
                self.logger.warning("Лиды не найдены")
                return 0, 0

            # Сохранение в БД
            saved, duplicates = await self.db.save_leads_batch(leads)
            self.logger.info(f"Сохранено: {saved}, дублей: {duplicates}")

            return saved, duplicates

        except Exception as e:
            self.logger.error(f"Ошибка парсера {self.SOURCE_NAME}: {e}", exc_info=True)
            raise
