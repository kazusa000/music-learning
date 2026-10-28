"""Explicit local environment checks, without loading music-model weights."""
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(os.environ.get('ML_RUN_ENVIRONMENT_CUDA_SMOKE') != '1',
                                reason='Explicit local GPU environment check')


@pytest.mark.parametrize('environment', ['transcription', 'reference', 'separation'])
def test_installed_model_environment_cuda_and_imports(environment, tmp_path):
    python = ROOT / 'data/envs' / environment / 'Scripts/python.exe'
    code = '''
import importlib, json, sys
import numpy as np
import torch, torchaudio
assert torch.cuda.is_available(), 'CUDA is unavailable'
cpu = torch.from_numpy(np.arange(16, dtype=np.float32).reshape(4, 4))
assert cpu.numpy().shape == (4, 4)
gpu = cpu.to('cuda')
assert torch.isfinite(gpu @ gpu.T).all().item()
assert torch.allclose(torch.linalg.inv(torch.eye(4, device='cuda')), torch.eye(4, device='cuda'))
audio = torch.zeros(1, 4096, device='cuda')
assert torchaudio.functional.resample(audio, 44100, 22050).shape[-1] == 2048
modules = {
    'transcription': ['muscriptor.transcription_model', 'beat_this.inference'],
    'reference': ['transformers', 'scipy', 'safetensors'],
    'separation': ['bs_roformer', 'bs_roformer.inference'],
}[sys.argv[1]]
for name in modules:
    importlib.import_module(name)
print(json.dumps(dict(environment=sys.argv[1], torch=torch.__version__, torchaudio=torchaudio.__version__,
                      numpy=np.__version__, device=torch.cuda.get_device_name(0), imported=modules)))
'''
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', TEMP=str(tmp_path), TMP=str(tmp_path),
               CUDA_CACHE_PATH=str(tmp_path / 'cuda-cache'), HF_HUB_OFFLINE='1')
    result = subprocess.run([str(python), '-B', '-c', code, environment], cwd=tmp_path,
                            env=env, capture_output=True, text=True, encoding='utf-8',
                            errors='replace', timeout=90)
    assert result.returncode == 0, result.stderr[-6000:]
    output = json.loads(result.stdout.strip().splitlines()[-1])
    print(json.dumps(output))
