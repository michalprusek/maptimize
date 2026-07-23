"""Unit tests for services.protein_embedding_service and
services.user_data_provisioning (DB + encoder mocked).

Coverage strategy:
  * protein_embedding_service imports ``get_esmc_encoder`` and
    ``parse_fasta_sequence`` at module level, so they are patched at the
    service boundary (``services.protein_embedding_service.*``).
  * user_data_provisioning issues raw SQL via ``db.execute``; the mock_db
    fixture returns ``make_result(...)`` per call, configured via
    ``side_effect`` lists to drive the loops.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from services import protein_embedding_service as pes
from services import user_data_provisioning as prov
from tests.unit.conftest import make_result


# --- helpers ------------------------------------------------------------------

def _fake_encoder(vector=None, encode_side_effect=None):
    """Build a fake ESM-C encoder with the attributes the service uses."""
    enc = MagicMock(name="ESMCEncoder")
    enc.model_name = "esmc-600m"
    if encode_side_effect is not None:
        enc.encode_sequence.side_effect = encode_side_effect
    else:
        enc.encode_sequence.return_value = (
            vector if vector is not None else np.array([0.1, 0.2, 0.3])
        )
    return enc


def _protein(**kw):
    """A protein-like object whose attributes the service reads/writes."""
    defaults = dict(
        id=1,
        name="PRC1",
        fasta_sequence="MKT",
        embedding=None,
        embedding_model=None,
        embedding_computed_at=None,
        sequence_length=None,
        umap_x=1.0,
        umap_y=2.0,
        umap_computed_at="something",
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


# === compute_protein_embedding ===============================================

async def test_compute_embedding_success(mock_db):
    protein = _protein()
    mock_db.execute.return_value = make_result(scalar=protein)
    encoder = _fake_encoder(vector=np.array([0.1, 0.2, 0.3, 0.4]))

    with patch("ml.features.esmc_encoder.get_esmc_encoder", return_value=encoder), \
         patch("ml.features.esmc_encoder.parse_fasta_sequence", return_value="MKT"):
        out = await pes.compute_protein_embedding(1, mock_db)

    assert out["success"] is True
    assert out["protein_id"] == 1
    assert out["protein_name"] == "PRC1"
    assert out["sequence_length"] == 3  # len("MKT")
    assert out["embedding_dim"] == 4
    assert out["embedding_model"] == "esmc-600m"
    # _update_protein_embedding side effects
    assert protein.embedding == [0.1, 0.2, 0.3, 0.4]
    assert protein.embedding_model == "esmc-600m"
    assert protein.sequence_length == 3
    assert protein.umap_x is None and protein.umap_y is None
    assert protein.umap_computed_at is None
    # computed_at must be ISO-serialisable (asserts it was set to a datetime)
    assert out["computed_at"] == protein.embedding_computed_at.isoformat()
    mock_db.commit.assert_awaited_once()
    encoder.encode_sequence.assert_called_once_with("MKT")


async def test_compute_embedding_protein_not_found_raises_lookuperror(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with patch("ml.features.esmc_encoder.get_esmc_encoder") as get_enc:
        with pytest.raises(LookupError, match="Protein with ID 99 not found"):
            await pes.compute_protein_embedding(99, mock_db)
    get_enc.assert_not_called()
    mock_db.commit.assert_not_awaited()


async def test_compute_embedding_no_sequence_raises_valueerror(mock_db):
    protein = _protein(fasta_sequence=None, name="EmptyProt")
    mock_db.execute.return_value = make_result(scalar=protein)
    with patch("ml.features.esmc_encoder.get_esmc_encoder") as get_enc:
        with pytest.raises(ValueError, match="has no FASTA sequence"):
            await pes.compute_protein_embedding(1, mock_db)
    get_enc.assert_not_called()
    mock_db.commit.assert_not_awaited()


async def test_compute_embedding_encoder_error_propagates(mock_db):
    protein = _protein()
    mock_db.execute.return_value = make_result(scalar=protein)
    encoder = _fake_encoder(encode_side_effect=RuntimeError("CUDA out of memory"))

    with patch("ml.features.esmc_encoder.get_esmc_encoder", return_value=encoder), \
         patch("ml.features.esmc_encoder.parse_fasta_sequence", return_value="MKT"):
        with pytest.raises(RuntimeError, match="CUDA out of memory"):
            await pes.compute_protein_embedding(1, mock_db)
    mock_db.commit.assert_not_awaited()


# === sequence-identical reuse ================================================
#
# ESM-C on GPU is not bit-reproducible: encoding the same sequence twice drifts
# by ~1e-5 (measured across all 13 protein/"3D sim" twins in production). That
# is biologically meaningless but enough to separate them on the UMAP, so the
# vector is reused whenever another protein already holds the same sequence.

async def test_compute_embedding_reuses_vector_of_sequence_twin(mock_db):
    target = _protein(id=1, name="PRC1", fasta_sequence=">sp|Q99K43\nMKT\n")
    twin = _protein(
        id=2, name="PRC1 3D sim", fasta_sequence="mkt",  # same residues, different text
        embedding=[0.5, 0.6, 0.7], embedding_model="esmc-600m", sequence_length=3,
    )
    mock_db.execute.side_effect = [
        make_result(scalar=target),        # target lookup
        make_result(scalars_all=[twin]),   # twin lookup
    ]

    with patch("ml.features.esmc_encoder.get_esmc_encoder") as get_enc:
        out = await pes.compute_protein_embedding(1, mock_db)

    # The whole point: no GPU model is loaded and no new vector is produced.
    get_enc.assert_not_called()
    assert target.embedding == [0.5, 0.6, 0.7]
    assert target.embedding_model == "esmc-600m"
    assert target.sequence_length == 3
    assert out["reused_from"] == "PRC1 3D sim"
    mock_db.commit.assert_awaited_once()


async def test_single_recompute_does_not_reuse_its_own_stale_vector(mock_db):
    """The live endpoint must exclude the target from its own lookup.

    Without that, "compute embedding" matches the protein's own row and becomes
    a permanent no-op for any protein that has no twin.
    """
    target = _protein(id=1, name="A", fasta_sequence="MKT",
                      embedding=[9.9], embedding_model="esmc-600m", sequence_length=3)
    mock_db.execute.side_effect = [
        make_result(scalar=target),
        make_result(scalars_all=[target]),  # the index query returns the target itself
    ]
    encoder = _fake_encoder(vector=np.array([0.1, 0.2]))

    with patch("ml.features.esmc_encoder.get_esmc_encoder", return_value=encoder):
        out = await pes.compute_protein_embedding(1, mock_db)

    encoder.encode_sequence.assert_called_once()
    assert out["reused_from"] is None
    assert target.embedding == [0.1, 0.2]


async def test_force_skips_reuse_and_re_encodes(mock_db):
    """force=True is the only escape when the twin's own vector is bad."""
    target = _protein(id=1, name="A", fasta_sequence="MKT")
    twin = _protein(id=2, name="Twin", fasta_sequence="MKT",
                    embedding=[0.5, 0.6], embedding_model="esmc-600m", sequence_length=3)
    mock_db.execute.side_effect = [
        make_result(scalar=target),
        make_result(scalars_all=[twin]),
    ]
    encoder = _fake_encoder(vector=np.array([0.1, 0.2]))

    with patch("ml.features.esmc_encoder.get_esmc_encoder", return_value=encoder):
        out = await pes.compute_protein_embedding(1, mock_db, force=True)

    encoder.encode_sequence.assert_called_once()
    assert out["reused_from"] is None
    assert target.embedding == [0.1, 0.2]


async def test_stored_model_name_matches_the_one_reuse_queries_on():
    """`_load_sequence_index` filters on the class constant while
    `_update_protein_embedding` stores the instance attribute. If those ever
    diverge, reuse silently matches nothing and twins drift apart again.
    """
    from ml.features.esmc_encoder import ESMCEncoder
    assert ESMCEncoder(device="cpu").model_name == ESMCEncoder.MODEL_NAME


async def test_encoder_load_failure_aborts_the_batch_instead_of_blaming_proteins(mock_db):
    """A GPU/model outage is not a per-sequence problem.

    Filing it against each protein would report 'invalid sequence, not
    retryable' N times and hide the real cause completely.
    """
    proteins = [_protein(id=1, name="A"), _protein(id=2, name="B")]
    mock_db.execute.return_value = make_result(scalars_all=proteins)

    with patch("ml.features.esmc_encoder.get_esmc_encoder",
               side_effect=ValueError("Unknown model: esmc")), \
         patch("ml.features.esmc_encoder.parse_fasta_sequence", return_value="MKT"):
        with pytest.raises(pes.EncoderUnavailableError, match="could not be loaded"):
            await pes.batch_compute_protein_embeddings(mock_db)


async def test_compute_embedding_encodes_when_no_twin_matches(mock_db):
    target = _protein(id=1, fasta_sequence="MKT")
    other = _protein(id=2, name="Different", fasta_sequence="WWW",
                     embedding=[9.0], embedding_model="esmc-600m", sequence_length=3)
    mock_db.execute.side_effect = [
        make_result(scalar=target),
        make_result(scalars_all=[other]),
    ]
    encoder = _fake_encoder(vector=np.array([0.1, 0.2]))

    with patch("ml.features.esmc_encoder.get_esmc_encoder", return_value=encoder):
        out = await pes.compute_protein_embedding(1, mock_db)

    encoder.encode_sequence.assert_called_once()
    assert target.embedding == [0.1, 0.2]
    assert out["reused_from"] is None


async def test_compute_embedding_ignores_twin_from_a_different_model(mock_db):
    """A vector from another encoder is not interchangeable — recompute instead."""
    target = _protein(id=1, fasta_sequence="MKT")
    stale = _protein(id=2, name="Old", fasta_sequence="MKT",
                     embedding=[9.0], embedding_model="esm2-650m", sequence_length=3)
    mock_db.execute.side_effect = [
        make_result(scalar=target),
        make_result(scalars_all=[stale]),
    ]
    encoder = _fake_encoder(vector=np.array([0.1, 0.2]))

    with patch("ml.features.esmc_encoder.get_esmc_encoder", return_value=encoder):
        await pes.compute_protein_embedding(1, mock_db)

    encoder.encode_sequence.assert_called_once()
    assert target.embedding == [0.1, 0.2]


async def test_compute_embedding_skips_twin_with_unparseable_sequence(mock_db):
    """A junk sequence elsewhere must not abort the lookup for everyone else."""
    target = _protein(id=1, fasta_sequence="MKT")
    broken = _protein(id=2, name="Broken", fasta_sequence=">header only\n",
                      embedding=[9.0], embedding_model="esmc-600m", sequence_length=3)
    good = _protein(id=3, name="Good", fasta_sequence="MKT",
                    embedding=[0.5, 0.6], embedding_model="esmc-600m", sequence_length=3)
    mock_db.execute.side_effect = [
        make_result(scalar=target),
        make_result(scalars_all=[broken, good]),
    ]

    with patch("ml.features.esmc_encoder.get_esmc_encoder") as get_enc:
        out = await pes.compute_protein_embedding(1, mock_db)

    get_enc.assert_not_called()
    assert target.embedding == [0.5, 0.6]
    assert out["reused_from"] == "Good"


# === batch_compute_protein_embeddings ========================================

async def test_batch_no_proteins_returns_early(mock_db):
    mock_db.execute.return_value = make_result(scalars_all=[])
    with patch("ml.features.esmc_encoder.get_esmc_encoder") as get_enc:
        out = await pes.batch_compute_protein_embeddings(mock_db)
    # Shape must match the normal return, or a caller reading result["reused"]
    # gets a KeyError on the empty-batch path only.
    assert out == {"computed": 0, "reused": 0, "failed": 0, "errors": None,
                   "message": "No proteins need embedding computation"}
    # Encoder not loaded when nothing to do.
    get_enc.assert_not_called()
    mock_db.commit.assert_not_awaited()


async def test_batch_force_recompute_query_branch(mock_db):
    """force_recompute=True skips the embedding.is_(None) filter branch."""
    p1 = _protein(id=1, name="A")
    mock_db.execute.return_value = make_result(scalars_all=[p1])
    encoder = _fake_encoder(vector=np.array([1.0, 2.0]))
    with patch("ml.features.esmc_encoder.get_esmc_encoder", return_value=encoder), \
         patch("ml.features.esmc_encoder.parse_fasta_sequence", return_value="MKT"):
        out = await pes.batch_compute_protein_embeddings(mock_db, force_recompute=True)
    assert out["computed"] == 1
    assert out["failed"] == 0
    assert out["errors"] is None
    assert p1.embedding == [1.0, 2.0]
    mock_db.commit.assert_awaited_once()


async def test_batch_mixed_success_and_failures(mock_db):
    """One success, one validation error, one generic Exception."""
    ok = _protein(id=1, name="OK")
    bad_val = _protein(id=2, name="BadVal")
    bad_unknown = _protein(id=3, name="Unknown")
    mock_db.execute.return_value = make_result(scalars_all=[ok, bad_val, bad_unknown])

    encoder = _fake_encoder(encode_side_effect=[
        np.array([0.5, 0.6]),          # ok
        ValueError("bad sequence"),    # bad_val -> validation_error
        TypeError("weird"),            # bad_unknown -> unknown
    ])
    # Distinct sequences: same-sequence proteins are deliberately reused rather
    # than encoded, which would consume none of the encoder's side effects.
    with patch("ml.features.esmc_encoder.get_esmc_encoder", return_value=encoder), \
         patch("ml.features.esmc_encoder.parse_fasta_sequence",
               side_effect=["MKT", "WWW", "YYY"]):
        out = await pes.batch_compute_protein_embeddings(mock_db)

    assert out["computed"] == 1
    assert out["failed"] == 2
    types = {e["error_type"] for e in out["errors"]}
    assert types == {"validation_error", "unknown"}
    val_err = next(e for e in out["errors"] if e["error_type"] == "validation_error")
    assert val_err["retryable"] is False
    unknown_err = next(e for e in out["errors"] if e["error_type"] == "unknown")
    assert unknown_err["retryable"] is True
    mock_db.commit.assert_awaited_once()


async def test_batch_runtime_error_non_oom_is_retryable(mock_db):
    p = _protein(id=1, name="RT")
    mock_db.execute.return_value = make_result(scalars_all=[p])
    encoder = _fake_encoder(encode_side_effect=RuntimeError("some transient failure"))
    with patch("ml.features.esmc_encoder.get_esmc_encoder", return_value=encoder), \
         patch("ml.features.esmc_encoder.parse_fasta_sequence", return_value="MKT"):
        out = await pes.batch_compute_protein_embeddings(mock_db)
    assert out["computed"] == 0
    assert out["failed"] == 1
    assert out["errors"][0]["error_type"] == "runtime_error"
    assert out["errors"][0]["retryable"] is True


async def test_batch_gpu_oom_aborts_remaining(mock_db):
    """OOM RuntimeError appends a gpu_oom error and breaks the loop."""
    p1 = _protein(id=1, name="First")
    p2 = _protein(id=2, name="Second")
    mock_db.execute.return_value = make_result(scalars_all=[p1, p2])
    encoder = _fake_encoder(encode_side_effect=[
        RuntimeError("CUDA out of memory"),  # aborts immediately
        np.array([9.9]),                     # should never run
    ])
    with patch("ml.features.esmc_encoder.get_esmc_encoder", return_value=encoder), \
         patch("ml.features.esmc_encoder.parse_fasta_sequence", return_value="MKT"):
        out = await pes.batch_compute_protein_embeddings(mock_db)
    assert out["computed"] == 0
    assert out["failed"] == 1
    assert len(out["errors"]) == 1
    assert out["errors"][0]["error_type"] == "gpu_oom"
    assert out["errors"][0]["retryable"] is False
    # Loop broke before encoding the second protein.
    assert encoder.encode_sequence.call_count == 1
    # The abandoned protein is counted nowhere else, so the summary must not
    # read like a clean run.
    assert "Aborted" in out["message"] and "1 not attempted" in out["message"]
    mock_db.commit.assert_awaited_once()


async def test_batch_reuses_within_the_batch(mock_db):
    """Twins in one batch share a vector: encode once, copy to the rest."""
    first = _protein(id=1, name="PRC1")
    twin = _protein(id=2, name="PRC1 3D sim")
    mock_db.execute.return_value = make_result(scalars_all=[first, twin])
    encoder = _fake_encoder(vector=np.array([0.5, 0.6]))

    with patch("ml.features.esmc_encoder.get_esmc_encoder", return_value=encoder), \
         patch("ml.features.esmc_encoder.parse_fasta_sequence", return_value="MKT"):
        out = await pes.batch_compute_protein_embeddings(mock_db)

    assert encoder.encode_sequence.call_count == 1
    assert out["computed"] == 1
    assert out["reused"] == 1
    # The actual requirement: bit-identical vectors, not merely close ones.
    assert first.embedding == twin.embedding == [0.5, 0.6]


async def test_batch_force_recompute_does_not_reuse_own_stale_vector(mock_db):
    """Proteins being recomputed must not match themselves, or the force
    recompute silently becomes a no-op."""
    p = _protein(id=1, name="A", embedding=[9.9], embedding_model="esmc-600m")
    mock_db.execute.return_value = make_result(scalars_all=[p])
    encoder = _fake_encoder(vector=np.array([0.1, 0.2]))

    with patch("ml.features.esmc_encoder.get_esmc_encoder", return_value=encoder), \
         patch("ml.features.esmc_encoder.parse_fasta_sequence", return_value="MKT"):
        out = await pes.batch_compute_protein_embeddings(mock_db, force_recompute=True)

    encoder.encode_sequence.assert_called_once()
    assert out["computed"] == 1 and out["reused"] == 0
    assert p.embedding == [0.1, 0.2]


async def test_batch_of_pure_duplicates_never_loads_the_encoder(mock_db):
    """An all-reuse batch must not pay for a GPU load."""
    already = _protein(id=9, name="Source", embedding=[0.5, 0.6],
                       embedding_model="esmc-600m")
    pending = _protein(id=1, name="Copy")
    mock_db.execute.side_effect = [
        make_result(scalars_all=[pending]),  # proteins needing embeddings
        make_result(scalars_all=[already]),  # sequence index
    ]

    with patch("ml.features.esmc_encoder.get_esmc_encoder") as get_enc, \
         patch("ml.features.esmc_encoder.parse_fasta_sequence", return_value="MKT"):
        out = await pes.batch_compute_protein_embeddings(mock_db)

    get_enc.assert_not_called()
    assert out["reused"] == 1 and out["computed"] == 0
    assert pending.embedding == [0.5, 0.6]


async def test_batch_keyboard_interrupt_reraised(mock_db):
    p = _protein(id=1, name="KI")
    mock_db.execute.return_value = make_result(scalars_all=[p])
    encoder = _fake_encoder(encode_side_effect=KeyboardInterrupt())
    with patch("ml.features.esmc_encoder.get_esmc_encoder", return_value=encoder), \
         patch("ml.features.esmc_encoder.parse_fasta_sequence", return_value="MKT"):
        with pytest.raises(KeyboardInterrupt):
            await pes.batch_compute_protein_embeddings(mock_db)


# === user_data_provisioning ===================================================

def _row(**kw):
    """A DB row supporting attribute access (row.id, row.name, ...)."""
    return SimpleNamespace(**kw)


async def test_provision_no_template_experiments_skips(mock_db):
    # First execute: COUNT(*) == 0 -> early return.
    mock_db.execute.return_value = make_result(scalar=0)
    await prov.provision_new_user_data(new_user_id=5, db=mock_db)
    # Only the COUNT query ran.
    assert mock_db.execute.await_count == 1


async def test_provision_with_experiments_no_images(mock_db):
    """Template has 1 experiment but no images -> 'continue' branch."""
    exp = _row(id=10, name="Exp", description="d", map_protein_id=None,
               fasta_sequence="MKT", status="ready")
    mock_db.execute.side_effect = [
        make_result(scalar=1),               # COUNT(*) > 0
        make_result(fetchall=[exp]),         # list template experiments
        make_result(scalar=100),             # INSERT new experiment RETURNING id
        make_result(),                       # INSERT...SELECT images
        make_result(fetchall=[]),            # _build_id_mapping -> empty image_map
    ]
    await prov.provision_new_user_data(new_user_id=5, db=mock_db)
    # 5 execute calls; the per-image crop/SAM copies were skipped via continue.
    assert mock_db.execute.await_count == 5


async def test_provision_full_path_with_images_and_crops(mock_db):
    exp = _row(id=10, name="Exp", description="d", map_protein_id=7,
               fasta_sequence="MKT", status="ready")
    img_map_row = _row(old_id=200, new_id=300)
    crop_map_row = _row(old_id=400, new_id=500)
    mock_db.execute.side_effect = [
        make_result(scalar=1),                       # COUNT(*) > 0
        make_result(fetchall=[exp]),                 # template experiments
        make_result(scalar=100),                     # INSERT new experiment
        make_result(),                               # INSERT images
        make_result(fetchall=[img_map_row]),         # image id mapping (1 pair)
        make_result(),                               # INSERT cell_crops
        make_result(fetchall=[crop_map_row]),        # crop id mapping
        make_result(),                               # INSERT sam_embeddings
    ]
    await prov.provision_new_user_data(new_user_id=5, db=mock_db)
    assert mock_db.execute.await_count == 8


# === _build_id_mapping ========================================================

async def test_build_id_mapping_pairs_rows(mock_db):
    rows = [_row(old_id=1, new_id=11), _row(old_id=2, new_id=22)]
    mock_db.execute.return_value = make_result(fetchall=rows)
    mapping = await prov._build_id_mapping(mock_db, "images", "experiment_id", 1, 2)
    assert mapping == {1: 11, 2: 22}


# === _copy_metrics ============================================================

async def test_copy_metrics_no_metrics_returns(mock_db):
    mock_db.execute.return_value = make_result(fetchall=[])
    await prov._copy_metrics(mock_db, new_user_id=5, crop_id_map={1: 11})
    assert mock_db.execute.await_count == 1  # only the metrics SELECT ran


async def test_copy_metrics_empty_crop_map_returns(mock_db):
    metric = _row(id=1, name="M", description="d")
    mock_db.execute.return_value = make_result(fetchall=[metric])
    await prov._copy_metrics(mock_db, new_user_id=5, crop_id_map={})
    assert mock_db.execute.await_count == 1  # returns before inserting


async def test_copy_metrics_full_path_remaps_crop_ids(mock_db):
    metric = _row(id=1, name="M", description="d")
    # Two metric images: one mapped crop, one with NULL crop, one unmapped crop.
    mi_mapped = _row(cell_crop_id=400, file_path="a.png", original_filename="a")
    mi_null = _row(cell_crop_id=None, file_path="b.png", original_filename="b")
    mi_unmapped = _row(cell_crop_id=999, file_path="c.png", original_filename="c")
    mock_db.execute.side_effect = [
        make_result(fetchall=[metric]),                       # metrics SELECT
        make_result(scalar=900),                              # INSERT metric RETURNING id
        make_result(fetchall=[mi_mapped, mi_null, mi_unmapped]),  # metric_images SELECT
        make_result(),                                        # INSERT mi_mapped
        make_result(),                                        # INSERT mi_null
        make_result(),                                        # INSERT mi_unmapped
    ]
    await prov._copy_metrics(mock_db, new_user_id=5, crop_id_map={400: 500})

    # Inspect the params passed for each metric_images INSERT.
    insert_calls = [
        c for c in mock_db.execute.await_args_list
        if "INSERT INTO metric_images" in str(c.args[0])
    ]
    assert len(insert_calls) == 3
    params = [c.args[1] for c in insert_calls]
    assert params[0]["cell_crop_id"] == 500   # remapped
    assert params[1]["cell_crop_id"] is None  # NULL stays None
    assert params[2]["cell_crop_id"] is None  # unmapped -> None
