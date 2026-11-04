"""Explicit server-only settings; secrets are never passed to the browser."""
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import shutil

REPO = Path(__file__).resolve().parents[1]
VEO_MODELS = ('google/veo-3.1','fal-ai/veo3.1/reference-to-video','fal-ai/veo3.1/image-to-video','fal-ai/veo3.1/first-last-frame-to-video')

@dataclass(frozen=True)
class Settings:
    meta_key: str = field(repr=False)
    openrouter_key: str = field(repr=False)
    fal_key: str = field(repr=False)
    allow_live: bool = False
    root: Path = REPO / 'productions/ghost-director-hackathon'
    reasoning_model: str = 'muse-wonder-1.0'
    image_model: str = 'muse-image-1.0'
    segmentation_model: str = 'sam-3.1'
    video_model: str = 'fal-ai/veo3.1/first-last-frame-to-video'
    blender: str = ''
    studio_root: Path | None = None

    @property
    def secrets(self):
        return tuple(x for x in (self.meta_key, self.openrouter_key, self.fal_key) if x)


def load_settings(env_file: str | Path | None = None) -> Settings:
    path = Path(env_file or os.environ.get('GHOST_ENV_FILE', REPO / '.env.local'))
    values = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            match = re.fullmatch(r'\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*', line)
            if not match:
                continue
            key, value = match.groups()
            if key in values:
                raise ValueError('Duplicate environment field; values withheld')
            if value[:1] in ('"', "'"):
                quote = value[0]
                end = value.find(quote, 1)
                if end < 0 or (value[end+1:].strip() and not value[end+1:].strip().startswith('#')):
                    raise ValueError('Invalid environment quoting; values withheld')
                value = value[1:end]
            else:
                value = value.split(' #', 1)[0].strip()
            values[key] = value
    values.update({k: v for k, v in os.environ.items() if k.startswith(('GHOST_', 'META_', 'MODEL_', 'OPENROUTER_', 'FAL_', 'STUDIO_'))})
    expected = {'META_REASONING_MODEL':'muse-wonder-1.0','META_IMAGE_MODEL':'muse-image-1.0','META_SEGMENTATION_MODEL':'sam-3.1'}
    for key, model in expected.items():
        if values.get(key, model) != model:
            raise ValueError(f'{key} differs from the verified capability registry; revalidate before switching')
    video_model=values.get('GHOST_VIDEO_MODEL','fal-ai/veo3.1/first-last-frame-to-video')
    if video_model not in ('bytedance/seedance-2.5',*VEO_MODELS):
        raise ValueError('GHOST_VIDEO_MODEL is outside the verified capability registry')
    candidates = [shutil.which('blender'), '/Applications/Blender.app/Contents/MacOS/Blender', str(REPO.parent / 'tooling/Blender-5.2.3.app/Contents/MacOS/Blender')]
    blender = values.get('GHOST_BLENDER') or next((path for path in candidates if path and Path(path).is_file()), '')
    return Settings(meta_key=values.get('MODEL_API_KEY',''), openrouter_key=values.get('OPENROUTER_API_KEY',''), fal_key=values.get('FAL_KEY',''), allow_live=values.get('STUDIO_ALLOW_LIVE_CALLS')=='1', root=Path(values.get('GHOST_PRODUCTION_ROOT',str(REPO/'productions/ghost-director-hackathon'))), blender=blender,video_model=video_model,studio_root=Path(values['GHOST_STUDIO_ROOT']) if values.get('GHOST_STUDIO_ROOT') else None)
