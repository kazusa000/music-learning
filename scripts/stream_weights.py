"""Bounded host-memory safetensors reader for an already SHA-verified checkpoint."""
import json
import math
import struct


def load_weights(path, device, targets=None, remap=None):
    import torch
    types = {"F32": torch.float32, "F16": torch.float16, "BF16": torch.bfloat16,
             "I64": torch.int64, "I32": torch.int32, "BOOL": torch.bool}
    result = {}
    with open(path, "rb") as stream:
        size = struct.unpack("<Q", stream.read(8))[0]
        if size > 16 * 1024**2:
            raise ValueError("Unexpected safetensors header size")
        header = json.loads(stream.read(size))
        base = 8 + size
        for name, item in header.items():
            if name == "__metadata__":
                continue
            dtype = types[item["dtype"]]
            start, end = item["data_offsets"]
            length = math.prod(item["shape"]) * torch.empty((), dtype=dtype).element_size()
            if start < 0 or end - start != length:
                raise ValueError("Invalid tensor byte span")
            stream.seek(base + start)
            raw = bytearray(stream.read(length))
            if len(raw) != length:
                raise ValueError("Truncated tensor")
            value = torch.frombuffer(raw, dtype=dtype).reshape(item["shape"])
            if targets is not None:
                key = next(iter(remap({name: value}))) if remap else name
                target = targets[key]
                if target.shape != value.shape or target.dtype != value.dtype:
                    raise ValueError("Checkpoint tensor differs from model parameter")
                target.copy_(value)
                result[name] = target
            else:
                result[name] = value.to(device=device, copy=True)
    return result
