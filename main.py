import asyncio
import sys
import threading

from loguru import logger

from config import settings
from database import DatabaseManager
from scheduler import ParserScheduler


def setup_logging():
    """Настроить логирование"""
    # Удалить стандартный handler
    logger.remove()

    # Console handler
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{extra[parser]}</cyan> | <level>{message}</level>",
        level=settings.log_level,
        colorize=True,
    )

    # File handler
    logger.add(
        settings.log_file,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[parser]} | {message}",
        level=settings.log_level,
        rotation="10 MB",
        retention="30 days",
        compression="zip",
    )

    # Добавить контекст по умолчанию
    logger.configure(extra={"parser": "main"})


def start_dashboard():
    """Запустить Flask дашборд в отдельном потоке"""
    try:
        from dashboard.app import app
        logger.info(f"Запуск дашборда на http://localhost:{settings.flask_port}")
        app.run(
            host="0.0.0.0",
            port=settings.flask_port,
            debug=False,
            use_reloader=False,
        )
    except Exception as e:
        logger.error(f"Ошибка запуска дашборда: {e}")


async def main():
    """Главная функция"""
    logger.info("=" * 60)
    logger.info("SEO Lead Parser - Запуск")
    logger.info("=" * 60)

    # Инициализация БД
    try:
        db = DatabaseManager()
        await db.init_db()
        logger.info("База данных инициализирована")
    except Exception as e:
        logger.error(f"Ошибка инициализации БД: {e}")
        return

    # Создать планировщик
    scheduler = ParserScheduler()

    # Первый немедленный запуск
    try:
        logger.info("Запуск первого цикла парсинга")
        await scheduler.run_full_cycle()
    except Exception as e:
        logger.error(f"Ошибка первого цикла: {e}", exc_info=True)

    # Запустить планировщик
    try:
        scheduler.start()
        logger.info(f"Планировщик запущен. Интервал парсинга: {settings.parse_interval_hours}ч")
    except Exception as e:
        logger.error(f"Ошибка запуска планировщика: {e}")
        return

    # Запустить дашборд в отдельном потоке
    dashboard_thread = threading.Thread(
        target=start_dashboard,
        daemon=True,
        name="DashboardThread"
    )
    dashboard_thread.start()
    logger.info(f"Дашборд запущен на http://localhost:{settings.flask_port}")

    # Бесконечный цикл
    logger.info("Система запущена. Нажмите Ctrl+C для остановки")
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Получен сигнал остановки")
        scheduler.shutdown()
        logger.info("Парсер остановлен")


if __name__ == "__main__":
    # Настроить логирование
    setup_logging()

    # Запустить
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Программа завершена")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        sys.exit(1)
