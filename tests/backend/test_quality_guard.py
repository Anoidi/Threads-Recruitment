import pytest


@pytest.mark.asyncio
async def test_quality_guard_reports_incomplete_vacancy_and_invalid_ai_draft(client):
    vacancy = await client.post('/api/vacancies', json={'title': 'Склад', 'description': 'Оголошення', 'location': 'Не вказано в оголошенні', 'salary': None, 'language': 'uk'})
    assert vacancy.status_code == 201
    result = await client.get('/api/threads/quality-guard')
    assert result.status_code == 200
    findings = result.json()['findings']
    assert any(item['entity_type'] == 'vacancy' and item['entity_id'] == vacancy.json()['id'] for item in findings)
    assert result.json()['summary']['mutations'] == 0
