from app.ai import planner_vacancy_context
from app.models import Vacancy


def test_planner_keeps_requirements_and_contact_after_first_eight_lines():
    vacancy = Vacancy(id=1, title='Склад', description='', location='Kraków', salary='23 зл/год',
        student_salary='31,40 зл/год', schedule='2 зміни', contract_type='Umowa zlecenie',
        housing='650 зл/міс', transport='Надається', requirements='Права UDT', contact='+48 577 017 187')
    lines, included, excluded = planner_vacancy_context([vacancy])
    assert 'Права UDT' in lines[0]
    assert '+48 577 017 187' in lines[0]
    assert included == {'1': 1} and excluded == []


def test_planner_omits_whole_oversized_vacancy_not_individual_conditions():
    large = Vacancy(id=1, title='Large', description='', requirements='x' * 1000)
    small = Vacancy(id=2, title='Small', description='', salary='23 зл/год')
    lines, included, excluded = planner_vacancy_context([large, small], character_budget=200)
    assert included == {'2': 2}
    assert excluded == [1]
    assert len(lines) == 1 and 'Large' not in lines[0]
