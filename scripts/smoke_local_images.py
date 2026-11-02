"""Generate three real local ComfyUI images; no application DB or publishing."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import time
import urllib.parse
import urllib.request


def render(value, replacements):
    if isinstance(value, dict):
        return {key: render(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [render(item, replacements) for item in value]
    return replacements.get(value, value) if isinstance(value, str) else value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8188')
    parser.add_argument('--checkpoint', default='v1-5-pruned-emaonly-fp16.safetensors')
    parser.add_argument('--timeout', type=int, default=1800)
    args = parser.parse_args()
    parsed = urllib.parse.urlparse(args.base_url)
    if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost'):
        parser.error('Smoke test supports a local loopback HTTP server only')
    base = args.base_url.rstrip('/')

    def request(path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(base + path, data=data, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.read()

    info = json.loads(request('/object_info/CheckpointLoaderSimple'))
    available = info['CheckpointLoaderSimple']['input']['required']['ckpt_name'][0]
    if args.checkpoint not in available:
        raise RuntimeError('Verified checkpoint is not available in ComfyUI')
    template = json.loads((Path(__file__).resolve().parents[1] / 'workflows/image-sd15-api.json').read_text(encoding='utf-8'))
    for ratio, width, height in [('1:1', 512, 512), ('16:9', 768, 432), ('9:16', 432, 768)]:
        started = time.monotonic()
        workflow = render(template, {
            '{{IMAGE_CHECKPOINT}}': args.checkpoint,
            '{{PROMPT}}': 'professional photograph of a clean modern warehouse aisle, shelves with cardboard boxes, realistic lighting, no people, no text',
            '{{NEGATIVE_PROMPT}}': 'text, letters, watermark, logo, blur',
            '{{WIDTH}}': width, '{{HEIGHT}}': height, '{{SEED}}': 42,
            '{{OUTPUT_PREFIX}}': f'local-smoke-{width}x{height}',
        })
        accepted = json.loads(request('/prompt', {'prompt': workflow}))
        prompt_id = accepted['prompt_id']
        while time.monotonic() - started < args.timeout:
            history = json.loads(request('/history/' + urllib.parse.quote(prompt_id))).get(prompt_id)
            if history:
                status = history.get('status', {})
                if status.get('status_str') == 'error':
                    raise RuntimeError(f'ComfyUI execution failed: {prompt_id}')
                outputs = history.get('outputs', {}).get('7', {}).get('images', [])
                if outputs:
                    asset = outputs[0]
                    image = request('/view?' + urllib.parse.urlencode(asset))
                    if image[:8] != b'\x89PNG\r\n\x1a\n' or len(image) < 24:
                        raise RuntimeError('Generated file is not a PNG')
                    if struct.unpack('>II', image[16:24]) != (width, height):
                        raise RuntimeError('Generated dimensions differ from requested format')
                    print(json.dumps(dict(ratio=ratio, width=width, height=height,
                        seconds=round(time.monotonic()-started, 1), byte_size=len(image),
                        sha256=hashlib.sha256(image).hexdigest(), output=asset,
                        application_saved=False, published=False)), flush=True)
                    break
            time.sleep(2)
        else:
            raise TimeoutError(f'Generation timeout: {prompt_id}; do not blindly resubmit')


if __name__ == '__main__':
    main()
