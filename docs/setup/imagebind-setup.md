# ImageBind Setup Guide

ImageBind is an experimental Tier 1 embedding backend for OpenCastor that maps
6 modalities (image, text, audio, depth, IMU, thermal) into a shared 1024-dim space.

## License Warning

ImageBind is released under **CC BY-NC 4.0** by Meta AI Research.
- Research and internal deployments: OK
- Personal/hobby robots: OK
- Commercial products: NOT permitted
- Redistribution for commercial purposes: NOT permitted

See https://github.com/facebookresearch/ImageBind for the full license.

## Installation

```bash
git clone https://github.com/facebookresearch/ImageBind
cd ImageBind
pip install -e .
```

Then configure in your RCAN file:

```yaml
interpreter:
  enabled: true
  backend: local_extended
```

## Supported Modalities

| Modality | Input | Notes |
|---|---|---|
| Image | JPEG/PNG bytes | Camera frames |
| Text | str | Instructions, descriptions |
| Audio | WAV/MP3 bytes | Voice commands |
| IMU | (future) | via `audio_bytes` extension |

## Fallback Behaviour

If ImageBind is not installed, OpenCastor automatically falls back to the
CLIP Tier 0 provider (512-dim). No configuration change is required.
