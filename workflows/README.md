# ComfyUI API workflows

## Bundled first image workflow

After the local server is healthy, run `python scripts/smoke_local_images.py`.
It generates one image at a time in 1:1, 16:9 and 9:16, confirms output PNG
dimensions and prints duration/hash and ComfyUI output filename. It does not
create app records or publish. On timeout it leaves the existing prompt alone;
inspect its history before submitting another request. Visual inspection and
peak GPU/RAM measurement are additional acceptance checks.

`scripts/download-image-checkpoint.ps1` downloads only the selected Comfy-Org
SD1.5 FP16 checkpoint (2,132,696,762 bytes), resumes a partial transfer and verifies
SHA256 before exposing the final checkpoint filename. Existing mismatched files
are preserved, not overwritten. Model license: CreativeML Open RAIL-M; follow its
usage restrictions. Model source and hash:
https://huggingface.co/Comfy-Org/stable-diffusion-v1-5-archive/blob/main/v1-5-pruned-emaonly-fp16.safetensors .

The isolated runtime lives in ignored `.cache/ComfyUI/.venv`. After its
dependencies and checkpoint are installed, `scripts/start-comfyui.ps1` starts
the pinned local source on 127.0.0.1:8188 with offline mode, no custom nodes and
low-VRAM policy. It does not change the application out of mock mode or install
anything. Startup is not proof of usable generation; run the real smoke test.

`image-sd15-api.json` uses only standard ComfyUI nodes: a single SD1.5 checkpoint,
CLIP prompt encoding, batch=1 latent image, 20-step Euler sampling and PNG output.
Start at 512x512. It is a workflow template, not an installed model or proof of
successful generation on this hardware. Do not enable live mode until ComfyUI
is running and its checkpoint list contains the configured filename.

After installation, set `COMFYUI_IMAGE_WORKFLOW=image-sd15-api.json` and
`IMAGE_CHECKPOINT` to the actual installed checkpoint basename. No custom nodes,
paid API or model auto-download is used. A real generated-file smoke test and
memory/latency measurement remain required. This does not generate video scenes.

Upstream references: https://github.com/Comfy-Org/ComfyUI and
https://huggingface.co/Comfy-Org/stable-diffusion-v1-5-archive .

Place only exported **API-format** ComfyUI workflow JSON files in this directory.
The application never downloads checkpoints or custom nodes automatically.

Supported placeholders inside JSON string values:

- `{{PROMPT}}`
- `{{NEGATIVE_PROMPT}}`
- `{{WIDTH}}` and `{{HEIGHT}}`
- `{{SEED}}`
- `{{OUTPUT_PREFIX}}`
- `{{IMAGE_CHECKPOINT}}`
- `{{WAN_TEXT_ENCODER}}`

Example environment values:

```dotenv
COMFYUI_MOCK_MODE=false
COMFYUI_IMAGE_WORKFLOW=image-api.json
IMAGE_CHECKPOINT=your-small-checkpoint.safetensors
```

For a 4 GB NVIDIA T1200, begin with a small/quantized image checkpoint and a
512 px workflow. Wan 2.2 video generation is intentionally not enabled by
default: the `wan22-qx-encoders-gguf` files are text encoders, not a complete
video checkpoint.
