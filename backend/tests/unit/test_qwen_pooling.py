"""Unit tests for the Qwen VL encoder's last-token pooling.

These exercise real torch tensor ops, so they are skipped in the mocked-torch
coverage lane (where ``sys.modules['torch']`` is a Mock) and run under the
real-torch runner (prod image + pytest).
"""
import types
from unittest.mock import Mock

import numpy as np
import pytest

torch = pytest.importorskip("torch")

pytestmark = pytest.mark.skipif(
    isinstance(torch, Mock), reason="needs real torch (mocked in coverage lane)"
)


def _encoder_with_hidden(hidden):
    from ml.rag.qwen_vl_encoder import QwenVLEncoder

    enc = QwenVLEncoder(device="cpu")
    enc.model = lambda **kw: types.SimpleNamespace(hidden_states=[None, hidden])
    return enc


def _directional_hidden(seq_len: int, dim: int = 2048):
    """token t -> unit vector along dim t, so argmax(embedding) == picked token."""
    hidden = torch.zeros(1, seq_len, dim)
    for t in range(seq_len):
        hidden[0, t, t] = 1.0
    return hidden


def test_last_token_right_padded():
    enc = _encoder_with_hidden(_directional_hidden(4))
    v = enc._pool_and_normalize({"attention_mask": torch.tensor([[1, 1, 1, 0]])})
    assert int(np.argmax(v)) == 2  # last non-pad token
    assert v.shape[0] == enc.EMBEDDING_DIM
    assert np.isclose(np.linalg.norm(v), 1.0, atol=1e-5)


def test_last_token_left_padded():
    enc = _encoder_with_hidden(_directional_hidden(4))
    v = enc._pool_and_normalize({"attention_mask": torch.tensor([[0, 1, 1, 1]])})
    assert int(np.argmax(v)) == 3  # true last position


def test_last_token_no_mask():
    enc = _encoder_with_hidden(_directional_hidden(4))
    v = enc._pool_and_normalize({})
    assert int(np.argmax(v)) == 3


def test_wrong_dim_raises():
    enc = _encoder_with_hidden(torch.ones(1, 2, 128))  # 128 != EMBEDDING_DIM
    with pytest.raises(RuntimeError):
        enc._pool_and_normalize({"attention_mask": torch.tensor([[1, 1]])})
