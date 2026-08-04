import asyncio
import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional
import random

import google.generativeai as genai
from loguru import logger

from config import settings
from database import DatabaseManager, Lead

# Профиль компании (услуги, стоп-критерии, шкала оценки) держим вне кода:
# он меняется чаще самого парсера и у каждой компании свой.
# Рабочий файл в .gitignore, в репозитории лежит lead_scoring.example.txt.
PROMPT_PATH = Path(
    os.environ.get("SCORING_PROMPT_PATH", Path(__file__).parent / "prompts" / "lead_scoring.txt")
)


class AIFilter:
    """AI-фильтрация лидов через Google Gemini"""

    def __init__(self):
        self.api_keys = settings.gemini_api_keys
        self.current_key_index = 0
        self.db = DatabaseManager()
        self.logger = logger.bind(parser="ai_filter")
        self.prompt_template = self._load_prompt_template()

        # Инициализация с первым ключом
        self._configure_api()

        self.logger.info(f"Инициализирован с {len(self.api_keys)} API ключами")

    def _configure_api(self):
        """Настроить API с текущим ключом"""
        current_key = self.api_keys[self.current_key_index]
        genai.configure(api_key=current_key)
        self.model = genai.GenerativeModel("gemini-2.5-flash")
        self.logger.debug(f"Используется API ключ #{self.current_key_index + 1}")

    def _rotate_key(self):
        """Переключиться на следующий ключ"""
        self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
        self._configure_api()
        self.logger.info(f"Переключение на API ключ #{self.current_key_index + 1}")

    def _load_prompt_template(self) -> str:
        """Прочитать шаблон промпта скоринга с диска."""
        if not PROMPT_PATH.exists():
            example = PROMPT_PATH.with_name("lead_scoring.example.txt")
            raise FileNotFoundError(
                f"Не найден шаблон промпта {PROMPT_PATH}. "
                f"Скопируйте {example.name} в {PROMPT_PATH.name} "
                f"и опишите в нём услуги и критерии отбора вашей компании."
            )
        return PROMPT_PATH.read_text(encoding="utf-8")

    def build_prompt(self, lead: Lead) -> str:
        """Подставить данные лида в шаблон промпта."""
        return self.prompt_template.format(
            source=lead.source,
            title=lead.title,
            description=lead.description[:2000],
            budget=lead.budget or "не указан",
            city=lead.city or "не указан",
        )

    def parse_json_response(self, response_text: str) -> Optional[Dict]:
        """Парсинг JSON ответа от Gemini"""
        try:
            # Удалить markdown блоки если есть
            cleaned = re.sub(r'```json\s*|\s*```', '', response_text)
            cleaned = cleaned.strip()

            # Парсинг JSON
            data = json.loads(cleaned)

            # Валидация обязательных полей
            required_fields = ["is_relevant", "ai_score", "urgency", "recommended_service", "ai_summary"]
            for field in required_fields:
                if field not in data:
                    logger.warning(f"Отсутствует обязательное поле: {field}")
                    return None

            # Валидация типов
            if not isinstance(data["is_relevant"], bool):
                data["is_relevant"] = data["is_relevant"] in ["true", "True", True]

            if not isinstance(data["ai_score"], int) or not (1 <= data["ai_score"] <= 10):
                logger.warning(f"Некорректный ai_score: {data.get('ai_score')}")
                return None

            if data["urgency"] not in ["low", "medium", "high"]:
                logger.warning(f"Некорректный urgency: {data.get('urgency')}")
                data["urgency"] = "medium"

            # Преобразовать tags в JSON строку
            if "tags" in data and isinstance(data["tags"], list):
                data["tags"] = json.dumps(data["tags"], ensure_ascii=False)

            return data

        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга JSON: {e}")
            logger.debug(f"Ответ Gemini: {response_text[:500]}")
            return None
        except Exception as e:
            logger.error(f"Неожиданная ошибка парсинга: {e}")
            return None

    async def score_lead(self, lead: Lead, max_retries: int = 3) -> Optional[Dict]:
        """
        Оценить один лид через Gemini
        Returns: словарь с AI-полями или None при ошибке
        """
        prompt = self.build_prompt(lead)

        for attempt in range(1, max_retries + 1):
            try:
                logger.debug(f"Запрос к Gemini для лида {lead.id} (попытка {attempt}/{max_retries})")

                response = await asyncio.to_thread(
                    self.model.generate_content,
                    prompt
                )

                response_text = response.text
                logger.debug(f"Ответ Gemini получен: {len(response_text)} символов")

                # Парсинг ответа
                ai_data = self.parse_json_response(response_text)
                if ai_data:
                    logger.info(f"Лид {lead.id} оценен: score={ai_data['ai_score']}, relevant={ai_data['is_relevant']}")
                    return ai_data
                else:
                    logger.warning(f"Не удалось распарсить ответ Gemini для лида {lead.id}")

            except Exception as e:
                error_msg = str(e)

                # Проверка на ошибку квоты
                if "429" in error_msg or "quota" in error_msg.lower() or "resource_exhausted" in error_msg.lower():
                    self.logger.warning(f"Квота API ключа #{self.current_key_index + 1} исчерпана")

                    # Попробовать следующий ключ
                    if len(self.api_keys) > 1:
                        self._rotate_key()
                        self.logger.info(f"Повтор запроса с новым ключом")
                        continue
                    else:
                        self.logger.error("Все API ключи исчерпали квоту")
                        return None

                logger.error(f"Ошибка запроса к Gemini (попытка {attempt}/{max_retries}): {e}")

            # Retry с задержкой
            if attempt < max_retries:
                delay = [5, 10, 20][attempt - 1]
                logger.debug(f"Повтор через {delay} сек")
                await asyncio.sleep(delay)

        logger.error(f"Не удалось оценить лид {lead.id} после {max_retries} попыток")
        return None

    async def score_batch(self, leads: List[Lead], max_workers: int = 5) -> List[Dict]:
        """
        Параллельная обработка пакета лидов
        Returns: список словарей с AI-данными
        """
        logger.info(f"Начало обработки пакета из {len(leads)} лидов (workers={max_workers})")

        # Создать семафор для ограничения параллельных запросов
        semaphore = asyncio.Semaphore(max_workers)

        async def score_with_semaphore(lead: Lead) -> tuple:
            async with semaphore:
                ai_data = await self.score_lead(lead)
                return lead.id, ai_data

        # Запустить все задачи
        tasks = [score_with_semaphore(lead) for lead in leads]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Собрать успешные результаты
        scored = []
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Ошибка обработки лида: {result}")
                continue

            lead_id, ai_data = result
            if ai_data:
                scored.append({"lead_id": lead_id, "ai_data": ai_data})

        logger.info(f"Обработано успешно: {len(scored)}/{len(leads)}")
        return scored

    async def process_unscored_leads(self, limit: int = 50) -> int:
        """
        Взять непроскоренные лиды из БД, оценить и сохранить
        Returns: количество обработанных лидов
        """
        logger.info(f"Поиск непроскоренных лидов (limit={limit})")

        # Получить лиды без AI-оценки
        leads = await self.db.get_leads_for_ai(limit)
        if not leads:
            logger.info("Непроскоренные лиды не найдены")
            return 0

        logger.info(f"Найдено {len(leads)} непроскоренных лидов")

        # Обработать пакетом
        scored = await self.score_batch(leads)

        # Сохранить результаты в БД
        for item in scored:
            try:
                await self.db.update_ai_fields(item["lead_id"], item["ai_data"])
            except Exception as e:
                logger.error(f"Ошибка сохранения AI-полей для лида {item['lead_id']}: {e}")

        logger.info(f"AI-скоринг завершен: обработано {len(scored)} лидов")
        return len(scored)


# Для тестирования
async def main():
    ai_filter = AIFilter()
    count = await ai_filter.process_unscored_leads(limit=10)
    print(f"Обработано лидов: {count}")


if __name__ == "__main__":
    asyncio.run(main())
