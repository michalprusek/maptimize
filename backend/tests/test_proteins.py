"""Tests for MAP protein endpoints.

Integration tests that verify CRUD operations and embedding computation
for the /api/proteins endpoints.

These tests run against a live backend server.
Make sure the backend is running: docker-compose up -d

IMPORTANT: Proteins are SHARED across all users. Tests must clean up
any proteins they create to avoid polluting production data.
"""
import pytest

from conftest import unique_name


class TestProteinList:
    """Tests for GET /api/proteins."""

    def test_list_returns_array(self, client, auth_headers):
        """GET /api/proteins returns a JSON array."""
        response = client.get("/api/proteins", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_list_requires_auth(self, client):
        """GET /api/proteins without auth returns 401."""
        response = client.get("/api/proteins")
        assert response.status_code == 401

    def test_list_items_have_expected_fields(self, client, auth_headers):
        """Each protein in the list has required fields."""
        response = client.get("/api/proteins", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()

        if len(data) == 0:
            pytest.skip("No proteins available for field validation")

        expected_fields = [
            "id", "name", "image_count", "created_at",
            "has_embedding", "color",
        ]
        for protein in data:
            for field in expected_fields:
                assert field in protein, f"Missing field '{field}' in protein response"

    def test_list_items_have_optional_fields(self, client, auth_headers):
        """Each protein may have optional detail fields."""
        response = client.get("/api/proteins", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()

        if len(data) == 0:
            pytest.skip("No proteins available for field validation")

        # These fields should be present (even if null)
        optional_fields = [
            "description", "fasta_sequence", "full_name",
            "uniprot_id", "gene_name", "organism",
        ]
        protein = data[0]
        for field in optional_fields:
            assert field in protein, f"Missing optional field '{field}' in protein response"


class TestProteinCreate:
    """Tests for POST /api/proteins."""

    def test_create_protein_minimal(self, client, auth_headers):
        """POST /api/proteins with only name returns 201."""
        name = unique_name()
        protein_id = None
        try:
            response = client.post(
                "/api/proteins",
                headers=auth_headers,
                json={"name": name}
            )
            assert response.status_code == 201
            data = response.json()
            protein_id = data["id"]

            assert data["name"] == name
            assert data["image_count"] == 0
            assert data["has_embedding"] is False
        finally:
            if protein_id:
                client.delete(f"/api/proteins/{protein_id}", headers=auth_headers)

    def test_create_protein_requires_auth(self, client):
        """POST /api/proteins without auth returns 401."""
        response = client.post(
            "/api/proteins",
            json={"name": unique_name()}
        )
        assert response.status_code == 401

    def test_create_protein_with_all_fields(self, client, auth_headers):
        """POST /api/proteins with all optional fields returns 201."""
        name = unique_name()
        protein_id = None
        try:
            response = client.post(
                "/api/proteins",
                headers=auth_headers,
                json={
                    "name": name,
                    "description": "Test description",
                    "fasta_sequence": "MAEPRQEFEVMEDHAGTY",
                    "color": "#ff6b6b",
                    "full_name": "Test Protein Full Name",
                    "gene_name": "TPFN",
                    "organism": "Homo sapiens",
                }
            )
            assert response.status_code == 201
            data = response.json()
            protein_id = data["id"]

            assert data["name"] == name
            assert data["description"] == "Test description"
            assert data["fasta_sequence"] == "MAEPRQEFEVMEDHAGTY"
            assert data["color"] == "#ff6b6b"
            assert data["full_name"] == "Test Protein Full Name"
            assert data["gene_name"] == "TPFN"
            assert data["organism"] == "Homo sapiens"
        finally:
            if protein_id:
                client.delete(f"/api/proteins/{protein_id}", headers=auth_headers)

    def test_create_protein_duplicate_name_fails(self, client, auth_headers):
        """POST /api/proteins with duplicate name returns 400."""
        name = unique_name()
        protein_id = None
        try:
            # Create first protein
            response = client.post(
                "/api/proteins",
                headers=auth_headers,
                json={"name": name}
            )
            assert response.status_code == 201
            protein_id = response.json()["id"]

            # Try to create second protein with same name
            response = client.post(
                "/api/proteins",
                headers=auth_headers,
                json={"name": name}
            )
            assert response.status_code == 400
            assert "already exists" in response.json()["detail"].lower()
        finally:
            if protein_id:
                client.delete(f"/api/proteins/{protein_id}", headers=auth_headers)

    def test_create_protein_empty_name_fails(self, client, auth_headers):
        """POST /api/proteins with empty name returns 422."""
        response = client.post(
            "/api/proteins",
            headers=auth_headers,
            json={"name": ""}
        )
        assert response.status_code == 422

    def test_create_protein_invalid_color_fails(self, client, auth_headers):
        """POST /api/proteins with invalid color format returns 422."""
        response = client.post(
            "/api/proteins",
            headers=auth_headers,
            json={"name": unique_name(), "color": "not-a-color"}
        )
        assert response.status_code == 422


class TestProteinDetail:
    """Tests for GET /api/proteins/{id}."""

    def test_get_protein_by_id(self, client, auth_headers):
        """GET /api/proteins/{id} returns protein details."""
        name = unique_name()
        protein_id = None
        try:
            # Create a protein first
            response = client.post(
                "/api/proteins",
                headers=auth_headers,
                json={"name": name, "description": "Detail test"}
            )
            assert response.status_code == 201
            protein_id = response.json()["id"]

            # Fetch it by ID
            response = client.get(
                f"/api/proteins/{protein_id}",
                headers=auth_headers
            )
            assert response.status_code == 200
            data = response.json()
            assert data["id"] == protein_id
            assert data["name"] == name
            assert data["description"] == "Detail test"
        finally:
            if protein_id:
                client.delete(f"/api/proteins/{protein_id}", headers=auth_headers)

    def test_get_nonexistent_protein_returns_404(self, client, auth_headers):
        """GET /api/proteins/{id} with invalid ID returns 404."""
        response = client.get("/api/proteins/999999", headers=auth_headers)
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestProteinUpdate:
    """Tests for PATCH /api/proteins/{id}."""

    def test_update_protein_name(self, client, auth_headers):
        """PATCH /api/proteins/{id} can update name."""
        original_name = unique_name()
        new_name = unique_name("UpdatedProtein")
        protein_id = None
        try:
            # Create
            response = client.post(
                "/api/proteins",
                headers=auth_headers,
                json={"name": original_name}
            )
            assert response.status_code == 201
            protein_id = response.json()["id"]

            # Update name
            response = client.patch(
                f"/api/proteins/{protein_id}",
                headers=auth_headers,
                json={"name": new_name}
            )
            assert response.status_code == 200
            assert response.json()["name"] == new_name

            # Verify persisted
            response = client.get(
                f"/api/proteins/{protein_id}",
                headers=auth_headers
            )
            assert response.status_code == 200
            assert response.json()["name"] == new_name
        finally:
            if protein_id:
                client.delete(f"/api/proteins/{protein_id}", headers=auth_headers)

    def test_update_protein_description(self, client, auth_headers):
        """PATCH /api/proteins/{id} can update description."""
        name = unique_name()
        protein_id = None
        try:
            response = client.post(
                "/api/proteins",
                headers=auth_headers,
                json={"name": name}
            )
            assert response.status_code == 201
            protein_id = response.json()["id"]

            # Update description
            response = client.patch(
                f"/api/proteins/{protein_id}",
                headers=auth_headers,
                json={"description": "Updated description"}
            )
            assert response.status_code == 200
            assert response.json()["description"] == "Updated description"
        finally:
            if protein_id:
                client.delete(f"/api/proteins/{protein_id}", headers=auth_headers)

    def test_update_nonexistent_protein_returns_404(self, client, auth_headers):
        """PATCH /api/proteins/{id} with invalid ID returns 404."""
        response = client.patch(
            "/api/proteins/999999",
            headers=auth_headers,
            json={"name": "ghost"}
        )
        assert response.status_code == 404


class TestProteinDelete:
    """Tests for DELETE /api/proteins/{id}."""

    def test_delete_protein_without_images(self, client, auth_headers):
        """DELETE /api/proteins/{id} returns 204 for protein with no images."""
        name = unique_name()
        response = client.post(
            "/api/proteins",
            headers=auth_headers,
            json={"name": name}
        )
        assert response.status_code == 201
        protein_id = response.json()["id"]

        # Delete it
        response = client.delete(
            f"/api/proteins/{protein_id}",
            headers=auth_headers
        )
        assert response.status_code == 204

        # Verify it is gone
        response = client.get(
            f"/api/proteins/{protein_id}",
            headers=auth_headers
        )
        assert response.status_code == 404

    def test_delete_nonexistent_protein_returns_404(self, client, auth_headers):
        """DELETE /api/proteins/{id} with invalid ID returns 404."""
        response = client.delete("/api/proteins/999999", headers=auth_headers)
        assert response.status_code == 404

    def test_delete_protein_with_images_returns_409(self, client, auth_headers):
        """DELETE /api/proteins/{id} returns 409 if protein has associated images."""
        # Find a protein that has images
        response = client.get("/api/proteins", headers=auth_headers)
        assert response.status_code == 200
        proteins = response.json()

        protein_with_images = next(
            (p for p in proteins if p["image_count"] > 0), None
        )
        if not protein_with_images:
            pytest.skip("No protein with associated images available for testing")

        response = client.delete(
            f"/api/proteins/{protein_with_images['id']}",
            headers=auth_headers
        )
        assert response.status_code == 409
        assert "associated images" in response.json()["detail"].lower()


class TestProteinEmbedding:
    """Tests for POST /api/proteins/{id}/compute-embedding."""

    @pytest.mark.slow
    def test_compute_embedding_without_fasta_returns_error(self, client, auth_headers):
        """POST compute-embedding for protein without fasta_sequence returns 400."""
        name = unique_name()
        protein_id = None
        try:
            # Create protein without fasta_sequence
            response = client.post(
                "/api/proteins",
                headers=auth_headers,
                json={"name": name}
            )
            assert response.status_code == 201
            protein_id = response.json()["id"]

            # Try to compute embedding
            response = client.post(
                f"/api/proteins/{protein_id}/compute-embedding",
                headers=auth_headers
            )
            assert response.status_code == 400
        finally:
            if protein_id:
                client.delete(f"/api/proteins/{protein_id}", headers=auth_headers)

    def test_compute_embedding_nonexistent_returns_404(self, client, auth_headers):
        """POST compute-embedding for nonexistent protein returns 404."""
        response = client.post(
            "/api/proteins/999999/compute-embedding",
            headers=auth_headers
        )
        assert response.status_code == 404
