import pytest

from app.content_quality import extract_vacancy_fields, vacancy_fact_lines, render_vacancy_post, vacancy_post_part_count


@pytest.mark.parametrize('kind', ['LEGAL_INFORMATION', 'DOCUMENTS_INFORMATION', 'CANDIDATE_STORY', 'RECRUITER_STORY'])
def test_sensitive_editorial_types_require_human_review(kind):
    from app.content_quality import assess_post
    result = assess_post('Достаточно длинный текст для проверки автоматического одобрения. ' * 3, kind)
    assert not result.approved
    assert any('проверка оператором' in reason for reason in result.reasons)


def test_rate_eligibility_before_colon_is_not_discarded():
    fields = extract_vacancy_fields('Ставка: 23 зл/год\nСтуденти до 26 років: 31,40 зл/год netto')
    assert fields['salary'] == '23 зл/год'
    assert 'до 26 років' in fields['student_salary']
    assert '31,40 зл/год netto' in fields['student_salary']


def test_rate_basis_in_heading_is_preserved_without_leaking_to_next_block():
    fields = extract_vacancy_fields('Ставка netto:\n25 зл/год\n29 зл/год\nСтуденти:\n31,40 зл/год')
    assert fields['salary'] == '25 зл/год netto; 29 зл/год netto'
    assert fields['student_salary'] == '31,40 зл/год'


def test_inline_netto_heading_preserves_pay_basis():
    fields = extract_vacancy_fields('💰 Ставка netto: 23 зл/год')
    assert '23 зл/год netto' in fields['salary']


def test_netto_context_ends_at_non_pay_condition_block():
    fields = extract_vacancy_fields('Ставка netto:\n23 зл/год\nГрафік: 2 зміни\n28 зл/год')
    assert fields['salary'] == '23 зл/год netto; 28 зл/год'


def test_schedule_numbers_are_not_contact_phone():
    source = 'Графік: 06-18 08-20 6\nСтавка: 23 зл/год'
    assert 'phone' not in extract_vacancy_fields(source)
    fields = extract_vacancy_fields(source + '\nТелефон: +48 577 017 187')
    assert fields['phone'] == '+48 577 017 187'


def test_local_phone_and_international_phone_are_preserved():
    assert extract_vacancy_fields('Контакт: 577-017-187')['phone'] == '577-017-187'
    assert extract_vacancy_fields('WhatsApp: +380 67 123 45 67')['phone'] == '+380 67 123 45 67'
from app.models import Vacancy
from worker.main import _create_telegram_post_draft


VACANCY_6_SALARY_BLOCK = """\
💰 Ставка netto:
• звичайний працівник — 25,50 zł/год
• працівник з правами UDT — 29 zł/год

🎓 Для студентів:
• без UDT — 33 zł/год
• з правами UDT — 35 zł/год
"""

VACANCY_9_SALARY_BLOCK = """\
💰 Ставка netto: 23 зл/год
🎓 Студенти: 31,40 зл/год netto
"""

VACANCY_10_SALARY_BLOCK = """\
💰 Ставка:
• 25 зл/год
• 32 зл/год — для студентів
"""


def make_vacancy(**overrides) -> Vacancy:
    values = {
        "title": "Працівник складу",
        "description": "Вакансія на складі",
        "location": "Kraków",
        "currency": "PLN",
        "language": "uk",
        "source": "telegram",
    }
    values.update(overrides)
    return Vacancy(**values)


def test_long_conditions_are_not_silently_dropped():
    vacancy = make_vacancy(salary='23 зл/год', student_salary='31,40 зл/год',
        housing='Умови проживання ' * 40)
    with pytest.raises(ValueError, match='dropping rates or conditions'):
        render_vacancy_post(vacancy)


def test_long_vacancy_reports_required_review_part_count():
    vacancy = make_vacancy(salary='23 зл/год', housing='Житло ' * 80)
    assert vacancy_post_part_count(vacancy) == 2


def test_requirements_are_present_in_new_vacancy_post():
    text = render_vacancy_post(make_vacancy(salary='23 зл/год', requirements='Потрібні права UDT'))
    assert 'Потрібні права UDT' in text


def test_ukrainian_transport_label_is_preserved_in_new_vacancy_post():
    vacancy = make_vacancy(
        salary='23 зл/год',
        description='📍 Місто: Краків\n🚐 Доїзд: безкоштовний транспорт до роботи з хостелу',
    )
    text = render_vacancy_post(vacancy)
    assert 'безкоштовний транспорт до роботи з хостелу' in text
    assert len(text) <= 500


def test_hourly_rates_exclude_age_housing_schedule_phone_and_productivity():
    source = """Вік: 18–45 років
Ставка: 23 зл/год
Житло: хостел, 650 зл/міс.
Продуктивність: приблизно 60 піків/год.
Студенти: 31,40 зл/год netto
Графік: 6 днів, по 8–10–12 годин
Перерва: 25 хвилин
Телефон: +48 577 017 187"""
    fields = extract_vacancy_fields(source)
    assert fields['salary'] == '23 зл/год'
    assert fields['student_salary'] == '31,40 зл/год netto'


def test_new_draft_ignores_polluted_legacy_salary_without_mutating_vacancy():
    vacancy = make_vacancy(
        title='ВАКАНСІЯ: Працівниця складу книг',
        description='Ставка: 23 зл/год\nСтуденти: 31,40 зл/год netto',
        salary='18–45 років; 23 зл/год; хостел 650 зл/міс.; +48 577 017 187',
        student_salary='31,40 зл/год netto; 6 днів; 25 хвилин',
        contract_type='Umowa zlecenie',
    )
    original = vacancy.salary
    result = render_vacancy_post(vacancy)
    assert '23 зл/год' in result and '31,40 зл/год netto' in result
    assert '18–45' not in result and '25 хвилин' not in result
    assert 'Оформление' not in result and 'Umowa zlecenie' in result
    assert result.count('Працівниця складу книг') == 1
    assert vacancy.salary == original


def test_new_draft_does_not_repeat_source_labels_or_emojis():
    vacancy = make_vacancy(
        title='ВАКАНСІЯ: Працівниця складу книг, канцелярії та іграшок 📚✏️🎨',
        description=(
            '💰 Ставка netto: 23 зл/год\n'
            '🎓 Студенти: 31,40 зл/год netto\n'
            '📄 Договір: umowa zlecenie\n'
            '☎️ Телефон: +48 577 017 187'
        ),
        salary=None,
        student_salary=None,
        contract_type=None,
        contact='@rob_Serv',
    )
    result = render_vacancy_post(vacancy)
    assert 'Оплата: 23 зл/год' in result
    assert 'Для студентів: 31,40 зл/год netto' in result
    assert 'Оплата: Ставка' not in result
    assert 'Для студентів: Студенти' not in result
    assert 'Договір: umowa zlecenie' in result
    assert '+48 577 017 187' in result
    assert '@rob_Serv' not in result


def test_oversized_critical_facts_require_review_instead_of_truncation():
    vacancy = make_vacancy(title='Назва ' * 120, salary='23 зл/год', student_salary='31,40 зл/год')
    with pytest.raises(ValueError, match='split or review'):
        render_vacancy_post(vacancy)


@pytest.mark.parametrize(
    ("source", "regular_rates", "student_rates"),
    [
        pytest.param(
            VACANCY_6_SALARY_BLOCK,
            ("25,50", "29"),
            ("33", "35"),
            id="vacancy-6-four-rates",
        ),
        pytest.param(
            VACANCY_9_SALARY_BLOCK,
            ("23",),
            ("31,40",),
            id="vacancy-9-inline-student-rate",
        ),
        pytest.param(
            VACANCY_10_SALARY_BLOCK,
            ("25",),
            ("32",),
            id="vacancy-10-multiline-rate-list",
        ),
    ],
)
def test_extract_vacancy_fields_preserves_every_regular_and_student_rate(
    source: str,
    regular_rates: tuple[str, ...],
    student_rates: tuple[str, ...],
):
    fields = extract_vacancy_fields(source)

    regular = fields.get("salary", "")
    student = fields.get("student_salary", "")
    for rate in regular_rates:
        assert rate in regular
    for rate in student_rates:
        assert rate in student


def test_extract_vacancy_fields_detects_ukrainian_workplace_and_student_line():
    fields = extract_vacancy_fields("""📍 Місце роботи: Energetyczna 12, 54-530 Krzyżowice
💰 Ставка:
• 25 зл/год
• 32 зл/год — для студентів""")
    assert "Krzyżowice" in fields["location"]
    assert fields["salary"] == "25 зл/год"
    assert fields["student_salary"] == "32 зл/год — для студентів"


def test_vacancy_facts_restore_exact_umowa_zlecenie_from_source():
    vacancy = make_vacancy(
        description="📄 Договір: Umowa zlecenie",
        contract_type="умова залогу",
    )

    facts = vacancy_fact_lines(vacancy)

    assert any("Umowa zlecenie" in fact for fact in facts)
    assert all("умова залогу" not in fact.lower() for fact in facts)


def test_vacancy_facts_remove_telegram_coordinates_and_use_phone():
    vacancy = make_vacancy(
        description=(
            "Контакт: @rob_Serv\n"
            "Telegram: https://t.me/saltgroup_work\n"
            "Телефон: +48 577 017 187"
        ),
        contact="@rob_Serv",
        source_url="https://t.me/saltgroup_work/159",
    )

    rendered = "\n".join(vacancy_fact_lines(vacancy))

    assert "+48 577 017 187" in rendered
    assert "@rob_Serv" not in rendered
    assert "t.me/" not in rendered
    assert "Telegram" not in rendered


@pytest.mark.asyncio
async def test_telegram_draft_uses_phone_cta_and_removes_telegram_coordinates(db):
    source = (
        "📦 Працівник складу\n"
        "📍 Місто: Краків\n"
        "💰 Ставка: 23 zł/год\n"
        "📄 Договір: Umowa zlecenie\n"
        "📲 Telegram: @rob_Serv\n"
        "Джерело: https://t.me/saltgroup_work/159\n"
        "☎️ Телефон: +48 577 017 187"
    )
    vacancy = make_vacancy(
        description=source,
        salary="23 zł/год",
        contract_type="Umowa zlecenie",
        contact="@rob_Serv",
        source_url="https://t.me/saltgroup_work/159",
    )
    db.add(vacancy)
    await db.flush()

    post = await _create_telegram_post_draft(db, vacancy, source)

    assert "+48 577 017 187" in post.content
    assert "@rob_Serv" not in post.content
    assert "t.me/" not in post.content
    assert "Telegram" not in post.content


@pytest.mark.asyncio
async def test_telegram_draft_uses_private_message_cta_when_phone_is_absent(db):
    source = (
        "📦 Працівник складу\n"
        "📍 Місто: Краків\n"
        "💰 Ставка: 23 zł/год\n"
        "📄 Договір: Umowa zlecenie\n"
        "📲 Telegram: @rob_Serv"
    )
    vacancy = make_vacancy(
        description=source,
        salary="23 zł/год",
        contract_type="Umowa zlecenie",
        contact="@rob_Serv",
    )
    db.add(vacancy)
    await db.flush()

    post = await _create_telegram_post_draft(db, vacancy, source)

    assert "Напишіть у приватні повідомлення" in post.content
    assert "@rob_Serv" not in post.content


@pytest.mark.asyncio
async def test_telegram_draft_renderer_is_deterministic_and_keeps_all_salary_rates(db):
    source = (
        "🚗 MINTH POLAND\n"
        "📍 Місто: Wiechlice\n"
        f"{VACANCY_6_SALARY_BLOCK}"
        "📄 Договір: Umowa zlecenie\n"
        "📲 Telegram: @rob_Serv\n"
        "☎️ Телефон: +48 577 017 187\n"
        + ("Обов’язки: пакування та комплектація. " * 20)
    )
    vacancies = [
        make_vacancy(
            title="MINTH POLAND",
            description=source,
            location="Wiechlice",
            contract_type="Umowa zlecenie",
            contact="@rob_Serv",
        )
        for _ in range(2)
    ]
    db.add_all(vacancies)
    await db.flush()

    first = await _create_telegram_post_draft(db, vacancies[0], source)
    second = await _create_telegram_post_draft(db, vacancies[1], source)

    assert first.content == second.content
    assert len(first.content) <= 500
    for rate in ("25,50", "29", "33", "35"):
        assert rate in first.content
    assert "Umowa zlecenie" in first.content
    assert "+48 577 017 187" in first.content
    assert "@rob_Serv" not in first.content
    assert "t.me/" not in first.content
