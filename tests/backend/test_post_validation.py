import pytest
from fastapi import HTTPException
from app.post_validation import validate_generated_post, validate_post_for_publication


def draft(content):
    return dict(hook='Питання перед виходом на роботу', content=content, reasoning_summary='Source facts used')


@pytest.mark.parametrize('content', ['Напишіть @rob_serv', 'Зв’язок t.me/rob_serv', 'Telegram контакт', 'x' * 501, 'Заголовок\nЗаголовок'])
def test_invalid_generation_is_rejected_not_silently_published(content):
    with pytest.raises(HTTPException):
        validate_generated_post(draft(content))


def test_punctuation_cleanup_preserves_rates_and_input():
    original = draft('Оплата — 23 зл/год, студентам 31,40 зл/год netto.')
    checked = validate_generated_post(original)
    assert '—' not in checked['content']
    assert '23 зл/год' in checked['content'] and '31,40 зл/год netto' in checked['content']
    assert '—' in original['content']


@pytest.mark.parametrize('hook', ['Пиши @rob_serv', 'Telegram', 't.me/rob_serv', 'x' * 501])
def test_hook_cannot_bypass_contact_and_length_validation(hook):
    value = draft('Уточніть вартість житла та кількість людей у кімнаті.')
    value['hook'] = hook
    with pytest.raises(HTTPException):
        validate_generated_post(value)


def test_mixed_address_across_hook_and_body_is_rejected():
    value = draft('Попросіть фото й адресу. Так ви зможете оцінити житло.')
    value['hook'] = 'Перед виїздом запитай про житло'
    with pytest.raises(HTTPException, match='mixes informal'):
        validate_generated_post(value)


def test_quoted_informal_speech_does_not_trigger_mixed_address():
    value = draft('Якщо вам кажуть «ти маєш погодитися», уточніть умови письмово.')
    assert validate_generated_post(value)['content'] == value['content']


def test_hook_is_actual_opening_not_independent_model_slogan():
    value = draft('Перед виїздом уточніть вартість житла. Попросіть фото й адресу.')
    value['hook'] = 'Не варто робити все на волі'
    checked = validate_generated_post(value)
    assert checked['hook'] == 'Перед виїздом уточніть вартість житла.'
    assert checked['content'] == value['content']


def test_hook_derivation_preserves_decimal_rates():
    value = draft('Студентам 31.40 зл/год netto. Уточніть умови.')
    assert validate_generated_post(value)['hook'] == 'Студентам 31.40 зл/год netto.'


def test_polish_draft_rejects_mixed_alphabet_but_accepts_polish_letters():
    value = draft('Utočnij warunki pracy.')
    value.update(language='pl', hook='Grafik pracy')
    with pytest.raises(HTTPException, match='non-Polish'):
        validate_generated_post(value)
    value['content'] = 'Poproś o potwierdzenie ustaleń na piśmie. Zapytaj o długość zmiany.'
    assert validate_generated_post(value)['content'] == value['content']


def test_all_english_answer_is_rejected_for_ukrainian_request():
    value = draft('Ask about housing before departure.')
    value['language'] = 'uk'
    with pytest.raises(HTTPException, match='no Cyrillic'):
        validate_generated_post(value)


def test_ukrainian_rejects_russian_fallback_wording():
    value = draft('В обсуждениях соискателей часто появляется тема. Перед откликом сравните стоимость жилья. Напишите первым.')
    value['language'] = 'uk'
    with pytest.raises(HTTPException, match='Russian wording'):
        validate_generated_post(value)


def test_location_name_skawina_is_not_treated_as_na_marker():
    content = 'Працівники виробництва\n📍 Skawina / Kopanka\nОплата: 23 зл/год netto'
    assert validate_post_for_publication(content=content, language='uk', content_type='VACANCY') == content


@pytest.mark.asyncio
async def test_retry_receives_validation_diagnostic(monkeypatch):
    from app.ai import AIService
    from app.config import settings
    monkeypatch.setattr(settings, 'ai_retries', 1)
    requests = []
    async def chat(self, route, prompt, max_tokens, json_mode=False, timeout=None):
        requests.append(prompt)
        return ('bad' if len(requests) == 1 else 'good', 'stop')
    monkeypatch.setattr(AIService, '_chat', chat)
    def check(value):
        if value == 'bad':
            raise HTTPException(502, 'Mixed address')
        return value
    assert await AIService()._request('free-default', 'Original task', 100, validate=check) == 'good'
    assert requests[0] == 'Original task'
    assert 'Mixed address' in requests[1]
