import asyncio
from datetime import datetime
from functools import wraps

from flask import Flask, render_template, request, jsonify
from loguru import logger

from config import settings
from database import DatabaseManager, Lead


app = Flask(__name__)
app.config['SECRET_KEY'] = settings.flask_secret_key

# Database manager
db = DatabaseManager()


def async_route(f):
    """Декоратор для async функций в Flask"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        return asyncio.run(f(*args, **kwargs))
    return wrapper


@app.route('/')
@async_route
async def index():
    """Главная страница - таблица лидов"""
    # Получить параметры фильтров
    page = request.args.get('page', 1, type=int)
    source = request.args.get('source', '')
    status = request.args.get('status', '')
    urgency = request.args.get('urgency', '')
    min_score = request.args.get('min_score', 1, type=int)
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')

    # Построить фильтры
    filters = {}
    if source:
        filters['source'] = source
    if status:
        filters['status'] = status
    if urgency:
        filters['urgency'] = urgency
    if min_score > 1:
        filters['min_score'] = min_score
    if date_from:
        try:
            filters['date_from'] = datetime.fromisoformat(date_from)
        except:
            pass
    if date_to:
        try:
            filters['date_to'] = datetime.fromisoformat(date_to)
        except:
            pass

    # Получить лиды
    leads, total_count = await db.get_leads_paginated(page=page, per_page=50, filters=filters)

    # Пагинация
    total_pages = (total_count + 49) // 50

    return render_template(
        'index.html',
        leads=leads,
        page=page,
        total_pages=total_pages,
        total_count=total_count,
        filters=request.args,
    )


@app.route('/lead/<int:lead_id>')
@async_route
async def lead_detail(lead_id):
    """Карточка лида"""
    lead = await db.get_lead_by_id(lead_id)

    if not lead:
        return "Лид не найден", 404

    # Парсинг тегов
    tags = []
    if lead.tags:
        try:
            import json
            tags = json.loads(lead.tags)
        except:
            pass

    return render_template('lead.html', lead=lead, tags=tags)


@app.route('/lead/<int:lead_id>/status', methods=['POST'])
@async_route
async def update_status(lead_id):
    """Обновить статус лида (AJAX)"""
    try:
        data = request.get_json()
        new_status = data.get('status')

        if new_status not in ['new', 'notified', 'in_progress', 'closed']:
            return jsonify({'success': False, 'error': 'Некорректный статус'}), 400

        await db.update_status(lead_id, new_status)

        return jsonify({'success': True})

    except Exception as e:
        logger.error(f"Ошибка обновления статуса: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/lead/<int:lead_id>/note', methods=['POST'])
@async_route
async def update_note(lead_id):
    """Добавить заметку менеджера (AJAX)"""
    try:
        data = request.get_json()
        note = data.get('note', '')

        # Получить текущий лид для сохранения статуса
        lead = await db.get_lead_by_id(lead_id)
        if not lead:
            return jsonify({'success': False, 'error': 'Лид не найден'}), 404

        await db.update_status(lead_id, lead.status, note=note)

        return jsonify({'success': True})

    except Exception as e:
        logger.error(f"Ошибка обновления заметки: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/stats')
@async_route
async def stats():
    """Страница статистики"""
    from datetime import datetime
    stats_data = await db.get_stats()

    return render_template('stats.html', stats=stats_data, now=datetime.now())


@app.route('/api/leads')
@async_route
async def api_leads():
    """JSON API для лидов"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)

    leads, total_count = await db.get_leads_paginated(page=page, per_page=per_page)

    # Сериализация
    leads_data = []
    for lead in leads:
        leads_data.append({
            'id': lead.id,
            'source': lead.source,
            'title': lead.title,
            'description': lead.description[:200],
            'budget': lead.budget,
            'url': lead.url,
            'city': lead.city,
            'ai_score': lead.ai_score,
            'urgency': lead.urgency,
            'status': lead.status,
            'published_at': lead.published_at.isoformat() if lead.published_at else None,
        })

    return jsonify({
        'leads': leads_data,
        'total': total_count,
        'page': page,
        'per_page': per_page,
    })


@app.route('/api/stats')
@async_route
async def api_stats():
    """JSON API для статистики"""
    stats_data = await db.get_stats()
    return jsonify(stats_data)


if __name__ == '__main__':
    app.run(debug=True, port=settings.flask_port)
