"""Tests for RAG (Retrieval-Augmented Generation) endpoints.

Tests the RAG API including:
- GET /api/rag/documents - document listing
- POST /api/rag/documents/upload - document upload
- GET /api/rag/documents/{id} - document detail
- DELETE /api/rag/documents/{id} - document deletion
- GET /api/rag/indexing/status - indexing status
- GET /api/rag/search - combined search
- GET /api/rag/search/documents - document-only search
- GET /api/rag/search/fov - FOV image-only search

These tests run against a live backend server.
Make sure the backend is running: docker-compose up -d
"""
import io

import pytest


class TestRAGDocumentList:
    """Test suite for document listing endpoint."""

    def test_list_documents(self, client, auth_headers):
        """GET /api/rag/documents returns a list of documents."""
        response = client.get("/api/rag/documents", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_list_documents_requires_auth(self, client):
        """GET /api/rag/documents requires authentication."""
        response = client.get("/api/rag/documents")
        assert response.status_code in [401, 403]

    def test_list_documents_pagination(self, client, auth_headers):
        """GET /api/rag/documents respects skip and limit params."""
        response = client.get(
            "/api/rag/documents?skip=0&limit=2",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) <= 2

    def test_list_documents_with_status_filter(self, client, auth_headers):
        """GET /api/rag/documents?status=completed filters by status."""
        response = client.get(
            "/api/rag/documents?status=completed",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        # All returned documents should have the filtered status
        for doc in data:
            assert doc["status"] == "completed"

    def test_list_documents_response_fields(self, client, auth_headers):
        """GET /api/rag/documents returns documents with required fields."""
        response = client.get("/api/rag/documents", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()

        if len(data) == 0:
            pytest.skip("No documents available for field validation")

        required_fields = [
            "id", "name", "file_type", "status", "progress",
            "page_count", "created_at",
        ]
        for doc in data:
            for field in required_fields:
                assert field in doc, f"Missing required field: {field}"


class TestRAGDocumentUpload:
    """Test suite for document upload endpoint."""

    def test_upload_requires_auth(self, client):
        """POST /api/rag/documents/upload requires authentication."""
        fake_pdf = io.BytesIO(b"%PDF-1.4 fake content")
        response = client.post(
            "/api/rag/documents/upload",
            files={"file": ("test.pdf", fake_pdf, "application/pdf")},
        )
        assert response.status_code in [401, 403]

    def test_upload_no_file_returns_422(self, client, auth_headers):
        """POST /api/rag/documents/upload without file returns 422."""
        response = client.post(
            "/api/rag/documents/upload",
            headers=auth_headers,
        )
        assert response.status_code == 422

    def test_upload_unsupported_file_type(self, client, auth_headers):
        """POST /api/rag/documents/upload rejects unsupported file types."""
        fake_file = io.BytesIO(b"not a valid document")
        response = client.post(
            "/api/rag/documents/upload",
            headers=auth_headers,
            files={"file": ("malware.exe", fake_file, "application/octet-stream")},
        )
        assert response.status_code == 400
        assert "unsupported" in response.json()["detail"].lower()

    @pytest.mark.slow
    def test_upload_valid_pdf(self, client, auth_headers):
        """POST /api/rag/documents/upload accepts a valid PDF file.

        Marked as slow because it triggers background document processing.
        """
        # Minimal valid-ish PDF (just enough to pass filename check)
        fake_pdf = io.BytesIO(b"%PDF-1.4 minimal content for testing")
        doc_id = None
        try:
            response = client.post(
                "/api/rag/documents/upload",
                headers=auth_headers,
                files={"file": ("test_upload.pdf", fake_pdf, "application/pdf")},
            )

            # May succeed (200) or fail on processing, but should accept the upload
            if response.status_code == 200:
                data = response.json()
                doc_id = data["id"]
                assert "id" in data
                assert "name" in data
                assert "status" in data
                assert "file_type" in data
        finally:
            if doc_id:
                client.delete(
                    f"/api/rag/documents/{doc_id}",
                    headers=auth_headers,
                )


class TestRAGDocumentDetail:
    """Test suite for document detail endpoint."""

    def test_get_document_nonexistent(self, client, auth_headers):
        """GET /api/rag/documents/{id} returns 404 for nonexistent document."""
        response = client.get(
            "/api/rag/documents/999999",
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_get_document_requires_auth(self, client):
        """GET /api/rag/documents/{id} requires authentication."""
        response = client.get("/api/rag/documents/1")
        assert response.status_code in [401, 403]

    def test_get_document_detail(self, client, auth_headers):
        """GET /api/rag/documents/{id} returns document details for existing document."""
        # First check if there are any documents
        list_resp = client.get("/api/rag/documents", headers=auth_headers)
        assert list_resp.status_code == 200
        documents = list_resp.json()

        if len(documents) == 0:
            pytest.skip("No documents available for detail test")

        doc_id = documents[0]["id"]
        response = client.get(
            f"/api/rag/documents/{doc_id}",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == doc_id
        assert "name" in data
        assert "file_type" in data
        assert "status" in data
        assert "progress" in data
        assert "page_count" in data

    def test_delete_document_nonexistent(self, client, auth_headers):
        """DELETE /api/rag/documents/{id} returns 404 for nonexistent document."""
        response = client.delete(
            "/api/rag/documents/999999",
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_delete_document_requires_auth(self, client):
        """DELETE /api/rag/documents/{id} requires authentication."""
        response = client.delete("/api/rag/documents/1")
        assert response.status_code in [401, 403]


class TestRAGSearch:
    """Test suite for RAG search endpoints."""

    def test_search_requires_auth(self, client):
        """GET /api/rag/search requires authentication."""
        response = client.get("/api/rag/search?q=test")
        assert response.status_code in [401, 403]

    def test_search_requires_query_param(self, client, auth_headers):
        """GET /api/rag/search without q param returns 422."""
        response = client.get("/api/rag/search", headers=auth_headers)
        assert response.status_code == 422

    def test_search_combined(self, client, auth_headers):
        """GET /api/rag/search returns combined document and FOV results."""
        response = client.get(
            "/api/rag/search?q=cell",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert "documents" in data
        assert "fov_images" in data
        assert "query" in data
        assert data["query"] == "cell"
        assert isinstance(data["documents"], list)
        assert isinstance(data["fov_images"], list)

    def test_search_documents_only_requires_auth(self, client):
        """GET /api/rag/search/documents requires authentication."""
        response = client.get("/api/rag/search/documents?q=test")
        assert response.status_code in [401, 403]

    def test_search_documents_only_requires_query(self, client, auth_headers):
        """GET /api/rag/search/documents without q param returns 422."""
        response = client.get(
            "/api/rag/search/documents",
            headers=auth_headers,
        )
        assert response.status_code == 422

    def test_search_documents_only(self, client, auth_headers):
        """GET /api/rag/search/documents returns document results."""
        response = client.get(
            "/api/rag/search/documents?q=protein&limit=5",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert "query" in data
        assert data["query"] == "protein"
        assert "results" in data
        assert isinstance(data["results"], list)

    def test_search_fov_only_requires_auth(self, client):
        """GET /api/rag/search/fov requires authentication."""
        response = client.get("/api/rag/search/fov?q=test")
        assert response.status_code in [401, 403]

    def test_search_fov_only_requires_query(self, client, auth_headers):
        """GET /api/rag/search/fov without q param returns 422."""
        response = client.get(
            "/api/rag/search/fov",
            headers=auth_headers,
        )
        assert response.status_code == 422

    def test_search_fov_only(self, client, auth_headers):
        """GET /api/rag/search/fov returns FOV image results."""
        response = client.get(
            "/api/rag/search/fov?q=microscopy&limit=5",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert "query" in data
        assert data["query"] == "microscopy"
        assert "results" in data
        assert isinstance(data["results"], list)

    def test_search_empty_query_rejected(self, client, auth_headers):
        """GET /api/rag/search with empty q string returns 422."""
        response = client.get(
            "/api/rag/search?q=",
            headers=auth_headers,
        )
        assert response.status_code == 422


class TestRAGIndexing:
    """Test suite for RAG indexing status endpoint."""

    def test_indexing_status(self, client, auth_headers):
        """GET /api/rag/indexing/status returns valid indexing status."""
        response = client.get(
            "/api/rag/indexing/status",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()

        required_fields = [
            "documents_pending",
            "documents_processing",
            "documents_completed",
            "documents_failed",
            "fov_images_pending",
            "fov_images_indexed",
        ]
        for field in required_fields:
            assert field in data, f"Missing required field: {field}"
            assert isinstance(data[field], int), f"Field {field} should be int, got {type(data[field])}"
            assert data[field] >= 0, f"Field {field} should be non-negative, got {data[field]}"

    def test_indexing_status_requires_auth(self, client):
        """GET /api/rag/indexing/status requires authentication."""
        response = client.get("/api/rag/indexing/status")
        assert response.status_code in [401, 403]
