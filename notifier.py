import asyncio
from datetime import datetime
from typing import List, Dict

from telegram import Bot
from telegram.constants import ParseMode
from loguru import logger

from config import settings
from database import Lead


class TelegramNotifier:
    """Отправка уведомлений в Telegram"""

    def __init__(self):
        self.bot = Bot(token=settings.telegram_bot_token)
        self.chat_id = settings.telegram_chat_id
        self.logger = logger.bind(parser="notifier")

    def get_urgency_emoji(self, urgency: str) -> str:
        """Получить emoji по уровню срочности"""
        emoji_map = {
            "high": "🔥",
            "medium": "⚡",
            "low": "📌",
        }
        return emoji_map.get(urgency, "📌")

    def format_lead_message(self, lead: Lead) -> str:
        """Форматировать сообщение для лида"""
        urgency_emoji = self.get_urgency_emoji(lead.urgency or "low")

        # Заголовок
        message = f"{urgency_emoji} <b>Новый лид · Score {lead.ai_score}/10</b>\n\n"

        # Название (ограничить длину)
        title = lead.title[:200] if len(lead.title) > 200 else lead.title
        message += f"<b>{title}</b>\n\n"

        # Основная информация
        message += f"🏷 <b>{lead.recommended_service or 'Не определено'}</b>\n"
        message += f"💰 {lead.budget or 'бюджет не указан'}\n"
        message += f"📍 {lead.city or 'город не указан'}\n"

        # Источник
        source_labels = {
            "hh": "HH.ru",
            "fl": "FL.ru",
            "kwork": "Kwork",
            "avito": "Avito"
        }
        message += f"🌐 {source_labels.get(lead.source, lead.source)}\n\n"

        # Профиль клиента
        if lead.client_profile:
            profile = lead.client_profile[:150] if len(lead.client_profile) > 150 else lead.client_profile
            message += f"👤 {profile}\n\n"

        # AI саммари (ограничить длину)
        if lead.ai_summary:
            summary = lead.ai_summary[:500] if len(lead.ai_summary) > 500 else lead.ai_summary
            message += f"📋 <b>Анализ:</b>\n{summary}\n\n"

        # Контакты
        if lead.contact_info:
            message += f"📞 {lead.contact_info}\n"

        # Ссылка
        message += f"🔗 <a href='{lead.url}'>Открыть оригинал</a>\n\n"

        # Хештеги
        tags = []
        tags.append(f"#{lead.source}")
        if lead.urgency:
            tags.append(f"#{lead.urgency}")

        # Добавить теги из AI
        if lead.tags:
            try:
                import json
                ai_tags = json.loads(lead.tags)
                for tag in ai_tags[:3]:  # Максимум 3 тега
                    clean_tag = tag.replace(" ", "_").replace("-", "_")
                    tags.append(f"#{clean_tag}")
            except:
                pass

        message += " ".join(tags)

        # Ограничить общую длину сообщения (Telegram лимит 4096 символов)
        if len(message) > 4000:
            message = message[:3997] + "..."

        return message

    async def send_lead(self, lead: Lead) -> bool:
        """
        Отправить уведомление об одном лиде
        Returns: True если успешно
        """
        try:
            message = self.format_lead_message(lead)

            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )

            logger.info(f"Уведомление отправлено для лида {lead.id}")
            return True

        except Exception as e:
            logger.error(f"Ошибка отправки уведомления для лида {lead.id}: {e}")
            return False

    async def send_leads_batch(self, leads: List[Lead]) -> int:
        """
        Отправить пакет уведомлений с задержкой
        Returns: количество успешно отправленных
        """
        if not leads:
            logger.info("Нет лидов для отправки")
            return 0

        logger.info(f"Отправка {len(leads)} уведомлений")
        sent_count = 0

        for lead in leads:
            success = await self.send_lead(lead)
            if success:
                sent_count += 1

            # Задержка между сообщениями (Telegram rate limit)
            await asyncio.sleep(1)

        logger.info(f"Отправлено уведомлений: {sent_count}/{len(leads)}")
        return sent_count

    async def send_daily_summary(self, stats: Dict) -> bool:
        """Отправить ежедневный отчет"""
        try:
            date_str = datetime.now().strftime("%d.%m.%Y")

            message = f"📊 <b>Сводка за вчера — {date_str}</b>\n\n"

            # Общая статистика
            total = stats.get("recent_24h", 0)
            hot_count = stats.get("hot_count", 0)
            warm_count = stats.get("warm_count", 0)

            message += f"Всего лидов: <b>{total}</b>\n"
            message += f"🔥 Горячих (score 9-10): <b>{hot_count}</b>\n"
            message += f"⚡ Тёплых (score 7-8): <b>{warm_count}</b>\n\n"

            # По источникам
            by_source = stats.get("by_source", {})
            if by_source:
                message += "По источникам:\n"
                source_labels = {
                    "hh": "HH.ru",
                    "fl": "FL.ru",
                    "kwork": "Kwork",
                    "avito": "Avito"
                }
                for source, count in by_source.items():
                    label = source_labels.get(source, source)
                    message += f"• {label}: {count}\n"
                message += "\n"

            # Ссылка на дашборд
            message += f"👉 Дашборд: http://localhost:{settings.flask_port}"

            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )

            logger.info("Ежедневный отчет отправлен")
            return True

        except Exception as e:
            logger.error(f"Ошибка отправки ежедневного отчета: {e}")
            return False

    async def send_error_alert(self, error_msg: str) -> bool:
        """Отправить алерт о критической ошибке"""
        try:
            message = f"⚠️ <b>Ошибка парсера</b>\n\n"
            message += f"<code>{error_msg[:500]}</code>"

            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode=ParseMode.HTML,
            )

            logger.info("Алерт об ошибке отправлен")
            return True

        except Exception as e:
            logger.error(f"Ошибка отправки алерта: {e}")
            return False


# Для тестирования
async def main():
    notifier = TelegramNotifier()

    # Тестовая статистика
    test_stats = {
        "recent_24h": 42,
        "hot_count": 5,
        "warm_count": 12,
        "by_source": {
            "hh": 15,
            "fl": 10,
            "kwork": 8,
            "avito": 9
        }
    }

    await notifier.send_daily_summary(test_stats)
    print("Тестовое уведомление отправлено")


if __name__ == "__main__":
    asyncio.run(main())
