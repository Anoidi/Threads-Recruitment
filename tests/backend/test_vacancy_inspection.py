import pytest


@pytest.mark.asyncio
async def test_vacancy_inspection_exposes_both_rates_and_missing_fields(client):
    vacancy = await client.post('/api/vacancies', json={
        'title': 'Склад', 'description': '📍 Місце роботи: Kraków\n💰 Ставка: 25 zł/год\n🎓 Для студентів: 32 zł/год',
        'location': 'Не вказано в оголошенні', 'salary': '25 zł/год', 'student_salary': '32 zł/год для студентів',
        'schedule': '', 'contract_type': None, 'language': 'uk',
    })
    assert vacancy.status_code == 201
    item = await client.get(f"/api/vacancies/{vacancy.json()['id']}/inspection")
    assert item.status_code == 200
    payload = item.json()
    assert payload['status'] == 'NEEDS_REVIEW'
    assert len(payload['rates']) == 2
    assert 'Не указан график' in payload['issues']


@pytest.mark.asyncio
async def test_repair_preview_has_source_hash_and_selected_repair(client):
    vacancy = await client.post('/api/vacancies', json={
        'title': 'Оператор', 'description': '📍 Місце роботи: Wiechlice\nСтавка: 25 zł/год',
        'location': 'Не вказано в оголошенні', 'salary': '25 zł/год', 'language': 'uk',
    })
    vid = vacancy.json()['id']
    preview = await client.get(f'/api/vacancies/{vid}/repair-preview')
    assert preview.status_code == 200
    data = preview.json()
    assert data['source_hash'] and any(change['field'] == 'location' for change in data['changes'])
    repaired = await client.post(f'/api/vacancies/{vid}/repair', json={'source_hash': data['source_hash'], 'fields': ['location']})
    assert repaired.status_code == 200
    assert repaired.json()['fields']['location']['value'] == 'Wiechlice'
