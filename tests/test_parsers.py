import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock
from datetime import datetime

from parsers.base_parser import BaseParser


class TestParser(BaseParser):
    """Тестовый парсер для проверки базового класса"""
    SOURCE_NAME = "test"

    async def parse(self):
        """Реализация абстрактного метода"""
        return [
            {
                "source": self.SOURCE_NAME,
                "external_id": "test_1",
                "title": "Тестовый лид",
                "description": "Описание",
                "url": "https://example.com/1",
                "published_at": datetime.utcnow(),
            }
        ]


@pytest.fixture
def parser():
    """Фикстура для тестового парсера"""
    return TestParser()


def test_user_agents_list(parser):
    """Тест наличия User-Agent списка"""
    assert len(parser.USER_AGENTS) >= 15
    assert all(isinstance(ua, str) for ua in parser.USER_AGENTS)
    assert all("Mozilla" in ua for ua in parser.USER_AGENTS)


def test_get_random_headers(parser):
    """Тест генерации случайных заголовков"""
    headers1 = parser.get_random_headers()
    headers2 = parser.get_random_headers()

    # Проверить обязательные заголовки
    assert "User-Agent" in headers1
    assert "Accept" in headers1
    assert "Accept-Language" in headers1

    # User-Agent должен быть из списка
    assert headers1["User-Agent"] in parser.USER_AGENTS

    # При повторном вызове может быть другой User-Agent (с вероятностью)
    # Проверяем что оба валидны
    assert headers2["User-Agent"] in parser.USER_AGENTS


@pytest.mark.asyncio
async def test_random_delay(parser):
    """Тест случайной задержки"""
    import time

    start = time.time()
    await parser.random_delay(0.1, 0.2)
    elapsed = time.time() - start

    # Задержка должна быть в указанном диапазоне
    assert 0.1 <= elapsed <= 0.3  # Небольшой запас на выполнение


@pytest.mark.asyncio
async def test_fetch_with_retry_success(parser):
    """Тест успешного HTTP запроса"""
    mock_response = Mock()
    mock_response.status = 200
    mock_response.text = AsyncMock(return_value="<html>Test content</html>")

    mock_session = Mock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_response)
    mock_session.__aexit__ = AsyncMock()

    mock_client_session = Mock()
    mock_client_session.get = Mock(return_value=mock_session)
    mock_client_session.__aenter__ = AsyncMock(return_value=mock_client_session)
    mock_client_session.__aexit__ = AsyncMock()

    with patch('parsers.base_parser.aiohttp.ClientSession', return_value=mock_client_session):
        result = await parser.fetch_with_retry("https://example.com")

        assert result == "<html>Test content</html>"


@pytest.mark.asyncio
async def test_fetch_with_retry_failure(parser):
    """Тест HTTP запроса с ошибкой"""
    mock_response = Mock()
    mock_response.status = 404

    mock_session = Mock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_response)
    mock_session.__aexit__ = AsyncMock()

    mock_client_session = Mock()
    mock_client_session.get = Mock(return_value=mock_session)
    mock_client_session.__aenter__ = AsyncMock(return_value=mock_client_session)
    mock_client_session.__aexit__ = AsyncMock()

    with patch('parsers.base_parser.aiohttp.ClientSession', return_value=mock_client_session):
        result = await parser.fetch_with_retry("https://example.com", max_retries=2)

        assert result is None


@pytest.mark.asyncio
async def test_fetch_with_retry_timeout(parser):
    """Тест HTTP запроса с таймаутом"""
    async def timeout_side_effect(*args, **kwargs):
        raise asyncio.TimeoutError()

    mock_client_session = Mock()
    mock_client_session.get = Mock(side_effect=timeout_side_effect)
    mock_client_session.__aenter__ = AsyncMock(return_value=mock_client_session)
    mock_client_session.__aexit__ = AsyncMock()

    with patch('parsers.base_parser.aiohttp.ClientSession', return_value=mock_client_session):
        result = await parser.fetch_with_retry("https://example.com", max_retries=2)

        assert result is None


@pytest.mark.asyncio
async def test_fetch_with_retry_exponential_backoff(parser):
    """Тест exponential backoff при retry"""
    import time

    call_count = 0

    async def failing_request(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise asyncio.TimeoutError()

    mock_client_session = Mock()
    mock_client_session.get = Mock(side_effect=failing_request)
    mock_client_session.__aenter__ = AsyncMock(return_value=mock_client_session)
    mock_client_session.__aexit__ = AsyncMock()

    with patch('parsers.base_parser.aiohttp.ClientSession', return_value=mock_client_session):
        start = time.time()
        result = await parser.fetch_with_retry("https://example.com", max_retries=3)
        elapsed = time.time() - start

        assert result is None
        assert call_count == 3
        # Проверить что были задержки (2^1 + 2^2 = 6 секунд минимум)
        assert elapsed >= 6


@pytest.mark.asyncio
async def test_fetch_with_retry_success_after_failures(parser):
    """Тест успешного запроса после нескольких неудач"""
    call_count = 0

    async def intermittent_failure(*args, **kwargs):
        nonlocal call_count
        call_count += 1

        if call_count < 3:
            raise asyncio.TimeoutError()

        # Третья попытка успешна
        mock_response = Mock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value="Success")

        mock_session = Mock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_response)
        mock_session.__aexit__ = AsyncMock()
        return mock_session

    mock_client_session = Mock()
    mock_client_session.get = Mock(side_effect=intermittent_failure)
    mock_client_session.__aenter__ = AsyncMock(return_value=mock_client_session)
    mock_client_session.__aexit__ = AsyncMock()

    with patch('parsers.base_parser.aiohttp.ClientSession', return_value=mock_client_session):
        result = await parser.fetch_with_retry("https://example.com", max_retries=3)

        assert result == "Success"
        assert call_count == 3


@pytest.mark.asyncio
async def test_run_method(parser):
    """Тест метода run"""
    # Мокируем save_leads_batch
    with patch.object(parser.db, 'save_leads_batch', return_value=(1, 0)):
        saved, duplicates = await parser.run()

        assert saved == 1
        assert duplicates == 0


@pytest.mark.asyncio
async def test_run_method_with_duplicates(parser):
    """Тест метода run с дубликатами"""
    # Мокируем save_leads_batch
    with patch.object(parser.db, 'save_leads_batch', return_value=(3, 2)):
        saved, duplicates = await parser.run()

        assert saved == 3
        assert duplicates == 2


@pytest.mark.asyncio
async def test_run_method_no_leads(parser):
    """Тест метода run когда лиды не найдены"""
    # Переопределить parse для возврата пустого списка
    async def empty_parse():
        return []

    parser.parse = empty_parse

    with patch.object(parser.db, 'save_leads_batch', return_value=(0, 0)):
        saved, duplicates = await parser.run()

        assert saved == 0
        assert duplicates == 0


@pytest.mark.asyncio
async def test_run_method_exception_handling(parser):
    """Тест обработки исключений в методе run"""
    # Переопределить parse для генерации исключения
    async def failing_parse():
        raise Exception("Parse error")

    parser.parse = failing_parse

    with pytest.raises(Exception):
        await parser.run()


def test_source_name(parser):
    """Тест наличия SOURCE_NAME"""
    assert parser.SOURCE_NAME == "test"
    assert isinstance(parser.SOURCE_NAME, str)
    assert len(parser.SOURCE_NAME) > 0


def test_logger_binding(parser):
    """Тест привязки логгера"""
    # Проверить что логгер настроен с контекстом парсера
    assert hasattr(parser, 'logger')


@pytest.mark.asyncio
async def test_parse_is_abstract():
    """Тест что parse() абстрактный метод"""
    # Попытка создать экземпляр BaseParser должна вызвать ошибку
    # из-за абстрактного метода
    with pytest.raises(TypeError):
        BaseParser()


def test_headers_variety(parser):
    """Тест разнообразия User-Agent"""
    # Получить несколько заголовков
    user_agents = set()
    for _ in range(20):
        headers = parser.get_random_headers()
        user_agents.add(headers["User-Agent"])

    # Должно быть несколько разных User-Agent
    assert len(user_agents) > 1


def test_headers_structure(parser):
    """Тест структуры заголовков"""
    headers = parser.get_random_headers()

    # Проверить все обязательные заголовки
    required_headers = [
        "User-Agent",
        "Accept",
        "Accept-Language",
        "Accept-Encoding",
        "DNT",
        "Connection",
        "Upgrade-Insecure-Requests"
    ]

    for header in required_headers:
        assert header in headers
        assert isinstance(headers[header], str)
        assert len(headers[header]) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
