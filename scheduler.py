import asyncio
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from config import settings
from database import DatabaseManager
from ai_filter import AIFilter
from notifier import TelegramNotifier
from parsers.fl_parser import FLParser
from parsers.hh_api_parser import HHAPIParser
from parsers.youdo_parser import YouDoParser
from parsers.workspace_parser import WorkspaceParser
from parsers.kwork_parser import KworkParser
# from parsers.avito_parser import AvitoParser


class ParserScheduler:
    """Планировщик задач парсинга"""

    def __init__(self):
        self.scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
        self.db = DatabaseManager()
        self.ai_filter = AIFilter()
        self.notifier = TelegramNotifier()
        self.logger = logger.bind(parser="scheduler")

        # Парсеры
        self.parsers = [
            FLParser,              # Работает отлично (~270 лидов)
            HHAPIParser,           # Работает через OAuth API
            WorkspaceParser,       # Работает с авторизацией (~1-5 лидов, мало заказов)
            KworkParser,           # Работает через Playwright (~36 лидов/цикл)
            # YouDoParser,         # Отключен - не показывает задачи даже с авторизацией
            # AvitoParser,         # Отключен - требуется прокси
        ]

    async def run_full_cycle(self):
        """Полный цикл: парсинг → AI → уведомления"""
        logger.info("=== Запуск полного цикла парсинга ===")
        start_time = datetime.now()

        total_saved = 0
        total_duplicates = 0

        # 1. Запустить все парсеры последовательно
        for parser_class in self.parsers:
            try:
                parser = parser_class()
                logger.info(f"Запуск парсера: {parser.SOURCE_NAME}")

                saved, duplicates = await parser.run()
                total_saved += saved
                total_duplicates += duplicates

                logger.info(f"{parser.SOURCE_NAME}: сохранено {saved}, дублей {duplicates}")

            except Exception as e:
                logger.error(f"Ошибка парсера {parser_class.__name__}: {e}", exc_info=True)
                # Отправить алерт
                await self.notifier.send_error_alert(f"Парсер {parser_class.__name__} упал: {str(e)[:200]}")
                continue

        logger.info(f"Парсинг завершен: всего сохранено {total_saved}, дублей {total_duplicates}")

        # 2. AI-фильтрация всех непроскоренных лидов
        try:
            logger.info("Запуск AI-скоринга")
            scored_count = await self.ai_filter.process_unscored_leads(limit=50)
            logger.info(f"Проскорено лидов: {scored_count}")

        except Exception as e:
            logger.error(f"Ошибка AI-скоринга: {e}", exc_info=True)
            await self.notifier.send_error_alert(f"AI-скоринг упал: {str(e)[:200]}")

        # 3. Отправить новые горячие лиды
        try:
            logger.info("Поиск новых горячих лидов для уведомлений")
            new_leads = await self.db.get_new_leads_for_notify(settings.min_score_for_alert)

            if new_leads:
                logger.info(f"Найдено {len(new_leads)} горячих лидов")
                sent_count = await self.notifier.send_leads_batch(new_leads)

                # Пометить как отправленные
                lead_ids = [lead.id for lead in new_leads]
                await self.db.mark_as_notified(lead_ids)

                logger.info(f"Отправлено уведомлений: {sent_count}")
            else:
                logger.info("Новых горячих лидов не найдено")

        except Exception as e:
            logger.error(f"Ошибка отправки уведомлений: {e}", exc_info=True)
            await self.notifier.send_error_alert(f"Отправка уведомлений упала: {str(e)[:200]}")

        # Итоги
        duration = (datetime.now() - start_time).total_seconds()
        logger.info(f"=== Цикл завершен за {duration:.1f} сек ===")

    async def send_daily_summary(self):
        """Отправить ежедневный отчет"""
        logger.info("Отправка ежедневного отчета")

        try:
            stats = await self.db.get_stats()
            await self.notifier.send_daily_summary(stats)
            logger.info("Ежедневный отчет отправлен")

        except Exception as e:
            logger.error(f"Ошибка отправки ежедневного отчета: {e}", exc_info=True)

    async def cleanup_old_leads(self):
        """Очистка старых лидов"""
        logger.info("Очистка старых лидов")

        try:
            await self.db.cleanup_old_leads(settings.leads_retention_days)
            logger.info(f"Очистка завершена (старше {settings.leads_retention_days} дней)")

        except Exception as e:
            logger.error(f"Ошибка очистки старых лидов: {e}", exc_info=True)

    def setup_jobs(self):
        """Настроить задачи планировщика"""
        logger.info("Настройка задач планировщика")

        # 1. Полный цикл парсинга каждые N часов
        self.scheduler.add_job(
            self.run_full_cycle,
            trigger=IntervalTrigger(hours=settings.parse_interval_hours),
            id="full_cycle",
            name="Полный цикл парсинга",
            replace_existing=True,
        )
        logger.info(f"Задача 'Полный цикл': каждые {settings.parse_interval_hours} часов")

        # 2. Ежедневный отчет в 09:00 МСК
        self.scheduler.add_job(
            self.send_daily_summary,
            trigger=CronTrigger(hour=9, minute=0, timezone="Europe/Moscow"),
            id="daily_summary",
            name="Ежедневный отчет",
            replace_existing=True,
        )
        logger.info("Задача 'Ежедневный отчет': каждый день в 09:00 МСК")

        # 3. Очистка старых лидов в 03:00 МСК
        self.scheduler.add_job(
            self.cleanup_old_leads,
            trigger=CronTrigger(hour=3, minute=0, timezone="Europe/Moscow"),
            id="cleanup",
            name="Очистка старых лидов",
            replace_existing=True,
        )
        logger.info("Задача 'Очистка': каждый день в 03:00 МСК")

    def start(self):
        """Запустить планировщик"""
        self.setup_jobs()
        self.scheduler.start()
        logger.info("Планировщик запущен")

    def shutdown(self):
        """Остановить планировщик"""
        self.scheduler.shutdown()
        logger.info("Планировщик остановлен")


# Для тестирования
async def main():
    scheduler = ParserScheduler()

    # Инициализация БД
    await scheduler.db.init_db()

    # Запустить один цикл
    await scheduler.run_full_cycle()


if __name__ == "__main__":
    asyncio.run(main())
