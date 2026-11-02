from pathlib import Path
import pytest
from app.comfyui import load_workflow
from app.config import settings


@pytest.mark.parametrize('width,height', [(512, 512), (768, 432), (432, 768)])
def test_bundled_image_workflow_renders_numeric_dimensions_and_safe_batch(monkeypatch, width, height):
    monkeypatch.setattr(settings, 'comfyui_workflow_dir', str(Path(__file__).resolve().parents[2] / 'workflows'))
    monkeypatch.setattr(settings, 'image_checkpoint', 'test-sd15.safetensors')
    workflow = load_workflow('image-sd15-api.json', prompt='Warehouse scene', negative_prompt='text',
        width=width, height=height, seed=42, output_prefix='smoke')
    assert workflow['1']['inputs']['ckpt_name'] == 'test-sd15.safetensors'
    assert workflow['4']['inputs'] == {'width': width, 'height': height, 'batch_size': 1}
    assert workflow['5']['inputs']['seed'] == 42
    assert workflow['5']['inputs']['latent_image'] == ['4', 0]
    assert workflow['7']['inputs']['images'] == ['6', 0]
    assert all('{{' not in str(node) for node in workflow.values())
