"""Run with the isolated transcription environment; no music or GPU allocation."""
import sys
from pathlib import Path

import pytest
import torch
from safetensors.torch import save_file, load_file

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"scripts"))
from stream_weights import load_weights


def test_streamed_weights_match_official_reader_exactly(tmp_path):
    checkpoint=tmp_path/"weights.safetensors"
    values={"float":torch.arange(12,dtype=torch.float32).reshape(3,4),
            "half":torch.tensor([.25,.5],dtype=torch.float16),
            "index":torch.tensor([2,7],dtype=torch.int64)}
    save_file(values,checkpoint)
    reference=load_file(checkpoint)
    streamed=load_weights(checkpoint,"cpu")
    assert streamed.keys()==reference.keys()
    for key in reference:
        assert streamed[key].dtype==reference[key].dtype
        assert torch.equal(streamed[key],reference[key])


def test_loading_into_existing_storage_does_not_allocate_a_second_model(tmp_path):
    checkpoint=tmp_path/"weights.safetensors"
    save_file({"emb.0.weight":torch.arange(12,dtype=torch.float32).reshape(3,4)},checkpoint)
    target={"emb.weight":torch.zeros(3,4)}
    address=target["emb.weight"].data_ptr()
    result=load_weights(checkpoint,"cpu",target,lambda d:{k.replace("emb.0.","emb."):v for k,v in d.items()})
    assert result["emb.0.weight"].data_ptr()==address
    assert target["emb.weight"].sum()==66


def test_truncated_or_incompatible_tensor_cannot_be_accepted(tmp_path):
    checkpoint=tmp_path/"weights.safetensors"
    save_file({"weight":torch.ones(3,4)},checkpoint)
    with pytest.raises(ValueError):
        load_weights(checkpoint,"cpu",{"weight":torch.zeros(4,3)})
    checkpoint.write_bytes(checkpoint.read_bytes()[:-3])
    with pytest.raises(ValueError):
        load_weights(checkpoint,"cpu")
