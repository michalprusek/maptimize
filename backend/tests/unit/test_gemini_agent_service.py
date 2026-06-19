"""In-process unit tests for ``services.gemini_agent_service``.

The module is a Gemini function-calling agent. It lazy-imports ``google.genai``,
numpy/pandas/matplotlib, httpx and several sibling services *inside* the function
bodies, which lets us mock each at the call boundary:

  * the Gemini client is faked via ``patch("google.genai.Client")`` so
    ``client.models.generate_content`` returns canned ``types``-built responses
    (text and/or function_call parts) that drive the tool-dispatch loop;
  * the DB is the AsyncMock ``mock_db`` fixture; ``make_result`` builds Result
    objects and ``.side_effect=[...]`` feeds multiple queries;
  * sibling services (rag_service, data_export_service, code_execution_service,
    image_processor) and httpx are patched per test.

Pure helpers (``_serialize_for_json``, ``_is_safe_url``, ``_inject_user_id_filter``,
``_fix_passage_links_in_response``, ``StatsCache``) are exercised directly.
"""
import sys
from datetime import datetime, date
from decimal import Decimal
from enum import Enum
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

import services.gemini_agent_service as svc
from services.gemini_agent_service import (
    StatsCache,
    _fix_passage_links_in_response,
    _inject_user_id_filter,
    _is_safe_url,
    _serialize_for_json,
    execute_tool,
    generate_response,
)

from tests.unit.conftest import make_result


# =========================================================================== #
# _serialize_for_json
# =========================================================================== #
def test_serialize_datetime_and_date():
    assert _serialize_for_json(datetime(2024, 1, 2, 3, 4, 5)) == "2024-01-02T03:04:05"
    assert _serialize_for_json(date(2024, 1, 2)) == "2024-01-02"


def test_serialize_decimal():
    assert _serialize_for_json(Decimal("3.5")) == 3.5
    assert isinstance(_serialize_for_json(Decimal("3.5")), float)


def test_serialize_enum():
    class Color(Enum):
        RED = "red"
    assert _serialize_for_json(Color.RED) == "red"


def test_serialize_numpy_scalars_and_array():
    assert _serialize_for_json(np.int64(7)) == 7
    assert isinstance(_serialize_for_json(np.int64(7)), int)
    assert _serialize_for_json(np.float32(1.5)) == 1.5
    assert isinstance(_serialize_for_json(np.float64(1.5)), float)
    assert _serialize_for_json(np.array([1, 2, 3])) == [1, 2, 3]
    assert _serialize_for_json(np.bool_(True)) is True


def test_serialize_bytes():
    assert _serialize_for_json(b"hi") == "hi"
    # invalid utf-8 falls back to replacement chars (errors='replace')
    assert isinstance(_serialize_for_json(b"\xff\xfe"), str)


def test_serialize_nested_dict_list_tuple():
    out = _serialize_for_json({"a": [date(2024, 1, 1), (Decimal("2"),)]})
    assert out == {"a": ["2024-01-01", [2.0]]}


def test_serialize_object_with_dict_becomes_str():
    class Thing:
        def __init__(self):
            self.x = 1

        def __str__(self):
            return "THING"
    assert _serialize_for_json(Thing()) == "THING"


def test_serialize_passthrough_primitives():
    assert _serialize_for_json("plain") == "plain"
    assert _serialize_for_json(42) == 42
    assert _serialize_for_json(None) is None


# =========================================================================== #
# StatsCache
# =========================================================================== #
def test_stats_cache_set_get_and_copy():
    StatsCache._cache.clear()
    data = {"a": 1}
    StatsCache.set(99, data)
    got = StatsCache.get(99)
    assert got == {"a": 1}
    # returned a copy: mutating it does not corrupt the cache
    got["a"] = 2
    assert StatsCache.get(99) == {"a": 1}


def test_stats_cache_miss_returns_none():
    StatsCache._cache.clear()
    assert StatsCache.get(12345) is None


def test_stats_cache_expiry(monkeypatch):
    StatsCache._cache.clear()
    t = [1000.0]
    monkeypatch.setattr(svc.time, "time", lambda: t[0])
    StatsCache.set(1, {"v": 1})
    t[0] = 1000.0 + StatsCache.TTL + 1  # advance past TTL
    assert StatsCache.get(1) is None
    assert 1 not in StatsCache._cache  # expired entry was purged


def test_stats_cache_cleanup_over_100(monkeypatch):
    StatsCache._cache.clear()
    t = [1.0]
    monkeypatch.setattr(svc.time, "time", lambda: t[0])
    for i in range(101):
        StatsCache.set(i, {"v": i})
    # Cleanup path triggered (len > 100); all entries are still fresh so kept.
    assert len(StatsCache._cache) <= 101


# =========================================================================== #
# _is_safe_url (SSRF protection)
# =========================================================================== #
def test_is_safe_url_rejects_non_http_scheme():
    ok, err = _is_safe_url("ftp://example.com")
    assert ok is False and "HTTP/HTTPS" in err


def test_is_safe_url_rejects_no_hostname():
    ok, err = _is_safe_url("http://")
    assert ok is False


def test_is_safe_url_rejects_localhost_string():
    ok, err = _is_safe_url("http://localhost/x")
    assert ok is False and "localhost" in err


def test_is_safe_url_rejects_internal_suffix():
    ok, err = _is_safe_url("http://service.internal/x")
    assert ok is False and "internal" in err.lower()
    ok2, _ = _is_safe_url("http://foo.local/x")
    assert ok2 is False


def test_is_safe_url_rejects_private_ip_literal():
    ok, err = _is_safe_url("http://192.168.1.1/x")
    assert ok is False and "private" in err.lower()


def test_is_safe_url_rejects_loopback_ip_literal():
    ok, err = _is_safe_url("http://127.0.0.1/x")
    # caught by the literal-string localhost block
    assert ok is False


def test_is_safe_url_rejects_link_local_metadata():
    ok, err = _is_safe_url("http://169.254.169.254/latest/meta-data")
    assert ok is False


def test_is_safe_url_rejects_multicast():
    ok, err = _is_safe_url("http://224.0.0.1/x")
    assert ok is False and "multicast" in err.lower()


def test_is_safe_url_cloud_metadata_literal():
    # 100.100.100.200 is not private/loopback/link-local/multicast, so it reaches
    # the explicit cloud-metadata endpoint check.
    ok, err = _is_safe_url("http://100.100.100.200/x")
    assert ok is False and "metadata" in err.lower()


def test_is_safe_url_hostname_resolves_loopback_blocked(monkeypatch):
    # A hostname (bypasses the literal localhost string check) that resolves to a
    # 127.x address is blocked. Note 127.0.0.0/8 is classified as *private* by
    # ipaddress, so the private branch fires before the loopback branch.
    import socket
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("127.0.0.5", 0))],
    )
    ok, err = _is_safe_url("https://sneaky.example/")
    assert ok is False and "blocked address" in err


def test_is_safe_url_hostname_resolves_invalid_ip(monkeypatch):
    # getaddrinfo returns a non-IP string -> ip_address() raises ValueError ->
    # the "Invalid IP address" branch of _check_ip_is_safe.
    import socket
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("not-an-ip", 0))],
    )
    ok, err = _is_safe_url("https://weird.example/")
    assert ok is False and "Invalid IP" in err


def test_is_safe_url_public_ip_literal_ok():
    ok, err = _is_safe_url("https://8.8.8.8/")
    assert ok is True and err == ""


def test_is_safe_url_hostname_resolves_public(monkeypatch):
    import socket
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )
    ok, err = _is_safe_url("https://example.com/page")
    assert ok is True


def test_is_safe_url_hostname_resolves_private_blocked(monkeypatch):
    import socket
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("10.0.0.5", 0))],
    )
    ok, err = _is_safe_url("https://evil.example/page")
    assert ok is False and "blocked address" in err


def test_is_safe_url_hostname_no_resolution(monkeypatch):
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [])
    ok, err = _is_safe_url("https://nowhere.example/")
    assert ok is False and "resolve" in err.lower()


def test_is_safe_url_gaierror(monkeypatch):
    import socket
    def boom(*a, **k):
        raise socket.gaierror("no dns")
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    ok, err = _is_safe_url("https://broken.example/")
    assert ok is False and "resolve" in err.lower()


def test_is_safe_url_generic_dns_error(monkeypatch):
    import socket
    def boom(*a, **k):
        raise RuntimeError("weird")
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    ok, err = _is_safe_url("https://broken2.example/")
    assert ok is False and "DNS resolution error" in err


def test_is_safe_url_outer_exception():
    # Passing a non-string makes urlparse raise -> outer except branch.
    ok, err = _is_safe_url(12345)
    assert ok is False and "Invalid URL" in err


# =========================================================================== #
# _inject_user_id_filter
# =========================================================================== #
def test_inject_filter_with_where_and_orderby():
    q = "SELECT * FROM experiments WHERE name = 'x' ORDER BY id"
    out = _inject_user_id_filter(q, "experiments")
    assert "experiments.user_id = :user_id AND (name = 'x')" in out
    assert "ORDER BY id" in out


def test_inject_filter_with_where_no_trailer():
    q = "SELECT * FROM experiments WHERE name = 'x'"
    out = _inject_user_id_filter(q, "experiments")
    assert "experiments.user_id = :user_id AND (name = 'x')" in out


def test_inject_filter_no_where_adds_after_from():
    q = "SELECT * FROM experiments"
    out = _inject_user_id_filter(q, "experiments")
    assert "WHERE experiments.user_id = :user_id" in out


def test_inject_filter_no_from_appends_where():
    # No FROM clause -> the regex misses and it appends a WHERE at the end.
    q = "SELECT 1"
    out = _inject_user_id_filter(q, "experiments")
    assert out.endswith("WHERE experiments.user_id = :user_id")


# =========================================================================== #
# _fix_passage_links_in_response
# =========================================================================== #
VALID_MD = "![Fig 2](passage:42:3:abcdef012345)"


def test_fix_passage_noop_empty():
    assert _fix_passage_links_in_response("", [VALID_MD], 1) == ""
    assert _fix_passage_links_in_response("text", [], 1) == "text"


def test_fix_passage_converts_link_to_image():
    content = "See [Fig 2](passage:42:3:abcdef012345) here."
    out = _fix_passage_links_in_response(content, [VALID_MD], 1)
    assert "![Fig 2](passage:42:3:abcdef012345)" in out


def test_fix_passage_corrects_invalid_hash():
    content = "![Fig 2](passage:42:3:bad)"  # wrong-length hash
    out = _fix_passage_links_in_response(content, [VALID_MD], 1)
    assert "abcdef012345" in out


def test_fix_passage_removes_unfixable_link():
    content = "![Fig 9](passage:99:9:000000000000)"  # no valid passage for 99:9
    out = _fix_passage_links_in_response(content, [VALID_MD], 1)
    assert "*[Fig 9]*" in out


# =========================================================================== #
# execute_tool — dispatch + tool bodies
# =========================================================================== #
async def test_execute_tool_unknown(mock_db):
    res = await execute_tool("does_not_exist", {}, 1, mock_db)
    assert "Unknown tool" in res["error"]


async def test_execute_tool_outer_exception_rollback(mock_db):
    # Force the body to raise so the outer except + rollback path runs.
    mock_db.execute.side_effect = RuntimeError("db down")
    res = await execute_tool("get_overview_stats", {}, 1, mock_db)
    assert "failed" in res["error"].lower()
    mock_db.rollback.assert_awaited()


async def test_execute_tool_outer_exception_rollback_also_fails(mock_db):
    mock_db.execute.side_effect = RuntimeError("db down")
    mock_db.rollback.side_effect = RuntimeError("rollback boom")
    res = await execute_tool("get_overview_stats", {}, 1, mock_db)
    assert "failed" in res["error"].lower()


# --- get_overview_stats ---------------------------------------------------- #
async def test_get_overview_stats_computes_and_caches(mock_db):
    StatsCache._cache.clear()
    row = SimpleNamespace(img=5, cell=20)
    mock_db.execute.side_effect = [
        make_result(scalar=3),                # experiment count
        make_result(first=row),               # img/cell counts
        make_result(scalar=2),                # doc count
        make_result(scalar=1),                # memory count
    ]
    res = await execute_tool("get_overview_stats", {}, 7, mock_db)
    assert res == {
        "total_experiments": 3, "total_images": 5, "total_cells": 20,
        "total_documents": 2, "total_memories": 1,
    }
    # Second call hits the cache (no further execute calls needed)
    res2 = await execute_tool("get_overview_stats", {}, 7, mock_db)
    assert res2 == res


async def test_get_overview_stats_null_counts(mock_db):
    StatsCache._cache.clear()
    mock_db.execute.side_effect = [
        make_result(scalar=None),
        make_result(first=None),   # row is None -> images/cells default to 0
        make_result(scalar=None),
        make_result(scalar=None),
    ]
    res = await execute_tool("get_overview_stats", {}, 8, mock_db)
    assert res["total_experiments"] == 0
    assert res["total_images"] == 0 and res["total_cells"] == 0


# --- list_experiments ------------------------------------------------------ #
async def test_list_experiments(mock_db):
    protein = SimpleNamespace(name="PRC1")
    exp = SimpleNamespace(id=1, name="E1", description="d",
                          status=SimpleNamespace(value="active"), map_protein=protein)
    exp2 = SimpleNamespace(id=2, name="E2", description=None,
                           status="raw", map_protein=None)
    mock_db.execute.return_value = make_result(scalars_all=[exp, exp2])
    res = await execute_tool("list_experiments", {"limit": 5}, 1, mock_db)
    assert res["experiments"][0] == {"id": 1, "name": "E1", "description": "d",
                                     "status": "active", "protein": "PRC1"}
    assert res["experiments"][1]["status"] == "raw"
    assert res["experiments"][1]["protein"] is None


# --- list_images ----------------------------------------------------------- #
async def test_list_images(mock_db):
    img = SimpleNamespace(id=10, original_filename="a.tif", experiment_id=3,
                          experiment=SimpleNamespace(name="Exp"), width=100, height=200)
    mock_db.execute.return_value = make_result(scalars_all=[img])
    res = await execute_tool("list_images", {"experiment_id": 3, "random": True}, 1, mock_db)
    assert res["images"][0]["id"] == 10
    assert res["images"][0]["thumbnail_url"] == "/api/images/10/file?type=thumbnail"


async def test_list_images_no_experiment(mock_db):
    img = SimpleNamespace(id=11, original_filename="b.tif", experiment_id=None,
                          experiment=None, width=None, height=None)
    mock_db.execute.return_value = make_result(scalars_all=[img])
    res = await execute_tool("list_images", {}, 1, mock_db)
    assert res["images"][0]["experiment_name"] is None


# --- list_documents -------------------------------------------------------- #
async def test_list_documents(mock_db):
    doc = SimpleNamespace(id=1, name="paper.pdf", file_type="pdf",
                          page_count=10, status="indexed")
    mock_db.execute.return_value = make_result(scalars_all=[doc])
    res = await execute_tool("list_documents", {}, 1, mock_db)
    assert res["documents"][0]["name"] == "paper.pdf"


# --- get_documents_summary ------------------------------------------------- #
async def test_get_documents_summary(mock_db):
    with patch.object(svc, "get_all_documents_summary",
                      new=AsyncMock(return_value=[{"id": 1}])):
        res = await execute_tool("get_documents_summary", {}, 1, mock_db)
    assert res["documents"] == [{"id": 1}]


# --- semantic_search ------------------------------------------------------- #
async def test_semantic_search(mock_db):
    fake = {
        "query": "tubulin",
        "documents": [{"document_id": 1, "page_number": 2}],
        "fov_images": [{"image_id": 9}],
    }
    with patch.object(svc, "combined_search", new=AsyncMock(return_value=fake)):
        res = await execute_tool("semantic_search", {"query": "tubulin"}, 1, mock_db)
    assert res["document_results"]["count"] == 1
    assert res["image_results"]["count"] == 1


async def test_semantic_search_missing_query(mock_db):
    res = await execute_tool("semantic_search", {}, 1, mock_db)
    assert res["error"] == "query required"


# --- search_documents / search_fov_images ---------------------------------- #
async def test_search_documents(mock_db):
    with patch.object(svc, "search_documents",
                      new=AsyncMock(return_value=[{"document_id": 1}])):
        res = await execute_tool("search_documents", {"query": "x"}, 1, mock_db)
    assert res["results"] == [{"document_id": 1}]


async def test_search_fov_images(mock_db):
    with patch.object(svc, "search_fov_images",
                      new=AsyncMock(return_value=[{"image_id": 1}])):
        res = await execute_tool("search_fov_images",
                                 {"query": "cell", "experiment_id": 2}, 1, mock_db)
    assert res["results"] == [{"image_id": 1}]


# --- get_document_content -------------------------------------------------- #
async def test_get_document_content_missing_id(mock_db):
    res = await execute_tool("get_document_content", {}, 1, mock_db)
    assert res["error"] == "document_id required"


async def test_get_document_content_not_found(mock_db):
    with patch.object(svc, "get_document_content", new=AsyncMock(return_value=None)):
        res = await execute_tool("get_document_content", {"document_id": 5}, 1, mock_db)
    assert res["error"] == "Document not found"


async def test_get_document_content_ok(mock_db):
    payload = {"id": 5, "name": "p.pdf", "pages": [{"page_number": 1}]}
    with patch.object(svc, "get_document_content", new=AsyncMock(return_value=payload)):
        res = await execute_tool("get_document_content",
                                 {"document_id": 5, "page_numbers": [1]}, 1, mock_db)
    assert res == payload


# --- get_experiment_stats -------------------------------------------------- #
async def test_get_experiment_stats_missing_id(mock_db):
    res = await execute_tool("get_experiment_stats", {}, 1, mock_db)
    assert res["error"] == "experiment_id required"


async def test_get_experiment_stats_not_found(mock_db):
    mock_db.execute.return_value = make_result(first=None)
    res = await execute_tool("get_experiment_stats", {"experiment_id": 9}, 1, mock_db)
    assert res["error"] == "Experiment not found"


async def test_get_experiment_stats_ok(mock_db):
    exp = SimpleNamespace(id=9, name="E", description="d",
                          status=SimpleNamespace(value="active"),
                          map_protein=SimpleNamespace(name="PRC1"),
                          created_at=datetime(2024, 1, 1))
    mock_db.execute.return_value = make_result(first=(exp, 3, 12))
    res = await execute_tool("get_experiment_stats", {"experiment_id": 9}, 1, mock_db)
    assert res["image_count"] == 3 and res["cell_count"] == 12
    assert res["protein"] == "PRC1"
    assert res["created_at"] == "2024-01-01T00:00:00"


async def test_get_experiment_stats_no_protein_no_created(mock_db):
    exp = SimpleNamespace(id=9, name="E", description=None, status="raw",
                          map_protein=None, created_at=None)
    mock_db.execute.return_value = make_result(first=(exp, None, None))
    res = await execute_tool("get_experiment_stats", {"experiment_id": 9}, 1, mock_db)
    assert res["protein"] is None and res["created_at"] is None
    assert res["image_count"] == 0 and res["cell_count"] == 0


# --- get_protein_info ------------------------------------------------------ #
async def test_get_protein_info_by_id(mock_db):
    p = SimpleNamespace(id=1, name="PRC1", full_name="Protein", uniprot_id="P1",
                        gene_name="g", organism="human")
    mock_db.execute.return_value = make_result(scalar=p)
    res = await execute_tool("get_protein_info", {"protein_id": 1}, 1, mock_db)
    assert res["name"] == "PRC1" and res["organism"] == "human"


async def test_get_protein_info_by_name(mock_db):
    p = SimpleNamespace(id=2, name="HMMR", full_name=None, uniprot_id=None,
                        gene_name=None, organism=None)
    mock_db.execute.return_value = make_result(scalar=p)
    res = await execute_tool("get_protein_info", {"protein_name": "HMMR"}, 1, mock_db)
    assert res["protein_id"] == 2


async def test_get_protein_info_none_args(mock_db):
    res = await execute_tool("get_protein_info", {}, 1, mock_db)
    assert res["error"] == "protein_id or protein_name required"


async def test_get_protein_info_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    res = await execute_tool("get_protein_info", {"protein_id": 99}, 1, mock_db)
    assert res["error"] == "Protein not found"


# --- get_cell_detection_results -------------------------------------------- #
async def test_get_cell_detection_missing_id(mock_db):
    res = await execute_tool("get_cell_detection_results", {}, 1, mock_db)
    assert res["error"] == "image_id required"


async def test_get_cell_detection_image_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    res = await execute_tool("get_cell_detection_results", {"image_id": 5}, 1, mock_db)
    assert res["error"] == "Image not found"


async def test_get_cell_detection_with_crops(mock_db):
    img = SimpleNamespace(original_filename="im.tif")
    crop = SimpleNamespace(id=1, bbox_x=0, bbox_y=0, bbox_w=10, bbox_h=20,
                           detection_confidence=0.9)
    mock_db.execute.side_effect = [
        make_result(scalar=img),
        make_result(scalars_all=[crop]),
    ]
    res = await execute_tool("get_cell_detection_results",
                             {"image_id": 5, "include_crops": True}, 1, mock_db)
    assert res["cell_count"] == 1
    assert res["detection_summary"]["avg_confidence"] == 0.9
    assert res["crops"][0]["thumbnail_url"] == "/api/images/crops/1/image"


async def test_get_cell_detection_no_crops_empty(mock_db):
    img = SimpleNamespace(original_filename="im.tif")
    mock_db.execute.side_effect = [
        make_result(scalar=img),
        make_result(scalars_all=[]),
    ]
    res = await execute_tool("get_cell_detection_results", {"image_id": 5}, 1, mock_db)
    assert res["cell_count"] == 0
    assert res["detection_summary"]["avg_confidence"] == 0
    assert "crops" not in res


# --- execute_python_code --------------------------------------------------- #
async def test_execute_python_code_missing_code(mock_db):
    res = await execute_tool("execute_python_code", {}, 1, mock_db)
    assert res["error"] == "code required"


async def test_execute_python_code_with_plots(mock_db):
    fake_exec = AsyncMock(return_value={"plots": ["/uploads/temp/plot_1.png"],
                                        "stdout": "ok"})
    with patch("services.code_execution_service.execute_python_code", new=fake_exec):
        res = await execute_tool("execute_python_code",
                                 {"code": "print(1)"}, 1, mock_db)
    assert res["plots_markdown"] == ["![Plot 1](/uploads/temp/plot_1.png)"]
    assert "display_instruction" in res


async def test_execute_python_code_no_plots(mock_db):
    fake_exec = AsyncMock(return_value={"stdout": "42", "plots": []})
    with patch("services.code_execution_service.execute_python_code", new=fake_exec):
        res = await execute_tool("execute_python_code", {"code": "1+1"}, 1, mock_db)
    assert "plots_markdown" not in res


# --- query_database -------------------------------------------------------- #
async def test_query_database_missing_query(mock_db):
    res = await execute_tool("query_database", {}, 1, mock_db)
    assert res["error"] == "query required"


async def test_query_database_non_select(mock_db):
    res = await execute_tool("query_database",
                             {"query": "UPDATE experiments SET x=1"}, 1, mock_db)
    # forbidden keyword check or non-SELECT both reject; either is fine
    assert "error" in res


async def test_query_database_forbidden_keyword(mock_db):
    res = await execute_tool("query_database",
                             {"query": "SELECT * FROM experiments; DROP TABLE x"}, 1, mock_db)
    assert "error" in res


async def test_query_database_semicolon(mock_db):
    res = await execute_tool("query_database",
                             {"query": "SELECT id FROM experiments WHERE a=1;"}, 1, mock_db)
    # ";" trailing -> forbidden via either DROP-style or semicolon check
    assert "error" in res


async def test_query_database_subquery_blocked(mock_db):
    res = await execute_tool(
        "query_database",
        {"query": "SELECT id FROM experiments WHERE id IN (SELECT id FROM images)"},
        1, mock_db)
    assert res["error"] == "Subqueries not allowed"


async def test_query_database_union_blocked(mock_db):
    # Two SELECTs -> caught by the subquery (select_count > 1) check first.
    res = await execute_tool(
        "query_database",
        {"query": "SELECT id FROM experiments UNION SELECT id FROM images"}, 1, mock_db)
    assert "error" in res


async def test_query_database_union_single_select_branch(mock_db):
    # Single SELECT + UNION -> reaches the dedicated UNION rejection branch.
    res = await execute_tool(
        "query_database",
        {"query": "SELECT id FROM experiments UNION foo"}, 1, mock_db)
    assert res["error"] == "UNION/INTERSECT/EXCEPT not allowed"


async def test_query_database_with_cte_blocked(mock_db):
    # Two SELECTs -> caught by subquery check.
    res = await execute_tool(
        "query_database",
        {"query": "WITH x AS (SELECT 1) SELECT * FROM x"}, 1, mock_db)
    assert "error" in res


async def test_query_database_with_cte_single_select_branch(mock_db):
    # Single SELECT WITH clause -> reaches the dedicated CTE rejection branch.
    res = await execute_tool(
        "query_database",
        {"query": "WITH cte AS (1) SELECT id FROM experiments"}, 1, mock_db)
    assert res["error"] == "WITH (CTE) queries not allowed"


async def test_query_database_blocks_non_whitelisted_table(mock_db):
    # `users` (password hashes!) is not in ALLOWED_SQL_TABLES -> denied, and the
    # query never reaches the DB.
    res = await execute_tool("query_database",
                             {"query": "SELECT * FROM users"}, 1, mock_db)
    assert "Access denied" in res["error"] and "users" in res["error"]
    mock_db.execute.assert_not_awaited()


async def test_query_database_indirect_table_requires_anchor(mock_db):
    # images/cell_crops have no user_id column -> must JOIN experiments so the
    # per-user filter can scope them. A bare query is rejected (no data leak).
    res = await execute_tool("query_database",
                             {"query": "SELECT * FROM cell_crops"}, 1, mock_db)
    assert "must JOIN experiments" in res["error"]
    mock_db.execute.assert_not_awaited()


async def test_query_database_direct_scoped_table_is_filtered(mock_db):
    # A directly user-scoped table (agent_memories) is filtered on user_id even
    # though it is neither experiments nor rag_documents.
    result = make_result(fetchall=[]); result.keys.return_value = []
    mock_db.execute.return_value = result
    res = await execute_tool("query_database",
                             {"query": "SELECT key FROM agent_memories"}, 9, mock_db)
    assert res["success"] is True
    stmt = str(mock_db.execute.await_args.args[0])
    assert "agent_memories.user_id = :user_id" in stmt
    assert mock_db.execute.await_args.args[1]["user_id"] == 9


async def test_query_database_parse_error(mock_db):
    with patch.object(svc.sqlparse, "parse", side_effect=ValueError("boom")):
        res = await execute_tool("query_database",
                                 {"query": "SELECT 1 FROM experiments"}, 1, mock_db)
    assert "Parse error" in res["error"]


async def test_query_database_success_injects_filter(mock_db):
    mock_db.execute.return_value = make_result(fetchall=[(1, "n")])
    mock_db.execute.return_value.keys.return_value = ["id", "name"]
    res = await execute_tool(
        "query_database",
        {"query": "SELECT id, name FROM experiments"}, 7, mock_db)
    assert res["success"] is True
    assert res["columns"] == ["id", "name"]
    assert res["rows"] == [{"id": 1, "name": "n"}]
    assert res["row_count"] == 1
    # the user_id filter must actually be injected into the executed SQL (not just
    # assumed) and bound to the caller's id
    stmt = str(mock_db.execute.await_args.args[0])
    assert "experiments.user_id = :user_id" in stmt
    assert mock_db.execute.await_args.args[1]["user_id"] == 7


async def test_query_database_success_doc_filter_and_limit(mock_db):
    result = make_result(fetchall=[])
    result.keys.return_value = ["id"]
    mock_db.execute.return_value = result
    res = await execute_tool(
        "query_database",
        {"query": "SELECT id FROM rag_documents LIMIT 5"}, 7, mock_db)
    assert res["success"] is True


async def test_query_database_execution_error_rollback(mock_db):
    mock_db.execute.side_effect = RuntimeError("exec failed")
    res = await execute_tool("query_database",
                             {"query": "SELECT id FROM experiments"}, 1, mock_db)
    assert "Query error" in res["error"]
    mock_db.rollback.assert_awaited()


async def test_query_database_execution_error_rollback_fails(mock_db):
    mock_db.execute.side_effect = RuntimeError("exec failed")
    mock_db.rollback.side_effect = RuntimeError("rollback failed")
    res = await execute_tool("query_database",
                             {"query": "SELECT id FROM experiments"}, 1, mock_db)
    assert "Query error" in res["error"]


# --- export_data ----------------------------------------------------------- #
async def test_export_data_missing_source(mock_db):
    res = await execute_tool("export_data", {}, 1, mock_db)
    assert res["error"] == "data_source required"


async def test_export_data_experiment_missing_id(mock_db):
    res = await execute_tool("export_data", {"data_source": "experiment"}, 1, mock_db)
    assert res["error"] == "experiment_id required"


async def test_export_data_experiment_ok(mock_db):
    with patch("services.data_export_service.export_experiment_data",
               new=AsyncMock(return_value={"download_url": "/x.csv"})), \
         patch("services.data_export_service.export_cell_crops", new=AsyncMock()), \
         patch("services.data_export_service.export_ranking_comparisons", new=AsyncMock()):
        res = await execute_tool("export_data",
                                 {"data_source": "experiment", "experiment_id": 3},
                                 1, mock_db)
    assert res["download_url"] == "/x.csv"


async def test_export_data_cells(mock_db):
    with patch("services.data_export_service.export_experiment_data", new=AsyncMock()), \
         patch("services.data_export_service.export_cell_crops",
               new=AsyncMock(return_value={"ok": True})), \
         patch("services.data_export_service.export_ranking_comparisons", new=AsyncMock()):
        res = await execute_tool("export_data",
                                 {"data_source": "cells", "format": "xlsx"}, 1, mock_db)
    assert res == {"ok": True}


async def test_export_data_comparisons(mock_db):
    with patch("services.data_export_service.export_experiment_data", new=AsyncMock()), \
         patch("services.data_export_service.export_cell_crops", new=AsyncMock()), \
         patch("services.data_export_service.export_ranking_comparisons",
               new=AsyncMock(return_value={"cmp": 1})):
        res = await execute_tool("export_data",
                                 {"data_source": "comparisons"}, 1, mock_db)
    assert res == {"cmp": 1}


async def test_export_data_unknown_source(mock_db):
    with patch("services.data_export_service.export_experiment_data", new=AsyncMock()), \
         patch("services.data_export_service.export_cell_crops", new=AsyncMock()), \
         patch("services.data_export_service.export_ranking_comparisons", new=AsyncMock()):
        res = await execute_tool("export_data", {"data_source": "analysis"}, 1, mock_db)
    assert "Unknown data_source" in res["error"]


# --- batch_export ---------------------------------------------------------- #
async def test_batch_export_missing_ids(mock_db):
    res = await execute_tool("batch_export", {"experiment_ids": []}, 1, mock_db)
    assert "required" in res["error"]


async def test_batch_export_some_missing(mock_db):
    mock_db.execute.return_value = make_result(scalars_all=[SimpleNamespace(id=1)])
    res = await execute_tool("batch_export", {"experiment_ids": [1, 2]}, 1, mock_db)
    assert "not found" in res["error"]


async def test_batch_export_xlsx(mock_db, tmp_path):
    exp = SimpleNamespace(id=1, name="E1")
    cell = SimpleNamespace(id=10, bbox_x=0, bbox_y=0, bbox_w=4, bbox_h=5,
                           detection_confidence=0.8, mean_intensity=12.0,
                           original_filename="im.tif")
    mock_db.execute.side_effect = [
        make_result(scalars_all=[exp]),     # experiments
        make_result(fetchall=[cell]),       # cells
    ]
    settings_obj = SimpleNamespace(upload_dir=str(tmp_path))
    with patch.object(svc, "get_settings", return_value=settings_obj):
        res = await execute_tool("batch_export", {"experiment_ids": [1]}, 1, mock_db)
    assert res["success"] is True
    assert res["total_cells"] == 1
    assert res["filename"].endswith(".xlsx")


async def test_batch_export_csv_no_cells(mock_db, tmp_path):
    exp = SimpleNamespace(id=1, name="E1")
    mock_db.execute.side_effect = [
        make_result(scalars_all=[exp]),
        make_result(fetchall=[]),  # no cells -> stats skipped
    ]
    settings_obj = SimpleNamespace(upload_dir=str(tmp_path))
    with patch.object(svc, "get_settings", return_value=settings_obj):
        res = await execute_tool(
            "batch_export",
            {"experiment_ids": [1], "format": "csv", "include_cells": False},
            1, mock_db)
    assert res["success"] is True
    assert res["filename"].endswith(".csv")


# --- manage_experiment ----------------------------------------------------- #
async def test_manage_experiment_create_missing_name(mock_db):
    res = await execute_tool("manage_experiment", {"action": "create"}, 1, mock_db)
    assert res["error"] == "name required"


async def test_manage_experiment_create_ok(mock_db):
    async def fake_refresh(obj):
        obj.id = 55
        obj.name = "New"
    mock_db.refresh.side_effect = fake_refresh
    res = await execute_tool("manage_experiment",
                             {"action": "create", "name": "New"}, 1, mock_db)
    assert res["success"] is True and res["experiment_id"] == 55
    mock_db.add.assert_called_once()


async def test_manage_experiment_update_missing_id(mock_db):
    res = await execute_tool("manage_experiment", {"action": "update"}, 1, mock_db)
    assert res["error"] == "experiment_id required"


async def test_manage_experiment_update_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    res = await execute_tool("manage_experiment",
                             {"action": "update", "experiment_id": 9}, 1, mock_db)
    assert res["error"] == "Not found"


async def test_manage_experiment_update_ok(mock_db):
    exp = SimpleNamespace(id=9, name="old", description="d", map_protein_id=None)
    mock_db.execute.return_value = make_result(scalar=exp)
    res = await execute_tool(
        "manage_experiment",
        {"action": "update", "experiment_id": 9, "name": "new",
         "description": "nd", "protein_id": 2}, 1, mock_db)
    assert res["success"] is True
    assert exp.name == "new" and exp.description == "nd" and exp.map_protein_id == 2


async def test_manage_experiment_archive_missing_id(mock_db):
    res = await execute_tool("manage_experiment", {"action": "archive"}, 1, mock_db)
    assert res["error"] == "experiment_id required"


async def test_manage_experiment_archive_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    res = await execute_tool("manage_experiment",
                             {"action": "archive", "experiment_id": 9}, 1, mock_db)
    assert res["error"] == "Not found"


async def test_manage_experiment_archive_ok(mock_db):
    exp = SimpleNamespace(id=9, status="active")
    mock_db.execute.return_value = make_result(scalar=exp)
    res = await execute_tool("manage_experiment",
                             {"action": "archive", "experiment_id": 9}, 1, mock_db)
    assert res["archived"] is True and exp.status == "archived"


async def test_manage_experiment_unknown_action(mock_db):
    res = await execute_tool("manage_experiment", {"action": "weird"}, 1, mock_db)
    assert "Unknown action" in res["error"]


# --- redetect_cells -------------------------------------------------------- #
async def test_redetect_cells_no_input(mock_db):
    with patch("services.image_processor.process_image_background", new=AsyncMock()):
        res = await execute_tool("redetect_cells", {}, 1, mock_db)
    assert "Provide either" in res["error"]


async def test_redetect_cells_experiment_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with patch("services.image_processor.process_image_background", new=AsyncMock()):
        res = await execute_tool("redetect_cells", {"experiment_id": 5}, 1, mock_db)
    assert "Experiment not found" in res["error"]


async def test_redetect_cells_experiment_no_images(mock_db):
    exp = SimpleNamespace(id=5)
    mock_db.execute.side_effect = [
        make_result(scalar=exp),       # experiment lookup
        make_result(scalars_all=[]),   # images in experiment -> none
    ]
    with patch("services.image_processor.process_image_background", new=AsyncMock()):
        res = await execute_tool("redetect_cells", {"experiment_id": 5}, 1, mock_db)
    assert "No images found" in res["error"]


async def test_redetect_cells_image_ids_none_valid(mock_db):
    mock_db.execute.return_value = make_result(scalars_all=[])
    with patch("services.image_processor.process_image_background", new=AsyncMock()):
        res = await execute_tool("redetect_cells", {"image_ids": [1, 2]}, 1, mock_db)
    assert "No valid images" in res["error"]


async def test_redetect_cells_processes_and_skips(mock_db):
    good = SimpleNamespace(id=1, original_filename="a.tif", source_discarded=False,
                           status=None, detect_cells=False)
    bad = SimpleNamespace(id=2, original_filename="b.tif", source_discarded=True)
    mock_db.execute.return_value = make_result(scalars_all=[good, bad])
    created = []
    with patch("services.image_processor.process_image_background", new=AsyncMock()), \
         patch.object(svc.asyncio, "create_task",
                      side_effect=lambda coro: created.append(coro) or coro.close()):
        res = await execute_tool("redetect_cells", {"image_ids": [1, 2]}, 1, mock_db)
    assert res["queued"] == 1 and res["skipped"] == 1
    assert "processing" in res and "skipped_details" in res
    assert good.status == svc.UploadStatus.PROCESSING


# --- call_external_api ----------------------------------------------------- #
async def test_call_external_api_not_approved(mock_db):
    res = await execute_tool("call_external_api",
                             {"api": "evil", "endpoint": "x"}, 1, mock_db)
    assert "not approved" in res["error"]


def _httpx_client_mock(resp):
    """Build a context-manager AsyncClient mock whose .get returns resp."""
    client = AsyncMock()
    client.get = AsyncMock(return_value=resp)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


async def test_call_external_api_success(mock_db):
    import httpx
    resp = SimpleNamespace(status_code=200, json=lambda: {"acc": "P1"})
    with patch.object(httpx, "AsyncClient", return_value=_httpx_client_mock(resp)):
        res = await execute_tool(
            "call_external_api",
            {"api": "uniprot", "endpoint": "/uniprotkb/P1", "params": {}}, 1, mock_db)
    assert res["success"] is True and res["data"] == {"acc": "P1"}


async def test_call_external_api_rate_limit(mock_db):
    import httpx
    resp = SimpleNamespace(status_code=429, text="too many")
    with patch.object(httpx, "AsyncClient", return_value=_httpx_client_mock(resp)):
        res = await execute_tool("call_external_api",
                                 {"api": "pubmed", "endpoint": "x"}, 1, mock_db)
    assert "rate limit" in res["error"]


async def test_call_external_api_404(mock_db):
    import httpx
    resp = SimpleNamespace(status_code=404, text="nf")
    with patch.object(httpx, "AsyncClient", return_value=_httpx_client_mock(resp)):
        res = await execute_tool("call_external_api",
                                 {"api": "ensembl", "endpoint": "x"}, 1, mock_db)
    assert "not found" in res["error"].lower()


async def test_call_external_api_other_status(mock_db):
    import httpx
    resp = SimpleNamespace(status_code=500, text="boom" * 200)
    with patch.object(httpx, "AsyncClient", return_value=_httpx_client_mock(resp)):
        res = await execute_tool("call_external_api",
                                 {"api": "string-db", "endpoint": "x"}, 1, mock_db)
    assert "status 500" in res["error"]


async def test_call_external_api_timeout(mock_db):
    import httpx
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(side_effect=httpx.TimeoutException("t"))
    cm.__aexit__ = AsyncMock(return_value=False)
    with patch.object(httpx, "AsyncClient", return_value=cm):
        res = await execute_tool("call_external_api",
                                 {"api": "uniprot", "endpoint": "x"}, 1, mock_db)
    assert "timed out" in res["error"]


async def test_call_external_api_connect_error(mock_db):
    import httpx
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(side_effect=httpx.ConnectError("c"))
    cm.__aexit__ = AsyncMock(return_value=False)
    with patch.object(httpx, "AsyncClient", return_value=cm):
        res = await execute_tool("call_external_api",
                                 {"api": "uniprot", "endpoint": "x"}, 1, mock_db)
    assert "Could not connect" in res["error"]


async def test_call_external_api_generic_error(mock_db):
    import httpx
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(side_effect=RuntimeError("weird"))
    cm.__aexit__ = AsyncMock(return_value=False)
    with patch.object(httpx, "AsyncClient", return_value=cm):
        res = await execute_tool("call_external_api",
                                 {"api": "uniprot", "endpoint": "x"}, 1, mock_db)
    assert "failed" in res["error"]


# --- google_search --------------------------------------------------------- #
async def test_google_search_missing_query(mock_db):
    res = await execute_tool("google_search", {}, 1, mock_db)
    assert res["error"] == "query required"


def _search_response(text="A summary", sources=None, queries=None):
    web_chunks = []
    if sources:
        for s in sources:
            web_chunks.append(SimpleNamespace(web=SimpleNamespace(title=s[0], uri=s[1])))
    metadata = SimpleNamespace(
        web_search_queries=queries or [],
        grounding_chunks=web_chunks,
    )
    part = SimpleNamespace(text=text)
    candidate = SimpleNamespace(
        content=SimpleNamespace(parts=[part]),
        grounding_metadata=metadata,
    )
    return SimpleNamespace(candidates=[candidate])


async def test_google_search_success(mock_db):
    resp = _search_response(sources=[("Title", "http://x")], queries=["q1"])
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = resp
    with patch("google.genai.Client", return_value=fake_client):
        res = await execute_tool("google_search", {"query": "weather"}, 1, mock_db)
    assert res["summary"] == "A summary"
    assert res["sources"][0]["url"] == "http://x"
    assert res["search_queries"] == ["q1"]


async def test_google_search_no_metadata(mock_db):
    candidate = SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(text="t")]),
                                grounding_metadata=None)
    resp = SimpleNamespace(candidates=[candidate])
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = resp
    with patch("google.genai.Client", return_value=fake_client):
        res = await execute_tool("google_search", {"query": "x"}, 1, mock_db)
    assert res["summary"] == "t"
    assert "sources" not in res


async def test_google_search_timeout(mock_db):
    import asyncio as aio
    fake_client = MagicMock()
    with patch("google.genai.Client", return_value=fake_client), \
         patch.object(svc.asyncio, "wait_for", side_effect=aio.TimeoutError()):
        res = await execute_tool("google_search", {"query": "x"}, 1, mock_db)
    assert "timed out" in res["error"]


async def test_google_search_generic_error(mock_db):
    with patch("google.genai.Client", side_effect=RuntimeError("kaboom")):
        res = await execute_tool("google_search", {"query": "x"}, 1, mock_db)
    assert "Search failed" in res["error"]


# --- browse_webpage -------------------------------------------------------- #
async def test_browse_webpage_missing_url(mock_db):
    res = await execute_tool("browse_webpage", {}, 1, mock_db)
    assert res["error"] == "url required"


async def test_browse_webpage_ssrf_blocked(mock_db):
    res = await execute_tool("browse_webpage",
                             {"url": "http://localhost/x"}, 1, mock_db)
    assert "localhost" in res["error"]


def _browse_client(get_side_effect):
    client = AsyncMock()
    client.get = AsyncMock(side_effect=get_side_effect)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, client


async def test_browse_webpage_text_ok(mock_db, monkeypatch):
    import httpx
    import socket
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))])
    html = "<html><body><p>Hello</p><a href='/l'>link</a>" \
           "<table><tr><td>c1</td></tr></table><script>x</script></body></html>"
    resp = SimpleNamespace(status_code=200, text=html, headers={})
    cm, _ = _browse_client([resp])
    with patch.object(httpx, "AsyncClient", return_value=cm):
        res = await execute_tool("browse_webpage",
                                 {"url": "https://example.com", "extract": "all"},
                                 1, mock_db)
    assert "Hello" in res["text"]
    assert res["links"][0]["href"] == "/l"
    assert res["tables"][0][0][0] == "c1"


async def test_browse_webpage_redirect_then_ok(mock_db, monkeypatch):
    import httpx
    import socket
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))])
    redirect = SimpleNamespace(status_code=302, headers={"location": "https://example.com/final"})
    final = SimpleNamespace(status_code=200, text="<p>Final</p>", headers={})
    cm, _ = _browse_client([redirect, final])
    with patch.object(httpx, "AsyncClient", return_value=cm):
        res = await execute_tool("browse_webpage",
                                 {"url": "https://example.com"}, 1, mock_db)
    assert "Final" in res["text"]


async def test_browse_webpage_redirect_no_location(mock_db, monkeypatch):
    import httpx
    import socket
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))])
    redirect = SimpleNamespace(status_code=302, headers={})
    cm, _ = _browse_client([redirect])
    with patch.object(httpx, "AsyncClient", return_value=cm):
        res = await execute_tool("browse_webpage",
                                 {"url": "https://example.com"}, 1, mock_db)
    assert "location header" in res["error"]


async def test_browse_webpage_redirect_blocked(mock_db, monkeypatch):
    import httpx
    import socket
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))])
    redirect = SimpleNamespace(status_code=302, headers={"location": "http://localhost/evil"})
    cm, _ = _browse_client([redirect])
    with patch.object(httpx, "AsyncClient", return_value=cm):
        res = await execute_tool("browse_webpage",
                                 {"url": "https://example.com"}, 1, mock_db)
    assert "Redirect blocked" in res["error"]


async def test_browse_webpage_non_200(mock_db, monkeypatch):
    import httpx
    import socket
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))])
    resp = SimpleNamespace(status_code=403, text="", headers={})
    cm, _ = _browse_client([resp])
    with patch.object(httpx, "AsyncClient", return_value=cm):
        res = await execute_tool("browse_webpage",
                                 {"url": "https://example.com"}, 1, mock_db)
    assert "HTTP 403" in res["error"]


async def test_browse_webpage_timeout(mock_db, monkeypatch):
    import httpx
    import socket
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))])
    cm, _ = _browse_client(httpx.TimeoutException("t"))
    with patch.object(httpx, "AsyncClient", return_value=cm):
        res = await execute_tool("browse_webpage",
                                 {"url": "https://example.com"}, 1, mock_db)
    assert "timed out" in res["error"]


async def test_browse_webpage_connect_error(mock_db, monkeypatch):
    import httpx
    import socket
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))])
    cm, _ = _browse_client(httpx.ConnectError("c"))
    with patch.object(httpx, "AsyncClient", return_value=cm):
        res = await execute_tool("browse_webpage",
                                 {"url": "https://example.com"}, 1, mock_db)
    assert "Could not connect" in res["error"]


async def test_browse_webpage_generic_error(mock_db, monkeypatch):
    import httpx
    import socket
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))])
    cm, _ = _browse_client(RuntimeError("weird"))
    with patch.object(httpx, "AsyncClient", return_value=cm):
        res = await execute_tool("browse_webpage",
                                 {"url": "https://example.com"}, 1, mock_db)
    assert "Failed to fetch page" in res["error"]


# --- get_segmentation_masks ------------------------------------------------ #
async def test_get_segmentation_masks_no_args(mock_db):
    res = await execute_tool("get_segmentation_masks", {}, 1, mock_db)
    assert "Provide crop_ids or image_id" in res["error"]


async def test_get_segmentation_masks_crop_ids(mock_db):
    mask = SimpleNamespace(cell_crop_id=1, polygon_points=[[0, 0]], area_pixels=10,
                           iou_score=0.9, creation_method="sam", prompt_count=1)
    mock_db.execute.return_value = make_result(scalars_all=[mask])
    res = await execute_tool("get_segmentation_masks", {"crop_ids": [1, 2]}, 1, mock_db)
    assert res["masks_found"] == 1
    assert res["masks_missing"] == 1
    assert res["cell_masks"][0]["crop_id"] == 1


async def test_get_segmentation_masks_image_with_mask(mock_db):
    img = SimpleNamespace(id=5)
    fov = SimpleNamespace(image_id=5, polygon_points=[[1, 1]], area_pixels=99,
                          iou_score=0.8, creation_method="sam")
    mock_db.execute.side_effect = [
        make_result(scalar=img),   # ownership check
        make_result(scalar=fov),   # fov mask
    ]
    res = await execute_tool("get_segmentation_masks", {"image_id": 5}, 1, mock_db)
    assert res["fov_mask"]["area_pixels"] == 99


async def test_get_segmentation_masks_image_no_mask(mock_db):
    img = SimpleNamespace(id=5)
    mock_db.execute.side_effect = [
        make_result(scalar=img),
        make_result(scalar=None),  # no fov mask
    ]
    res = await execute_tool("get_segmentation_masks", {"image_id": 5}, 1, mock_db)
    assert res["fov_mask"] is None
    assert "fov_mask_status" in res


async def test_get_segmentation_masks_image_access_denied(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)  # ownership fails
    res = await execute_tool("get_segmentation_masks", {"image_id": 5}, 1, mock_db)
    assert "access denied" in res["error"]


# --- render_segmentation_overlay ------------------------------------------- #
async def test_render_overlay_no_args(mock_db, tmp_path):
    settings_obj = SimpleNamespace(upload_dir=str(tmp_path))
    with patch.object(svc, "get_settings", return_value=settings_obj):
        res = await execute_tool("render_segmentation_overlay", {}, 1, mock_db)
    assert "Provide image_id or crop_id" in res["error"]


async def test_render_overlay_crop_not_found(mock_db, tmp_path):
    settings_obj = SimpleNamespace(upload_dir=str(tmp_path))
    mock_db.execute.return_value = make_result(scalar=None)
    with patch.object(svc, "get_settings", return_value=settings_obj):
        res = await execute_tool("render_segmentation_overlay", {"crop_id": 1}, 1, mock_db)
    assert "access denied" in res["error"]


async def test_render_overlay_crop_wrong_owner(mock_db, tmp_path):
    settings_obj = SimpleNamespace(upload_dir=str(tmp_path))
    crop = SimpleNamespace(
        id=1, image=SimpleNamespace(experiment=SimpleNamespace(user_id=999)))
    mock_db.execute.return_value = make_result(scalar=crop)
    with patch.object(svc, "get_settings", return_value=settings_obj):
        res = await execute_tool("render_segmentation_overlay", {"crop_id": 1}, 1, mock_db)
    assert "access denied" in res["error"]


async def test_render_overlay_crop_no_mask(mock_db, tmp_path):
    settings_obj = SimpleNamespace(upload_dir=str(tmp_path))
    crop = SimpleNamespace(
        id=1, image=SimpleNamespace(experiment=SimpleNamespace(user_id=1)))
    mock_db.execute.side_effect = [
        make_result(scalar=crop),
        make_result(scalar=None),  # no mask
    ]
    with patch.object(svc, "get_settings", return_value=settings_obj):
        res = await execute_tool("render_segmentation_overlay", {"crop_id": 1}, 1, mock_db)
    assert "No segmentation mask" in res["error"]


async def test_render_overlay_crop_image_missing(mock_db, tmp_path):
    settings_obj = SimpleNamespace(upload_dir=str(tmp_path))
    crop = SimpleNamespace(
        id=1, bbox_x=0, bbox_y=0, mip_path=None,
        image=SimpleNamespace(experiment=SimpleNamespace(user_id=1)))
    mask = SimpleNamespace(polygon_points=[[0, 0], [1, 1], [2, 2]],
                           area_pixels=1, iou_score=1)
    mock_db.execute.side_effect = [
        make_result(scalar=crop),
        make_result(scalar=mask),
    ]
    with patch.object(svc, "get_settings", return_value=settings_obj):
        res = await execute_tool("render_segmentation_overlay", {"crop_id": 1}, 1, mock_db)
    assert "Crop image file not found" in res["error"]


async def test_render_overlay_crop_success(mock_db, tmp_path):
    settings_obj = SimpleNamespace(upload_dir=str(tmp_path))
    # Real PNG file for PIL to open
    from PIL import Image as PILImage
    crop_path = tmp_path / "crop.png"
    PILImage.new("RGB", (20, 20), (10, 10, 10)).save(crop_path)
    crop = SimpleNamespace(
        id=1, bbox_x=0, bbox_y=0, mip_path=str(crop_path),
        image=SimpleNamespace(experiment=SimpleNamespace(user_id=1)))
    mask = SimpleNamespace(polygon_points=[[1, 1], [5, 1], [5, 5]],
                           area_pixels=12, iou_score=0.77)
    mock_db.execute.side_effect = [
        make_result(scalar=crop),
        make_result(scalar=mask),
    ]
    with patch.object(svc, "get_settings", return_value=settings_obj):
        res = await execute_tool("render_segmentation_overlay",
                                 {"crop_id": 1, "color": "red"}, 1, mock_db)
    assert res["success"] is True
    assert res["image_markdown"].startswith("![Segmentation](/uploads/temp/")
    assert res["mask_iou_score"] == 0.77


async def test_render_overlay_image_not_found(mock_db, tmp_path):
    settings_obj = SimpleNamespace(upload_dir=str(tmp_path))
    mock_db.execute.return_value = make_result(scalar=None)
    with patch.object(svc, "get_settings", return_value=settings_obj):
        res = await execute_tool("render_segmentation_overlay", {"image_id": 5}, 1, mock_db)
    assert "access denied" in res["error"]


async def test_render_overlay_image_no_mask(mock_db, tmp_path):
    settings_obj = SimpleNamespace(upload_dir=str(tmp_path))
    image = SimpleNamespace(id=5, mip_path=None, file_path=None)
    mock_db.execute.side_effect = [
        make_result(scalar=image),
        make_result(scalar=None),
    ]
    with patch.object(svc, "get_settings", return_value=settings_obj):
        res = await execute_tool("render_segmentation_overlay", {"image_id": 5}, 1, mock_db)
    assert "No FOV segmentation mask" in res["error"]


async def test_render_overlay_image_file_missing(mock_db, tmp_path):
    settings_obj = SimpleNamespace(upload_dir=str(tmp_path))
    image = SimpleNamespace(id=5, mip_path=None, file_path=None)
    mask = SimpleNamespace(polygon_points=[[0, 0], [1, 1], [2, 2]],
                           area_pixels=1, iou_score=1)
    mock_db.execute.side_effect = [
        make_result(scalar=image),
        make_result(scalar=mask),
    ]
    with patch.object(svc, "get_settings", return_value=settings_obj):
        res = await execute_tool("render_segmentation_overlay", {"image_id": 5}, 1, mock_db)
    assert "Image file not found" in res["error"]


async def test_render_overlay_image_multi_polygon_success(mock_db, tmp_path):
    settings_obj = SimpleNamespace(upload_dir=str(tmp_path))
    from PIL import Image as PILImage
    img_path = tmp_path / "fov.png"
    PILImage.new("RGB", (30, 30), (5, 5, 5)).save(img_path)
    image = SimpleNamespace(id=5, mip_path=str(img_path), file_path=None)
    # Multiple polygons: [[[x,y],...], [[x,y],...]]
    mask = SimpleNamespace(
        polygon_points=[[[1, 1], [10, 1], [10, 10]], [[15, 15], [20, 15], [20, 20]]],
        area_pixels=50, iou_score=0.6)
    mock_db.execute.side_effect = [
        make_result(scalar=image),
        make_result(scalar=mask),
    ]
    with patch.object(svc, "get_settings", return_value=settings_obj):
        res = await execute_tool("render_segmentation_overlay",
                                 {"image_id": 5, "show_fill": False}, 1, mock_db)
    assert res["success"] is True


# --- long_term_memory ------------------------------------------------------ #
async def test_memory_store_missing(mock_db):
    res = await execute_tool("long_term_memory", {"action": "store"}, 1, mock_db)
    assert "key and value required" in res["error"]


async def test_memory_store_new(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)  # no existing
    res = await execute_tool(
        "long_term_memory",
        {"action": "store", "key": "k", "value": "v"}, 1, mock_db)
    assert res["action"] == "stored"
    mock_db.add.assert_called_once()


async def test_memory_store_update_existing(mock_db):
    existing = SimpleNamespace(value="old", memory_type="note", updated_at=None)
    mock_db.execute.return_value = make_result(scalar=existing)
    res = await execute_tool(
        "long_term_memory",
        {"action": "store", "key": "k", "value": "new", "memory_type": "finding"},
        1, mock_db)
    assert res["success"] is True and res["action"] == "stored"
    assert existing.value == "new" and existing.memory_type == "finding"
    assert existing.updated_at is not None  # set via module-level datetime.now()


async def test_memory_retrieve_missing_key(mock_db):
    res = await execute_tool("long_term_memory", {"action": "retrieve"}, 1, mock_db)
    assert res["error"] == "key required"


async def test_memory_retrieve_found(mock_db):
    m = SimpleNamespace(key="k", value="v", memory_type="note", access_count=0,
                        last_accessed_at=None, created_at=datetime(2024, 1, 1))
    mock_db.execute.return_value = make_result(scalar=m)
    res = await execute_tool("long_term_memory",
                             {"action": "retrieve", "key": "k"}, 1, mock_db)
    assert m.access_count == 1 and m.last_accessed_at is not None
    assert res["key"] == "k" and res["value"] == "v" and res["type"] == "note"
    assert res["created_at"] == "2024-01-01T00:00:00"


async def test_memory_retrieve_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    res = await execute_tool("long_term_memory",
                             {"action": "retrieve", "key": "k"}, 1, mock_db)
    assert "not found" in res["error"]


async def test_memory_search(mock_db):
    m = SimpleNamespace(key="k", value="some long value here", memory_type="note")
    mock_db.execute.return_value = make_result(scalars_all=[m])
    res = await execute_tool("long_term_memory",
                             {"action": "search", "query": "long"}, 1, mock_db)
    assert res["memories"][0]["key"] == "k"


async def test_memory_list(mock_db):
    m = SimpleNamespace(key="k", memory_type="note", updated_at=datetime(2024, 1, 1))
    mock_db.execute.return_value = make_result(scalars_all=[m])
    res = await execute_tool("long_term_memory", {"action": "list"}, 1, mock_db)
    assert res["memories"][0]["updated"] == "2024-01-01T00:00:00"


async def test_memory_unknown_action(mock_db):
    res = await execute_tool("long_term_memory", {"action": "weird"}, 1, mock_db)
    assert "Unknown action" in res["error"]


# --- extract_document_region ----------------------------------------------- #
async def test_extract_region_missing_document_id(mock_db):
    res = await execute_tool("extract_document_region", {}, 1, mock_db)
    assert res["error"] == "document_id required"


async def test_extract_region_missing_page(mock_db):
    res = await execute_tool("extract_document_region", {"document_id": 1}, 1, mock_db)
    assert res["error"] == "page_number required"


async def test_extract_region_missing_description(mock_db):
    res = await execute_tool("extract_document_region",
                             {"document_id": 1, "page_number": 2}, 1, mock_db)
    assert "description required" in res["error"]


async def test_extract_region_no_passages(mock_db):
    with patch("services.rag_service.extract_relevant_passages",
               new=AsyncMock(return_value=[])):
        res = await execute_tool(
            "extract_document_region",
            {"document_id": 1, "page_number": 2, "description": "Figure 2"}, 1, mock_db)
    assert "Could not find" in res["error"]
    assert "fallback_markdown" in res


async def test_extract_region_full_page(mock_db):
    passage = {"type": "full_page"}
    with patch("services.rag_service.extract_relevant_passages",
               new=AsyncMock(return_value=[passage])):
        res = await execute_tool(
            "extract_document_region",
            {"document_id": 1, "page_number": 2, "description": "Whole page"}, 1, mock_db)
    assert res["type"] == "full_page"
    assert "![Whole page]" in res["markdown"]


async def test_extract_region_success_passage(mock_db):
    passage = {"extracted_text": "Figure [text]\nline", "document_id": 1,
               "page_number": 2, "passage_hash": "abcdef012345",
               "passage_type": "figure", "confidence": 0.8}
    with patch("services.rag_service.extract_relevant_passages",
               new=AsyncMock(return_value=[passage])):
        res = await execute_tool(
            "extract_document_region",
            {"document_id": 1, "page_number": 2, "description": "Figure 2"}, 1, mock_db)
    assert res["success"] is True
    assert "passage:1:2:abcdef012345" in res["markdown"]
    assert res["confidence"] == 0.8


async def test_extract_region_with_bbox(mock_db):
    passage = {"extracted_text": "X", "document_id": 1, "page_number": 2,
               "passage_hash": "abcdef012345", "passage_type": "region"}
    with patch("services.rag_service.extract_passage_image",
               new=AsyncMock(return_value=passage)):
        res = await execute_tool(
            "extract_document_region",
            {"document_id": 1, "page_number": 2, "description": "x",
             "bbox": [0, 0, 100, 100]}, 1, mock_db)
    assert res["success"] is True
    assert res["bbox"] == [0, 0, 100, 100]


async def test_extract_region_bbox_failed(mock_db):
    with patch("services.rag_service.extract_passage_image",
               new=AsyncMock(return_value=None)):
        res = await execute_tool(
            "extract_document_region",
            {"document_id": 1, "page_number": 2, "description": "x",
             "bbox": [0, 0, 100, 100]}, 1, mock_db)
    assert "Failed to extract region" in res["error"]


# =========================================================================== #
# generate_response — agent loop
# =========================================================================== #
def _model_content_with_function_call(name, args):
    """Build a real types.Content (role=model) holding one function_call part."""
    from google.genai import types
    return types.Content(role="model",
                          parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))])


def _model_content_with_text(text):
    from google.genai import types
    return types.Content(role="model", parts=[types.Part(text=text)])


def _fake_response(content, text=None):
    """A response object exposing .candidates, .text, .prompt_feedback."""
    candidate = SimpleNamespace(content=content, finish_reason="STOP")
    return SimpleNamespace(candidates=[candidate], text=text, prompt_feedback=None)


def _patch_client(responses):
    """Patch google.genai.Client so generate_content returns queued responses."""
    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = list(responses)
    return patch("google.genai.Client", return_value=fake_client)


async def test_generate_response_no_api_key(mock_db, monkeypatch):
    monkeypatch.setattr(svc.settings, "gemini_api_key", "")
    res = await generate_response("hi", 1, 100, mock_db)
    assert res["content"] == "AI service is not configured."
    assert res["interaction_id"] is None


async def test_generate_response_text_only(mock_db, monkeypatch):
    monkeypatch.setattr(svc.settings, "gemini_api_key", "fake-key")
    resp = _fake_response(_model_content_with_text("Hello there"))
    with _patch_client([resp]):
        res = await generate_response("hi", 1, 100, mock_db)
    assert res["content"] == "Hello there"
    assert res["interaction_id"].startswith("int_100_")


async def test_generate_response_function_call_then_text(mock_db, monkeypatch):
    monkeypatch.setattr(svc.settings, "gemini_api_key", "fake-key")
    StatsCache._cache.clear()
    # First response asks for a tool; second returns text.
    r1 = _fake_response(_model_content_with_function_call("get_overview_stats", {}))
    r2 = _fake_response(_model_content_with_text("You have 3 experiments."))
    # mock_db serves the 4 queries get_overview_stats needs
    row = SimpleNamespace(img=1, cell=2)
    mock_db.execute.side_effect = [
        make_result(scalar=3), make_result(first=row),
        make_result(scalar=0), make_result(scalar=0),
    ]
    with _patch_client([r1, r2]):
        res = await generate_response("how many?", 5, 100, mock_db)
    assert res["content"] == "You have 3 experiments."
    assert res["tool_calls"][0]["tool"] == "get_overview_stats"


async def test_generate_response_tool_error_then_text(mock_db, monkeypatch):
    monkeypatch.setattr(svc.settings, "gemini_api_key", "fake-key")
    r1 = _fake_response(_model_content_with_function_call("get_experiment_stats", {}))
    r2 = _fake_response(_model_content_with_text("Done"))
    with _patch_client([r1, r2]):
        # missing experiment_id -> tool returns {"error": ...}, loop continues
        res = await generate_response("stats", 1, 100, mock_db)
    assert res["content"] == "Done"
    assert "error" in res["tool_calls"][0]["result"]


async def test_generate_response_search_documents_citations(mock_db, monkeypatch):
    monkeypatch.setattr(svc.settings, "gemini_api_key", "fake-key")
    r1 = _fake_response(_model_content_with_function_call(
        "search_documents", {"query": "tubulin"}))
    r2 = _fake_response(_model_content_with_text("Found it"))
    with _patch_client([r1, r2]), \
         patch.object(svc, "search_documents", new=AsyncMock(return_value=[
             {"document_id": 1, "page_number": 2, "document_name": "paper.pdf",
              "similarity_score": 0.9}])):
        res = await generate_response("search", 1, 100, mock_db)
    assert any(c["type"] == "document" for c in res["citations"])


async def test_generate_response_response_text_fallback(mock_db, monkeypatch):
    monkeypatch.setattr(svc.settings, "gemini_api_key", "fake-key")
    # candidate has no parts -> falls through to response.text fallback
    candidate = SimpleNamespace(content=SimpleNamespace(parts=[]), finish_reason="STOP")
    resp = SimpleNamespace(candidates=[candidate], text="Fallback text",
                           prompt_feedback=None)
    with _patch_client([resp]):
        res = await generate_response("hi", 1, 100, mock_db)
    assert res["content"] == "Fallback text"


async def test_generate_response_no_candidates_breaks(mock_db, monkeypatch):
    monkeypatch.setattr(svc.settings, "gemini_api_key", "fake-key")
    resp = SimpleNamespace(candidates=[], text=None, prompt_feedback="blocked")
    with _patch_client([resp]):
        res = await generate_response("hi", 1, 100, mock_db)
    assert res["content"] == "I wasn't able to generate a response."


async def test_generate_response_api_exception(mock_db, monkeypatch):
    monkeypatch.setattr(svc.settings, "gemini_api_key", "fake-key")
    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = RuntimeError("api boom")
    with patch("google.genai.Client", return_value=fake_client):
        res = await generate_response("hi", 1, 100, mock_db)
    assert "error occurred" in res["content"]


async def test_generate_response_timeout(mock_db, monkeypatch):
    import asyncio as aio
    monkeypatch.setattr(svc.settings, "gemini_api_key", "fake-key")
    fake_client = MagicMock()
    with patch("google.genai.Client", return_value=fake_client), \
         patch.object(svc.asyncio, "wait_for", side_effect=aio.TimeoutError()):
        res = await generate_response("hi", 1, 100, mock_db)
    assert "took too long" in res["content"]


async def test_generate_response_fallback_after_exhaustion(mock_db, monkeypatch):
    monkeypatch.setattr(svc.settings, "gemini_api_key", "fake-key")
    # Always return a function_call -> never produces text -> loop exhausts.
    # Use get_experiment_stats (returns error quickly, no DB needed).
    fc_resp = _fake_response(
        _model_content_with_function_call("get_experiment_stats", {}))
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = fc_resp
    with patch("google.genai.Client", return_value=fake_client):
        res = await generate_response("loop", 1, 100, mock_db)
    # Exhausted with tool calls -> fallback content built from tool results.
    assert res["tool_calls"]
    assert "Completed actions" in res["content"] or "Analysis Results" in res["content"]


async def test_generate_response_get_document_content_vision(mock_db, monkeypatch):
    monkeypatch.setattr(svc.settings, "gemini_api_key", "fake-key")
    r1 = _fake_response(_model_content_with_function_call(
        "get_document_content", {"document_id": 5}))
    r2 = _fake_response(_model_content_with_text("Read the doc"))
    doc_payload = {
        "id": 5, "name": "p.pdf", "total_pages": 1,
        "pages": [{"page_number": 1, "image_base64": "aGk=",
                   "image_mime_type": "image/png"}],
    }
    with _patch_client([r1, r2]), \
         patch.object(svc, "get_document_content",
                      new=AsyncMock(return_value=doc_payload)):
        res = await generate_response("read doc 5", 1, 100, mock_db)
    assert res["content"] == "Read the doc"
    # vision path adds a document citation
    assert any(c["type"] == "document" for c in res["citations"])


async def test_generate_response_genai_import_error(mock_db, monkeypatch):
    # Simulate google.genai not being installed -> early return.
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "google.genai" or name.startswith("google.genai"):
            raise ImportError("no genai")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    res = await generate_response("hi", 1, 100, mock_db)
    assert res["content"] == "AI service is not configured."
    assert res["interaction_id"] is None


async def test_generate_response_semantic_search_citations(mock_db, monkeypatch):
    monkeypatch.setattr(svc.settings, "gemini_api_key", "fake-key")
    r1 = _fake_response(_model_content_with_function_call(
        "semantic_search", {"query": "x"}))
    r2 = _fake_response(_model_content_with_text("done"))
    combined = {
        "query": "x",
        "documents": [{"document_id": 1, "page_number": 2, "document_name": "d.pdf",
                       "similarity_score": 0.9}],
        "fov_images": [{"image_id": 7, "experiment_id": 3, "filename": "im.tif",
                        "similarity_score": 0.8}],
    }
    with _patch_client([r1, r2]), \
         patch.object(svc, "combined_search", new=AsyncMock(return_value=combined)):
        res = await generate_response("search", 1, 100, mock_db)
    types_seen = {c["type"] for c in res["citations"]}
    assert "document" in types_seen and "fov" in types_seen


async def test_generate_response_search_fov_and_list_images_citations(mock_db, monkeypatch):
    monkeypatch.setattr(svc.settings, "gemini_api_key", "fake-key")
    r1 = _fake_response(_model_content_with_function_call(
        "search_fov_images", {"query": "cell"}))
    r2 = _fake_response(_model_content_with_function_call("list_images", {}))
    r3 = _fake_response(_model_content_with_text("done"))
    img = SimpleNamespace(id=22, original_filename="im.tif", experiment_id=3,
                          experiment=SimpleNamespace(name="E"), width=1, height=1)
    mock_db.execute.return_value = make_result(scalars_all=[img])
    with _patch_client([r1, r2, r3]), \
         patch.object(svc, "search_fov_images", new=AsyncMock(return_value=[
             {"image_id": 7, "experiment_id": 3, "filename": "f.tif",
              "similarity_score": 0.7}])):
        res = await generate_response("imgs", 1, 100, mock_db)
    fov_ids = {c.get("image_id") for c in res["citations"] if c["type"] == "fov"}
    assert 7 in fov_ids and 22 in fov_ids


async def test_generate_response_google_search_citations(mock_db, monkeypatch):
    monkeypatch.setattr(svc.settings, "gemini_api_key", "fake-key")
    r1 = _fake_response(_model_content_with_function_call(
        "google_search", {"query": "news"}))
    r2 = _fake_response(_model_content_with_text("summary"))
    search_resp = _search_response(sources=[("T", "http://src")])
    search_client = MagicMock()
    search_client.models.generate_content.return_value = search_resp
    main_client = MagicMock()
    main_client.models.generate_content.side_effect = [r1, r2]
    # Both genai.Client (main loop + inner google_search) -> alternate clients
    with patch("google.genai.Client", side_effect=[main_client, search_client]):
        res = await generate_response("news", 1, 100, mock_db)
    assert any(c["type"] == "web" and c["url"] == "http://src" for c in res["citations"])


async def test_generate_response_extract_region_passage_fix(mock_db, monkeypatch):
    monkeypatch.setattr(svc.settings, "gemini_api_key", "fake-key")
    r1 = _fake_response(_model_content_with_function_call(
        "extract_document_region",
        {"document_id": 1, "page_number": 2, "description": "Fig"}))
    # Model emits a link-style (not image) reference; post-processing fixes it.
    r2 = _fake_response(_model_content_with_text(
        "See [Fig](passage:1:2:abcdef012345)"))
    passage = {"extracted_text": "Fig", "document_id": 1, "page_number": 2,
               "passage_hash": "abcdef012345", "passage_type": "figure"}
    with _patch_client([r1, r2]), \
         patch("services.rag_service.extract_relevant_passages",
               new=AsyncMock(return_value=[passage])):
        res = await generate_response("show fig", 1, 100, mock_db)
    assert "![Fig](passage:1:2:abcdef012345)" in res["content"]


async def test_generate_response_fallback_with_stats_and_images(mock_db, monkeypatch):
    monkeypatch.setattr(svc.settings, "gemini_api_key", "fake-key")
    # get_experiment_stats then a never-ending empty-parts response -> exhausts the
    # loop and builds the stats/images fallback content.
    r_stats = _fake_response(_model_content_with_function_call(
        "get_experiment_stats", {"experiment_id": 9}))
    empty = SimpleNamespace(
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=[]),
                                    finish_reason="STOP")],
        text=None, prompt_feedback=None)
    exp = SimpleNamespace(id=9, name="EXP", description="d",
                          status=SimpleNamespace(value="active"),
                          map_protein=None, created_at=None)
    mock_db.execute.return_value = make_result(first=(exp, 4, 8))
    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = [r_stats] + [empty] * 40
    with patch("google.genai.Client", return_value=fake_client):
        res = await generate_response("stats", 1, 100, mock_db)
    assert "Analysis Results" in res["content"]
    assert "EXP" in res["content"]


async def test_generate_response_fallback_segmentation_and_cells(mock_db, monkeypatch):
    monkeypatch.setattr(svc.settings, "gemini_api_key", "fake-key")
    # Call get_segmentation_masks (masks_found) + get_cell_detection_results
    # (crops with thumbnails), then exhaust the loop -> fallback extracts both
    # the stats line and the cell-image markdown.
    r_seg = _fake_response(_model_content_with_function_call(
        "get_segmentation_masks", {"crop_ids": [1]}))
    r_cells = _fake_response(_model_content_with_function_call(
        "get_cell_detection_results", {"image_id": 5, "include_crops": True}))
    empty = SimpleNamespace(
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=[]),
                                    finish_reason="STOP")],
        text=None, prompt_feedback=None)
    seg_mask = SimpleNamespace(cell_crop_id=1, polygon_points=[[0, 0]],
                               area_pixels=5, iou_score=0.9, creation_method="sam",
                               prompt_count=1)
    img = SimpleNamespace(original_filename="im.tif")
    crop = SimpleNamespace(id=1, bbox_x=0, bbox_y=0, bbox_w=4, bbox_h=4,
                           detection_confidence=0.7)
    mock_db.execute.side_effect = [
        make_result(scalars_all=[seg_mask]),  # get_segmentation_masks
        make_result(scalar=img),              # get_cell_detection: image
        make_result(scalars_all=[crop]),      # get_cell_detection: crops
    ] + [make_result()] * 60
    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = [r_seg, r_cells] + [empty] * 40
    with patch("google.genai.Client", return_value=fake_client):
        res = await generate_response("seg+cells", 1, 100, mock_db)
    assert "segmentation masks" in res["content"]
    assert "![Cell 1]" in res["content"]


async def test_generate_response_tool_execution_exception(mock_db, monkeypatch):
    monkeypatch.setattr(svc.settings, "gemini_api_key", "fake-key")
    r1 = _fake_response(_model_content_with_function_call("list_documents", {}))
    r2 = _fake_response(_model_content_with_text("recovered"))
    with _patch_client([r1, r2]), \
         patch.object(svc, "execute_tool",
                      new=AsyncMock(side_effect=RuntimeError("tool blew up"))):
        res = await generate_response("docs", 1, 100, mock_db)
    # The loop catches the tool error, records it and continues to the text turn.
    assert res["content"] == "recovered"
    assert "Tool execution failed" in res["tool_calls"][0]["result"]["error"]


async def test_generate_response_serialization_error(mock_db, monkeypatch):
    monkeypatch.setattr(svc.settings, "gemini_api_key", "fake-key")
    r1 = _fake_response(_model_content_with_function_call("list_documents", {}))
    r2 = _fake_response(_model_content_with_text("ok"))
    calls = {"n": 0}
    real_serialize = svc._serialize_for_json

    def flaky_serialize(obj):
        # First call (serializing the tool result for the JSONB log) blows up;
        # subsequent calls (args, response part) use the real implementation.
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("cannot serialize")
        return real_serialize(obj)

    mock_db.execute.return_value = make_result(scalars_all=[])
    with _patch_client([r1, r2]), \
         patch.object(svc, "_serialize_for_json", side_effect=flaky_serialize):
        res = await generate_response("docs", 1, 100, mock_db)
    assert res["content"] == "ok"
    assert "Serialization failed" in res["tool_calls"][0]["result"]["error"]


async def test_generate_response_other_part_type(mock_db, monkeypatch):
    monkeypatch.setattr(svc.settings, "gemini_api_key", "fake-key")
    # A part with neither .text nor .function_call exercises the "OTHER" log
    # branch, then we fall back to response.text.
    other_part = SimpleNamespace(text=None, function_call=None)
    candidate = SimpleNamespace(
        content=SimpleNamespace(parts=[other_part]), finish_reason="STOP")
    resp = SimpleNamespace(candidates=[candidate], text="textfallback",
                           prompt_feedback=None)
    with _patch_client([resp]):
        res = await generate_response("hi", 1, 100, mock_db)
    assert res["content"] == "textfallback"


async def test_generate_response_text_fallback_passage_fix(mock_db, monkeypatch):
    monkeypatch.setattr(svc.settings, "gemini_api_key", "fake-key")
    # extract_document_region first (records a valid passage), then a response
    # whose only text is in response.text (no parts text) -> passage-fix runs on
    # the response.text fallback path.
    r1 = _fake_response(_model_content_with_function_call(
        "extract_document_region",
        {"document_id": 1, "page_number": 2, "description": "Fig"}))
    empty_parts = SimpleNamespace(content=SimpleNamespace(parts=[]),
                                  finish_reason="STOP")
    r2 = SimpleNamespace(candidates=[empty_parts],
                         text="See [Fig](passage:1:2:abcdef012345)",
                         prompt_feedback=None)
    passage = {"extracted_text": "Fig", "document_id": 1, "page_number": 2,
               "passage_hash": "abcdef012345", "passage_type": "figure"}
    with _patch_client([r1, r2]), \
         patch("services.rag_service.extract_relevant_passages",
               new=AsyncMock(return_value=[passage])):
        res = await generate_response("show", 1, 100, mock_db)
    assert "![Fig](passage:1:2:abcdef012345)" in res["content"]


async def test_generate_response_fallback_completed_actions(mock_db, monkeypatch):
    monkeypatch.setattr(svc.settings, "gemini_api_key", "fake-key")
    # list_documents yields no stats/images keys -> fallback uses the
    # "Completed actions: ..." summary branch.
    r1 = _fake_response(_model_content_with_function_call("list_documents", {}))
    empty = SimpleNamespace(
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=[]),
                                    finish_reason="STOP")],
        text=None, prompt_feedback=None)
    mock_db.execute.return_value = make_result(scalars_all=[])
    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = [r1] + [empty] * 40
    with patch("google.genai.Client", return_value=fake_client):
        res = await generate_response("docs", 1, 100, mock_db)
    assert "Completed actions" in res["content"]
    assert "list_documents" in res["content"]


async def test_generate_response_fallback_render_overlay_markdown(mock_db, tmp_path, monkeypatch):
    monkeypatch.setattr(svc.settings, "gemini_api_key", "fake-key")
    # render_segmentation_overlay returns image_markdown; loop exhausts ->
    # fallback collects the overlay image markdown.
    from PIL import Image as PILImage
    crop_path = tmp_path / "crop.png"
    PILImage.new("RGB", (20, 20), (3, 3, 3)).save(crop_path)
    crop = SimpleNamespace(
        id=1, bbox_x=0, bbox_y=0, mip_path=str(crop_path),
        image=SimpleNamespace(experiment=SimpleNamespace(user_id=1)))
    mask = SimpleNamespace(polygon_points=[[1, 1], [5, 1], [5, 5]],
                           area_pixels=10, iou_score=0.5)
    r1 = _fake_response(_model_content_with_function_call(
        "render_segmentation_overlay", {"crop_id": 1}))
    empty = SimpleNamespace(
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=[]),
                                    finish_reason="STOP")],
        text=None, prompt_feedback=None)
    mock_db.execute.side_effect = [
        make_result(scalar=crop), make_result(scalar=mask),
    ] + [make_result()] * 60
    settings_obj = SimpleNamespace(upload_dir=str(tmp_path))
    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = [r1] + [empty] * 40
    with patch("google.genai.Client", return_value=fake_client), \
         patch.object(svc, "get_settings", return_value=settings_obj):
        res = await generate_response("overlay", 1, 100, mock_db)
    assert "![Segmentation]" in res["content"]


async def test_render_overlay_image_single_polygon_fill(mock_db, tmp_path):
    # FOV path keeps polygon points as lists, so a single polygon
    # ([[x,y],[x,y],...]) exercises the single-polygon branch + the show_fill draw.
    settings_obj = SimpleNamespace(upload_dir=str(tmp_path))
    from PIL import Image as PILImage
    img_path = tmp_path / "fov.png"
    PILImage.new("RGB", (30, 30), (2, 2, 2)).save(img_path)
    image = SimpleNamespace(id=5, mip_path=str(img_path), file_path=None)
    mask = SimpleNamespace(polygon_points=[[2, 2], [10, 2], [10, 10], [2, 10]],
                           area_pixels=64, iou_score=0.9)
    mock_db.execute.side_effect = [
        make_result(scalar=image), make_result(scalar=mask),
    ]
    with patch.object(svc, "get_settings", return_value=settings_obj):
        res = await execute_tool("render_segmentation_overlay",
                                 {"image_id": 5, "show_fill": True,
                                  "show_polygon": True}, 1, mock_db)
    assert res["success"] is True


async def test_render_overlay_image_degenerate_polygon_skipped(mock_db, tmp_path):
    # FOV path single polygon with < 3 points hits the `continue` skip branch.
    settings_obj = SimpleNamespace(upload_dir=str(tmp_path))
    from PIL import Image as PILImage
    img_path = tmp_path / "fov2.png"
    PILImage.new("RGB", (10, 10), (1, 1, 1)).save(img_path)
    image = SimpleNamespace(id=5, mip_path=str(img_path), file_path=None)
    mask = SimpleNamespace(polygon_points=[[1, 1], [2, 2]],  # only 2 points
                           area_pixels=2, iou_score=0.1)
    mock_db.execute.side_effect = [
        make_result(scalar=image), make_result(scalar=mask),
    ]
    with patch.object(svc, "get_settings", return_value=settings_obj):
        res = await execute_tool("render_segmentation_overlay",
                                 {"image_id": 5, "show_fill": True}, 1, mock_db)
    assert res["success"] is True


async def test_generate_response_empty_part_after_tool_retries(mock_db, monkeypatch):
    monkeypatch.setattr(svc.settings, "gemini_api_key", "fake-key")
    # After a tool call, return a response whose part has no text and no
    # function_call (and no response.text) -> the "no text after tool, retry"
    # branch logs parts and continues to the next iteration, which returns text.
    r1 = _fake_response(_model_content_with_function_call("list_documents", {}))
    other_part = SimpleNamespace(text=None, function_call=None)
    r2 = SimpleNamespace(
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=[other_part]),
                                    finish_reason="STOP")],
        text=None, prompt_feedback=None)
    r3 = _fake_response(_model_content_with_text("final answer"))
    mock_db.execute.return_value = make_result(scalars_all=[])
    with _patch_client([r1, r2, r3]):
        res = await generate_response("docs", 1, 100, mock_db)
    assert res["content"] == "final answer"


async def test_run_redetection_task_success():
    from services.gemini_agent_service import _run_redetection_task
    fake_db = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=fake_db)
    cm.__aexit__ = AsyncMock(return_value=False)
    with patch("database.get_db_context", return_value=cm), \
         patch("services.image_processor.process_image_background",
               new=AsyncMock()) as proc:
        await _run_redetection_task(42)
    proc.assert_awaited_once()


async def test_run_redetection_task_error_swallowed():
    from services.gemini_agent_service import _run_redetection_task
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(side_effect=RuntimeError("db boom"))
    cm.__aexit__ = AsyncMock(return_value=False)
    with patch("database.get_db_context", return_value=cm), \
         patch("services.image_processor.process_image_background", new=AsyncMock()):
        # Should not raise -- the error is logged and swallowed.
        await _run_redetection_task(42)
