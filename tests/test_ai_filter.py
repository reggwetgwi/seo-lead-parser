import pytest
import json
from unittest.mock import Mock, patch, AsyncMock
from datetime import datetime

from ai_filter import AIFilter
from database import Lead


@pytest.fixture
def ai_filter():
    """Фикстура для AIFilter"""
    with patch('ai_filter.genai.configure'):
        filter_instance = AIFilter()
        return filter_instance


@pytest.fixture
def sample_lead():
    """Фикстура для тестового лида"""
    lead = Lead(
        id=1,
        source="hh",
        external_id="test_123",
        title="Требуется SEO специалист",
        description="Ищем опытного SEO специалиста для продвижения интернет-магазина. Бюджет 50000 рублей.",
        budget="50000 ₽",
        url="https://example.com/test",
        city="Москва",
        published_at=datetime.utcnow(),
        parsed_at=datetime.utcnow(),
    )
    return lead


def test_build_prompt(ai_filter, sample_lead):
    """Тест построения промпта"""
    prompt = ai_filter.build_prompt(sample_lead)

    assert "SEO специалист" in prompt
    assert "50000 ₽" in prompt
    assert "Москва" in prompt
    assert "hh" in prompt
    assert "ai_score" in prompt
    assert "is_relevant" in prompt


def test_parse_json_response_valid(ai_filter):
    """Тест парсинга корректного JSON"""
    response_text = json.dumps({
        "is_relevant": True,
        "ai_score": 8,
        "urgency": "medium",
        "budget_rub": 50000,
        "contact_info": None,
        "recommended_service": "SEO-продвижение",
        "client_profile": "Интернет-магазин",
        "ai_summary": "Хороший лид с четким ТЗ",
        "tags": ["seo", "интернет-магазин"]
    })

    result = ai_filter.parse_json_response(response_text)

    assert result is not None
    assert result["is_relevant"] is True
    assert result["ai_score"] == 8
    assert result["urgency"] == "medium"
    assert result["recommended_service"] == "SEO-продвижение"
    assert isinstance(result["tags"], str)  # Должно быть преобразовано в JSON строку


def test_parse_json_response_with_markdown(ai_filter):
    """Тест парсинга JSON с markdown блоками"""
    response_text = """```json
{
    "is_relevant": true,
    "ai_score": 9,
    "urgency": "high",
    "budget_rub": 100000,
    "contact_info": "+7 999 123-45-67",
    "recommended_service": "Комплексный маркетинг",
    "client_profile": "Крупная компания",
    "ai_summary": "Отличный лид",
    "tags": ["срочно", "большой бюджет"]
}
```"""

    result = ai_filter.parse_json_response(response_text)

    assert result is not None
    assert result["ai_score"] == 9
    assert result["urgency"] == "high"


def test_parse_json_response_invalid(ai_filter):
    """Тест парсинга некорректного JSON"""
    response_text = "This is not JSON"

    result = ai_filter.parse_json_response(response_text)

    assert result is None


def test_parse_json_response_missing_fields(ai_filter):
    """Тест парсинга JSON с отсутствующими полями"""
    response_text = json.dumps({
        "is_relevant": True,
        "ai_score": 7,
        # Отсутствует urgency
    })

    result = ai_filter.parse_json_response(response_text)

    assert result is None  # Должен вернуть None из-за отсутствия обязательных полей


def test_parse_json_response_invalid_score(ai_filter):
    """Тест парсинга JSON с некорректным score"""
    response_text = json.dumps({
        "is_relevant": True,
        "ai_score": 15,  # Некорректное значение (должно быть 1-10)
        "urgency": "medium",
        "recommended_service": "SEO",
        "ai_summary": "Test"
    })

    result = ai_filter.parse_json_response(response_text)

    assert result is None


def test_parse_json_response_invalid_urgency(ai_filter):
    """Тест парсинга JSON с некорректной срочностью"""
    response_text = json.dumps({
        "is_relevant": True,
        "ai_score": 8,
        "urgency": "super_high",  # Некорректное значение
        "recommended_service": "SEO",
        "ai_summary": "Test"
    })

    result = ai_filter.parse_json_response(response_text)

    assert result is not None
    assert result["urgency"] == "medium"  # Должно быть исправлено на medium


@pytest.mark.asyncio
async def test_score_lead_success(ai_filter, sample_lead):
    """Тест успешной оценки лида"""
    # Мокируем ответ Gemini
    mock_response = Mock()
    mock_response.text = json.dumps({
        "is_relevant": True,
        "ai_score": 8,
        "urgency": "medium",
        "budget_rub": 50000,
        "contact_info": None,
        "recommended_service": "SEO-продвижение",
        "client_profile": "Интернет-магазин",
        "ai_summary": "Хороший лид",
        "tags": ["seo"]
    })

    with patch.object(ai_filter.model, 'generate_content', return_value=mock_response):
        result = await ai_filter.score_lead(sample_lead)

        assert result is not None
        assert result["ai_score"] == 8
        assert result["is_relevant"] is True


@pytest.mark.asyncio
async def test_score_lead_retry(ai_filter, sample_lead):
    """Тест retry логики при ошибке"""
    # Первые 2 попытки - ошибка, третья - успех
    mock_response = Mock()
    mock_response.text = json.dumps({
        "is_relevant": True,
        "ai_score": 7,
        "urgency": "low",
        "recommended_service": "SEO",
        "ai_summary": "Test",
        "client_profile": "Test"
    })

    call_count = 0

    def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise Exception("API Error")
        return mock_response

    with patch.object(ai_filter.model, 'generate_content', side_effect=side_effect):
        result = await ai_filter.score_lead(sample_lead, max_retries=3)

        assert result is not None
        assert call_count == 3


@pytest.mark.asyncio
async def test_score_lead_max_retries_exceeded(ai_filter, sample_lead):
    """Тест превышения максимального количества попыток"""
    with patch.object(ai_filter.model, 'generate_content', side_effect=Exception("API Error")):
        result = await ai_filter.score_lead(sample_lead, max_retries=2)

        assert result is None


@pytest.mark.asyncio
async def test_score_batch(ai_filter):
    """Тест пакетной обработки лидов"""
    # Создать несколько тестовых лидов
    leads = [
        Lead(
            id=i,
            source="hh",
            external_id=f"test_{i}",
            title=f"Лид {i}",
            description="Описание",
            url=f"https://example.com/{i}",
            published_at=datetime.utcnow(),
            parsed_at=datetime.utcnow(),
        )
        for i in range(3)
    ]

    # Мокируем успешный ответ
    mock_response = Mock()
    mock_response.text = json.dumps({
        "is_relevant": True,
        "ai_score": 7,
        "urgency": "medium",
        "recommended_service": "SEO",
        "ai_summary": "Test",
        "client_profile": "Test"
    })

    with patch.object(ai_filter.model, 'generate_content', return_value=mock_response):
        results = await ai_filter.score_batch(leads, max_workers=2)

        assert len(results) == 3
        assert all("lead_id" in r and "ai_data" in r for r in results)


@pytest.mark.asyncio
async def test_score_batch_partial_failure(ai_filter):
    """Тест пакетной обработки с частичными ошибками"""
    leads = [
        Lead(
            id=i,
            source="hh",
            external_id=f"test_{i}",
            title=f"Лид {i}",
            description="Описание",
            url=f"https://example.com/{i}",
            published_at=datetime.utcnow(),
            parsed_at=datetime.utcnow(),
        )
        for i in range(3)
    ]

    call_count = 0

    def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise Exception("API Error")

        mock_response = Mock()
        mock_response.text = json.dumps({
            "is_relevant": True,
            "ai_score": 7,
            "urgency": "medium",
            "recommended_service": "SEO",
            "ai_summary": "Test",
            "client_profile": "Test"
        })
        return mock_response

    with patch.object(ai_filter.model, 'generate_content', side_effect=side_effect):
        results = await ai_filter.score_batch(leads, max_workers=1)

        # Должно быть 2 успешных результата (1 ошибка)
        assert len(results) == 2


@pytest.mark.asyncio
async def test_process_unscored_leads(ai_filter):
    """Тест обработки непроскоренных лидов"""
    # Мокируем методы БД
    mock_leads = [
        Lead(
            id=1,
            source="hh",
            external_id="test_1",
            title="Лид 1",
            description="Описание",
            url="https://example.com/1",
            published_at=datetime.utcnow(),
            parsed_at=datetime.utcnow(),
        )
    ]

    mock_response = Mock()
    mock_response.text = json.dumps({
        "is_relevant": True,
        "ai_score": 8,
        "urgency": "high",
        "recommended_service": "SEO",
        "ai_summary": "Test",
        "client_profile": "Test"
    })

    with patch.object(ai_filter.db, 'get_leads_for_ai', return_value=mock_leads), \
         patch.object(ai_filter.db, 'update_ai_fields', new_callable=AsyncMock), \
         patch.object(ai_filter.model, 'generate_content', return_value=mock_response):

        count = await ai_filter.process_unscored_leads(limit=10)

        assert count == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
