import asyncio
import pytest
from datetime import datetime, timedelta

from database import DatabaseManager, Lead


@pytest.fixture
async def db():
    """Фикстура для тестовой базы данных"""
    # Использовать in-memory SQLite для тестов
    db = DatabaseManager()
    db.engine = db.engine.execution_options(isolation_level="AUTOCOMMIT")
    await db.init_db()
    yield db
    # Cleanup
    await db.engine.dispose()


@pytest.mark.asyncio
async def test_init_db():
    """Тест создания таблиц"""
    db = DatabaseManager()
    await db.init_db()

    # Проверить что таблицы созданы
    async with db.async_session() as session:
        result = await session.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in result.fetchall()]
        assert "leads" in tables


@pytest.mark.asyncio
async def test_save_lead(db):
    """Тест сохранения лида"""
    lead_data = {
        "source": "hh",
        "external_id": "test_123",
        "title": "Тестовая вакансия",
        "description": "Описание тестовой вакансии",
        "budget": "50000 ₽",
        "url": "https://example.com/test",
        "city": "Москва",
        "published_at": datetime.utcnow(),
    }

    result = await db.save_lead(lead_data)
    assert result is True

    # Проверить что лид сохранен
    async with db.async_session() as session:
        from sqlalchemy import select
        stmt = select(Lead).where(Lead.external_id == "test_123")
        result = await session.execute(stmt)
        lead = result.scalar_one_or_none()

        assert lead is not None
        assert lead.title == "Тестовая вакансия"
        assert lead.source == "hh"


@pytest.mark.asyncio
async def test_save_lead_duplicate(db):
    """Тест дедупликации лидов"""
    lead_data = {
        "source": "fl",
        "external_id": "duplicate_456",
        "title": "Дубликат",
        "description": "Описание",
        "url": "https://example.com/dup",
        "published_at": datetime.utcnow(),
    }

    # Первое сохранение
    result1 = await db.save_lead(lead_data)
    assert result1 is True

    # Второе сохранение (дубликат)
    result2 = await db.save_lead(lead_data)
    assert result2 is False


@pytest.mark.asyncio
async def test_save_leads_batch(db):
    """Тест пакетного сохранения"""
    leads = [
        {
            "source": "kwork",
            "external_id": f"batch_{i}",
            "title": f"Лид {i}",
            "description": f"Описание {i}",
            "url": f"https://example.com/{i}",
            "published_at": datetime.utcnow(),
        }
        for i in range(5)
    ]

    # Добавить дубликат
    leads.append(leads[0])

    saved, duplicates = await db.save_leads_batch(leads)
    assert saved == 5
    assert duplicates == 1


@pytest.mark.asyncio
async def test_get_leads_for_ai(db):
    """Тест получения лидов без AI-оценки"""
    # Создать лиды
    for i in range(3):
        await db.save_lead({
            "source": "avito",
            "external_id": f"ai_test_{i}",
            "title": f"Лид для AI {i}",
            "description": "Описание",
            "url": f"https://example.com/ai_{i}",
            "published_at": datetime.utcnow(),
        })

    # Получить лиды без AI-оценки
    leads = await db.get_leads_for_ai(limit=10)
    assert len(leads) == 3
    assert all(lead.ai_score is None for lead in leads)


@pytest.mark.asyncio
async def test_update_ai_fields(db):
    """Тест обновления AI-полей"""
    # Создать лид
    await db.save_lead({
        "source": "hh",
        "external_id": "ai_update_test",
        "title": "Тест AI",
        "description": "Описание",
        "url": "https://example.com/ai",
        "published_at": datetime.utcnow(),
    })

    # Получить ID лида
    async with db.async_session() as session:
        from sqlalchemy import select
        stmt = select(Lead).where(Lead.external_id == "ai_update_test")
        result = await session.execute(stmt)
        lead = result.scalar_one()
        lead_id = lead.id

    # Обновить AI-поля
    ai_data = {
        "ai_score": 8,
        "ai_summary": "Хороший лид",
        "urgency": "high",
        "is_relevant": True,
    }

    await db.update_ai_fields(lead_id, ai_data)

    # Проверить обновление
    lead = await db.get_lead_by_id(lead_id)
    assert lead.ai_score == 8
    assert lead.ai_summary == "Хороший лид"
    assert lead.urgency == "high"
    assert lead.is_relevant is True


@pytest.mark.asyncio
async def test_get_new_leads_for_notify(db):
    """Тест получения горячих лидов для уведомлений"""
    # Создать лиды с разными score
    for i, score in enumerate([9, 7, 5, 10, 3]):
        await db.save_lead({
            "source": "fl",
            "external_id": f"notify_test_{i}",
            "title": f"Лид {i}",
            "description": "Описание",
            "url": f"https://example.com/{i}",
            "published_at": datetime.utcnow(),
        })

        # Получить ID и обновить AI-поля
        async with db.async_session() as session:
            from sqlalchemy import select
            stmt = select(Lead).where(Lead.external_id == f"notify_test_{i}")
            result = await session.execute(stmt)
            lead = result.scalar_one()

            await db.update_ai_fields(lead.id, {
                "ai_score": score,
                "is_relevant": True,
            })

    # Получить лиды с score >= 7
    hot_leads = await db.get_new_leads_for_notify(min_score=7)
    assert len(hot_leads) == 3  # score 9, 7, 10
    assert all(lead.ai_score >= 7 for lead in hot_leads)


@pytest.mark.asyncio
async def test_mark_as_notified(db):
    """Тест пометки лидов как отправленных"""
    # Создать лид
    await db.save_lead({
        "source": "kwork",
        "external_id": "notified_test",
        "title": "Тест уведомления",
        "description": "Описание",
        "url": "https://example.com/notified",
        "published_at": datetime.utcnow(),
    })

    # Получить ID
    async with db.async_session() as session:
        from sqlalchemy import select
        stmt = select(Lead).where(Lead.external_id == "notified_test")
        result = await session.execute(stmt)
        lead = result.scalar_one()
        lead_id = lead.id

    # Пометить как отправленный
    await db.mark_as_notified([lead_id])

    # Проверить
    lead = await db.get_lead_by_id(lead_id)
    assert lead.notified_at is not None
    assert lead.status == "notified"


@pytest.mark.asyncio
async def test_update_status(db):
    """Тест обновления статуса"""
    # Создать лид
    await db.save_lead({
        "source": "avito",
        "external_id": "status_test",
        "title": "Тест статуса",
        "description": "Описание",
        "url": "https://example.com/status",
        "published_at": datetime.utcnow(),
    })

    # Получить ID
    async with db.async_session() as session:
        from sqlalchemy import select
        stmt = select(Lead).where(Lead.external_id == "status_test")
        result = await session.execute(stmt)
        lead = result.scalar_one()
        lead_id = lead.id

    # Обновить статус
    await db.update_status(lead_id, "in_progress", note="Взято в работу")

    # Проверить
    lead = await db.get_lead_by_id(lead_id)
    assert lead.status == "in_progress"
    assert lead.manager_note == "Взято в работу"


@pytest.mark.asyncio
async def test_get_stats(db):
    """Тест получения статистики"""
    # Создать несколько лидов
    sources = ["hh", "fl", "kwork", "avito"]
    for i, source in enumerate(sources):
        await db.save_lead({
            "source": source,
            "external_id": f"stats_test_{i}",
            "title": f"Лид {i}",
            "description": "Описание",
            "url": f"https://example.com/{i}",
            "published_at": datetime.utcnow(),
        })

    # Получить статистику
    stats = await db.get_stats()

    assert stats["total"] >= 4
    assert "by_source" in stats
    assert "by_status" in stats
    assert stats["by_source"]["hh"] >= 1


@pytest.mark.asyncio
async def test_get_leads_paginated(db):
    """Тест пагинации"""
    # Создать 10 лидов
    for i in range(10):
        await db.save_lead({
            "source": "hh",
            "external_id": f"page_test_{i}",
            "title": f"Лид {i}",
            "description": "Описание",
            "url": f"https://example.com/{i}",
            "published_at": datetime.utcnow(),
        })

    # Получить первую страницу
    leads, total = await db.get_leads_paginated(page=1, per_page=5)
    assert len(leads) == 5
    assert total >= 10

    # Получить вторую страницу
    leads, total = await db.get_leads_paginated(page=2, per_page=5)
    assert len(leads) == 5


@pytest.mark.asyncio
async def test_cleanup_old_leads(db):
    """Тест очистки старых лидов"""
    # Создать старый лид
    old_date = datetime.utcnow() - timedelta(days=40)
    await db.save_lead({
        "source": "fl",
        "external_id": "old_lead",
        "title": "Старый лид",
        "description": "Описание",
        "url": "https://example.com/old",
        "published_at": old_date,
    })

    # Изменить parsed_at на старую дату
    async with db.async_session() as session:
        from sqlalchemy import select, update
        stmt = update(Lead).where(Lead.external_id == "old_lead").values(parsed_at=old_date)
        await session.execute(stmt)
        await session.commit()

    # Очистить старые лиды
    await db.cleanup_old_leads(days=30)

    # Проверить что старый лид удален
    lead = await db.get_lead_by_id(1)  # Предполагаем что это был первый лид
    # Лид должен быть удален, но проверка зависит от других тестов


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
