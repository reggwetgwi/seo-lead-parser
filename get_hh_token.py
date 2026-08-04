"""
Скрипт для получения OAuth токена HH.ru
"""
import asyncio
import aiohttp
from config import settings


async def get_access_token():
    """Получить access token через client credentials flow"""

    if not settings.hh_client_id or not settings.hh_client_secret:
        print("❌ Ошибка: HH_CLIENT_ID и HH_CLIENT_SECRET не настроены в .env файле")
        print("\nДля получения credentials:")
        print("1. Зарегистрируйте приложение на https://dev.hh.ru/admin")
        print("2. Скопируйте Client ID и Client Secret в .env файл")
        return

    oauth_url = "https://hh.ru/oauth/token"

    data = {
        "grant_type": "client_credentials",
        "client_id": settings.hh_client_id,
        "client_secret": settings.hh_client_secret,
    }

    print(f"🔄 Запрос токена для Client ID: {settings.hh_client_id}")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(oauth_url, data=data, timeout=aiohttp.ClientTimeout(total=30)) as response:
                if response.status == 200:
                    result = await response.json()
                    access_token = result.get("access_token")
                    expires_in = result.get("expires_in", 0)

                    print(f"\n✅ Токен успешно получен!")
                    print(f"📝 Access Token: {access_token}")
                    print(f"⏰ Истекает через: {expires_in} секунд ({expires_in // 3600} часов)")
                    print(f"\n📋 Добавьте в .env файл:")
                    print(f"HH_ACCESS_TOKEN={access_token}")

                else:
                    error_text = await response.text()
                    print(f"\n❌ Ошибка {response.status}: {error_text}")

                    if response.status == 400:
                        print("\n💡 Возможные причины:")
                        print("- Неверный Client ID или Client Secret")
                        print("- Приложение не активировано на dev.hh.ru")
                    elif response.status == 403:
                        print("\n💡 Доступ запрещен. Проверьте:")
                        print("- Приложение должно быть типа 'Сервисное приложение'")
                        print("- У приложения должны быть права на чтение вакансий")

    except Exception as e:
        print(f"\n❌ Ошибка запроса: {e}")


async def test_token():
    """Протестировать существующий токен"""

    if not settings.hh_access_token:
        print("❌ HH_ACCESS_TOKEN не настроен в .env файле")
        return

    api_url = "https://api.hh.ru/vacancies"
    headers = {
        "Authorization": f"Bearer {settings.hh_access_token}",
        "User-Agent": settings.hh_app_name,
    }

    params = {
        "text": "SEO",
        "area": 113,
        "per_page": 1,
    }

    print(f"🔄 Тестирование токена...")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as response:
                if response.status == 200:
                    data = await response.json()
                    found = data.get("found", 0)
                    print(f"\n✅ Токен работает!")
                    print(f"📊 Найдено вакансий по запросу 'SEO': {found}")
                else:
                    error_text = await response.text()
                    print(f"\n❌ Ошибка {response.status}: {error_text}")

                    if response.status == 403:
                        print("\n💡 Токен истек или недействителен. Получите новый токен.")

    except Exception as e:
        print(f"\n❌ Ошибка запроса: {e}")


async def main():
    print("=" * 60)
    print("HH.ru OAuth Token Manager")
    print("=" * 60)

    print("\nВыберите действие:")
    print("1. Получить новый access token")
    print("2. Протестировать существующий токен")

    choice = input("\nВведите номер (1 или 2): ").strip()

    if choice == "1":
        await get_access_token()
    elif choice == "2":
        await test_token()
    else:
        print("❌ Неверный выбор")


if __name__ == "__main__":
    asyncio.run(main())
