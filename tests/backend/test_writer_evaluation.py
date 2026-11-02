import importlib.util
from pathlib import Path
import pytest


@pytest.fixture
def gate():
    path = Path(__file__).resolve().parents[2] / 'scripts' / 'evaluate_writer_quality.py'
    spec = importlib.util.spec_from_file_location('writer_probe', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.shape_failures


def test_probe_rejects_reasoning_and_truncation(gate):
    failures = gate('Okay, I need to write a post', 'length', 'uk')
    assert 'reasoning_in_post' in failures
    assert 'not_completed' in failures


def test_probe_distinguishes_shape_from_native_quality(gate):
    assert gate('Уточніть вартість житла перед виїздом.', 'stop', 'uk') == []
    assert 'empty' in gate('', 'stop', 'pl')
    assert 'mixed_alphabet' in gate('Zapytaj про grafik', 'stop', 'pl')
