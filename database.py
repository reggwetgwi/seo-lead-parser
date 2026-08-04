import asyncio
from datetime import datetime, timedelta
from typing import Optional, List, Tuple, Dict

from sqlalchemy import UniqueConstraint, Index, select, func, and_, or_
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Integer, DateTime, Boolean, Text

from config import settings
from loguru import logger


class Base(DeclarativeBase):
    pass


class Lead(Base):
    __tablename__ = "leads"

    # Primary key
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Core fields
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # hh / fl / kwork / avito
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    budget: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    budget_rub: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    city: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    contact_info: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    parsed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    # AI fields
    ai_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ai_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    recommended_service: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    urgency: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # low / medium / high
    client_profile: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tags: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON string
    is_relevant: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    # Status
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="new")  # new / notified / in_progress / closed
    notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    manager_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Constraints and indexes
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_source_external_id"),
        Index("idx_ai_score", "ai_score"),
        Index("idx_status", "status"),
        Index("idx_published_at", "published_at"),
        Index("idx_source", "source"),
    )


class DatabaseManager:
    def __init__(self):
        self.engine = create_async_engine(
            settings.database_url,
            echo=False,
            pool_pre_ping=True,
        )
        self.async_session = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def init_db(self):
        """Создать все таблицы"""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("База данных инициализирована")

    async def save_lead(self, lead_data: dict) -> bool:
        """
        Сохранить один лид
        Returns: True если сохранен, False если дубликат
        """
        async with self.async_session() as session:
            try:
                lead = Lead(**lead_data)
                session.add(lead)
                await session.commit()
                logger.debug(f"Лид сохранен: {lead_data['source']} - {lead_data['title'][:50]}")
                return True
            except Exception as e:
                await session.rollback()
                if "UNIQUE constraint failed" in str(e) or "uq_source_external_id" in str(e):
                    logger.debug(f"Дубликат пропущен: {lead_data['source']} - {lead_data['external_id']}")
                    return False
                else:
                    logger.error(f"Ошибка сохранения лида: {e}")
                    raise

    async def save_leads_batch(self, leads: List[dict]) -> Tuple[int, int]:
        """
        Сохранить пакет лидов
        Returns: (saved_count, duplicates_count)
        """
        saved = 0
        duplicates = 0

        for lead_data in leads:
            result = await self.save_lead(lead_data)
            if result:
                saved += 1
            else:
                duplicates += 1

        return saved, duplicates

    async def get_leads_for_ai(self, limit: int = 50) -> List[Lead]:
        """Получить лиды без AI-оценки"""
        async with self.async_session() as session:
            stmt = (
                select(Lead)
                .where(Lead.ai_score.is_(None))
                .order_by(Lead.parsed_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update_ai_fields(self, lead_id: int, ai_data: dict):
        """Обновить AI-поля лида"""
        async with self.async_session() as session:
            stmt = select(Lead).where(Lead.id == lead_id)
            result = await session.execute(stmt)
            lead = result.scalar_one_or_none()

            if lead:
                for key, value in ai_data.items():
                    setattr(lead, key, value)
                await session.commit()
                logger.debug(f"AI-поля обновлены для лида {lead_id}")
            else:
                logger.warning(f"Лид {lead_id} не найден")

    async def get_new_leads_for_notify(self, min_score: int) -> List[Lead]:
        """Получить новые горячие лиды для уведомлений"""
        async with self.async_session() as session:
            stmt = (
                select(Lead)
                .where(
                    and_(
                        Lead.ai_score >= min_score,
                        Lead.notified_at.is_(None),
                        Lead.is_relevant == True,
                    )
                )
                .order_by(Lead.ai_score.desc(), Lead.published_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def mark_as_notified(self, lead_ids: List[int]):
        """Пометить лиды как отправленные"""
        async with self.async_session() as session:
            stmt = select(Lead).where(Lead.id.in_(lead_ids))
            result = await session.execute(stmt)
            leads = result.scalars().all()

            for lead in leads:
                lead.notified_at = datetime.utcnow()
                lead.status = "notified"

            await session.commit()
            logger.info(f"Помечено как notified: {len(lead_ids)} лидов")

    async def update_status(self, lead_id: int, status: str, note: Optional[str] = None):
        """Обновить статус лида"""
        async with self.async_session() as session:
            stmt = select(Lead).where(Lead.id == lead_id)
            result = await session.execute(stmt)
            lead = result.scalar_one_or_none()

            if lead:
                if status:
                    lead.status = status
                if note is not None:
                    lead.manager_note = note
                await session.commit()
                logger.info(f"Статус лида {lead_id} обновлен: {status}")
            else:
                logger.warning(f"Лид {lead_id} не найден")

    async def get_leads_paginated(
        self,
        page: int = 1,
        per_page: int = 50,
        filters: Optional[Dict] = None
    ) -> Tuple[List[Lead], int]:
        """
        Получить лиды с пагинацией и фильтрами
        Returns: (leads, total_count)
        """
        async with self.async_session() as session:
            # Базовый запрос
            stmt = select(Lead)
            count_stmt = select(func.count(Lead.id))

            # Применить фильтры
            if filters:
                conditions = []

                if filters.get("source"):
                    conditions.append(Lead.source == filters["source"])

                if filters.get("status"):
                    conditions.append(Lead.status == filters["status"])

                if filters.get("urgency"):
                    conditions.append(Lead.urgency == filters["urgency"])

                if filters.get("min_score"):
                    conditions.append(Lead.ai_score >= filters["min_score"])

                if filters.get("date_from"):
                    conditions.append(Lead.published_at >= filters["date_from"])

                if filters.get("date_to"):
                    conditions.append(Lead.published_at <= filters["date_to"])

                if conditions:
                    stmt = stmt.where(and_(*conditions))
                    count_stmt = count_stmt.where(and_(*conditions))

            # Получить общее количество
            total_result = await session.execute(count_stmt)
            total_count = total_result.scalar()

            # Применить сортировку и пагинацию
            stmt = (
                stmt
                .order_by(Lead.ai_score.desc().nulls_last(), Lead.published_at.desc())
                .offset((page - 1) * per_page)
                .limit(per_page)
            )

            result = await session.execute(stmt)
            leads = list(result.scalars().all())

            return leads, total_count

    async def get_stats(self) -> Dict:
        """Получить статистику по лидам"""
        async with self.async_session() as session:
            # Общее количество
            total_stmt = select(func.count(Lead.id))
            total_result = await session.execute(total_stmt)
            total = total_result.scalar()

            # По источникам
            source_stmt = select(Lead.source, func.count(Lead.id)).group_by(Lead.source)
            source_result = await session.execute(source_stmt)
            by_source = {row[0]: row[1] for row in source_result.all()}

            # По статусам
            status_stmt = select(Lead.status, func.count(Lead.id)).group_by(Lead.status)
            status_result = await session.execute(status_stmt)
            by_status = {row[0]: row[1] for row in status_result.all()}

            # По score
            hot_stmt = select(func.count(Lead.id)).where(Lead.ai_score >= 9)
            hot_result = await session.execute(hot_stmt)
            hot_count = hot_result.scalar()

            warm_stmt = select(func.count(Lead.id)).where(and_(Lead.ai_score >= 7, Lead.ai_score < 9))
            warm_result = await session.execute(warm_stmt)
            warm_count = warm_result.scalar()

            # За последние 24 часа
            yesterday = datetime.utcnow() - timedelta(hours=24)
            recent_stmt = select(func.count(Lead.id)).where(Lead.parsed_at >= yesterday)
            recent_result = await session.execute(recent_stmt)
            recent_count = recent_result.scalar()

            return {
                "total": total,
                "by_source": by_source,
                "by_status": by_status,
                "hot_count": hot_count,
                "warm_count": warm_count,
                "recent_24h": recent_count,
            }

    async def cleanup_old_leads(self, days: int):
        """Удалить старые лиды (кроме тех что в работе)"""
        async with self.async_session() as session:
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            stmt = select(Lead).where(
                and_(
                    Lead.parsed_at < cutoff_date,
                    Lead.status != 'in_progress'
                )
            )
            result = await session.execute(stmt)
            old_leads = result.scalars().all()

            for lead in old_leads:
                await session.delete(lead)

            await session.commit()
            logger.info(f"Удалено старых лидов: {len(old_leads)}")

    async def get_lead_by_id(self, lead_id: int) -> Optional[Lead]:
        """Получить лид по ID"""
        async with self.async_session() as session:
            stmt = select(Lead).where(Lead.id == lead_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()


# Для тестирования
async def main():
    db = DatabaseManager()
    await db.init_db()
    logger.info("База данных создана успешно")


if __name__ == "__main__":
    asyncio.run(main())
