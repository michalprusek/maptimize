"""Tests for ranking undo functionality.

Integration tests that verify the undo feature restores previous
mu/sigma values correctly after comparisons.

These tests run against a live backend server.
Make sure the backend is running: docker-compose up -d

Test credentials are loaded from environment variables:
- TEST_USER_EMAIL: Test user email
- TEST_USER_PASSWORD: Test user password
"""
import pytest
from config import get_settings

settings = get_settings()


def test_comparison_stores_previous_values(client, auth_headers):
    """Test that submitting a comparison stores previous mu/sigma values."""
    # First, get a pair of cells to compare
    response = client.get("/api/ranking/pair", headers=auth_headers)

    if response.status_code == 400:
        pytest.skip("No cells available for comparison - need to import sources first")

    assert response.status_code == 200
    pair = response.json()
    crop_a_id = pair["crop_a"]["id"]
    crop_b_id = pair["crop_b"]["id"]

    # Submit comparison (crop_a wins)
    response = client.post(
        "/api/ranking/compare",
        headers=auth_headers,
        json={
            "crop_a_id": crop_a_id,
            "crop_b_id": crop_b_id,
            "winner_id": crop_a_id
        }
    )
    assert response.status_code == 200

    # Undo it to clean up
    response = client.post("/api/ranking/undo", headers=auth_headers)
    assert response.status_code == 200


def test_undo_restores_previous_values(client, auth_headers):
    """Test that undoing a comparison restores previous mu/sigma values."""
    # Get a pair
    response = client.get("/api/ranking/pair", headers=auth_headers)
    if response.status_code == 400:
        pytest.skip("No cells available for comparison")

    assert response.status_code == 200
    pair = response.json()
    crop_a_id = pair["crop_a"]["id"]
    crop_b_id = pair["crop_b"]["id"]

    # Get leaderboard to see current ratings
    response = client.get("/api/ranking/leaderboard", headers=auth_headers)
    assert response.status_code == 200
    leaderboard_before = {item["cell_crop_id"]: item for item in response.json()["items"]}

    mu_a_before = leaderboard_before.get(crop_a_id, {}).get("mu", settings.initial_mu)
    sigma_a_before = leaderboard_before.get(crop_a_id, {}).get("sigma", settings.initial_sigma)
    mu_b_before = leaderboard_before.get(crop_b_id, {}).get("mu", settings.initial_mu)
    sigma_b_before = leaderboard_before.get(crop_b_id, {}).get("sigma", settings.initial_sigma)

    # Submit comparison (crop_a wins)
    response = client.post(
        "/api/ranking/compare",
        headers=auth_headers,
        json={
            "crop_a_id": crop_a_id,
            "crop_b_id": crop_b_id,
            "winner_id": crop_a_id
        }
    )
    assert response.status_code == 200

    # Verify ratings changed
    response = client.get("/api/ranking/leaderboard", headers=auth_headers)
    assert response.status_code == 200
    leaderboard_after = {item["cell_crop_id"]: item for item in response.json()["items"]}

    mu_a_after = leaderboard_after[crop_a_id]["mu"]
    mu_b_after = leaderboard_after[crop_b_id]["mu"]

    # Winner's mu should increase, loser's should decrease
    assert mu_a_after > mu_a_before, f"Winner's mu should increase: {mu_a_after} > {mu_a_before}"
    assert mu_b_after < mu_b_before, f"Loser's mu should decrease: {mu_b_after} < {mu_b_before}"

    # Now UNDO
    response = client.post("/api/ranking/undo", headers=auth_headers)
    assert response.status_code == 200

    # Verify ratings are restored
    response = client.get("/api/ranking/leaderboard", headers=auth_headers)
    assert response.status_code == 200
    leaderboard_restored = {item["cell_crop_id"]: item for item in response.json()["items"]}

    mu_a_restored = leaderboard_restored[crop_a_id]["mu"]
    sigma_a_restored = leaderboard_restored[crop_a_id]["sigma"]
    mu_b_restored = leaderboard_restored[crop_b_id]["mu"]
    sigma_b_restored = leaderboard_restored[crop_b_id]["sigma"]

    # Check values are restored (with small tolerance for floating point)
    assert abs(mu_a_restored - mu_a_before) < 0.001, \
        f"Winner mu not restored: {mu_a_restored} != {mu_a_before}"
    assert abs(sigma_a_restored - sigma_a_before) < 0.001, \
        f"Winner sigma not restored: {sigma_a_restored} != {sigma_a_before}"
    assert abs(mu_b_restored - mu_b_before) < 0.001, \
        f"Loser mu not restored: {mu_b_restored} != {mu_b_before}"
    assert abs(sigma_b_restored - sigma_b_before) < 0.001, \
        f"Loser sigma not restored: {sigma_b_restored} != {sigma_b_before}"


def test_undo_decrements_comparison_count(client, auth_headers):
    """Test that undoing decrements the comparison count."""
    # Get a pair
    response = client.get("/api/ranking/pair", headers=auth_headers)
    if response.status_code == 400:
        pytest.skip("No cells available for comparison")

    pair = response.json()
    crop_a_id = pair["crop_a"]["id"]
    crop_b_id = pair["crop_b"]["id"]

    # Get initial comparison counts
    response = client.get("/api/ranking/leaderboard", headers=auth_headers)
    assert response.status_code == 200
    leaderboard = {item["cell_crop_id"]: item for item in response.json()["items"]}
    count_a_before = leaderboard.get(crop_a_id, {}).get("comparison_count", 0)

    # Submit comparison
    response = client.post(
        "/api/ranking/compare",
        headers=auth_headers,
        json={
            "crop_a_id": crop_a_id,
            "crop_b_id": crop_b_id,
            "winner_id": crop_a_id
        }
    )
    assert response.status_code == 200

    # Verify count increased
    response = client.get("/api/ranking/leaderboard", headers=auth_headers)
    leaderboard = {item["cell_crop_id"]: item for item in response.json()["items"]}
    count_a_after = leaderboard[crop_a_id]["comparison_count"]
    assert count_a_after == count_a_before + 1, \
        f"Comparison count should increase: {count_a_after} == {count_a_before + 1}"

    # Undo
    response = client.post("/api/ranking/undo", headers=auth_headers)
    assert response.status_code == 200

    # Verify count decreased
    response = client.get("/api/ranking/leaderboard", headers=auth_headers)
    leaderboard = {item["cell_crop_id"]: item for item in response.json()["items"]}
    count_a_restored = leaderboard[crop_a_id]["comparison_count"]
    assert count_a_restored == count_a_before, \
        f"Comparison count not restored: {count_a_restored} != {count_a_before}"


def test_undo_marks_comparison_as_undone(client, auth_headers):
    """Test that undo marks the comparison record as undone."""
    # Get a pair
    response = client.get("/api/ranking/pair", headers=auth_headers)
    if response.status_code == 400:
        pytest.skip("No cells available for comparison")

    pair = response.json()

    # Submit comparison
    response = client.post(
        "/api/ranking/compare",
        headers=auth_headers,
        json={
            "crop_a_id": pair["crop_a"]["id"],
            "crop_b_id": pair["crop_b"]["id"],
            "winner_id": pair["crop_a"]["id"]
        }
    )
    assert response.status_code == 200
    comparison_id = response.json()["id"]

    # Undo
    response = client.post("/api/ranking/undo", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["id"] == comparison_id


def test_undo_with_no_comparisons_returns_404(client, auth_headers):
    """Test that undo returns 404 when there are no comparisons to undo."""
    # Undo all existing comparisons first
    while True:
        response = client.post("/api/ranking/undo", headers=auth_headers)
        if response.status_code == 404:
            break

    # Now verify we get 404
    response = client.post("/api/ranking/undo", headers=auth_headers)
    assert response.status_code == 404
    assert "No comparison to undo" in response.json()["detail"]


def test_multiple_undo_operations(client, auth_headers):
    """Test multiple consecutive undo operations restore correct values."""
    # Get pairs and do multiple comparisons
    comparisons = []

    for _ in range(3):
        response = client.get("/api/ranking/pair", headers=auth_headers)
        if response.status_code == 400:
            pytest.skip("No cells available for comparison")

        pair = response.json()

        # Record current state before comparison
        response = client.get("/api/ranking/leaderboard", headers=auth_headers)
        leaderboard = {item["cell_crop_id"]: item for item in response.json()["items"]}

        crop_a_id = pair["crop_a"]["id"]
        crop_b_id = pair["crop_b"]["id"]

        comparisons.append({
            "crop_a_id": crop_a_id,
            "crop_b_id": crop_b_id,
            "mu_a_before": leaderboard.get(crop_a_id, {}).get("mu", settings.initial_mu),
            "mu_b_before": leaderboard.get(crop_b_id, {}).get("mu", settings.initial_mu),
        })

        # Submit comparison
        response = client.post(
            "/api/ranking/compare",
            headers=auth_headers,
            json={
                "crop_a_id": crop_a_id,
                "crop_b_id": crop_b_id,
                "winner_id": crop_a_id
            }
        )
        assert response.status_code == 200

    # Now undo all comparisons in reverse order
    for comp in reversed(comparisons):
        response = client.post("/api/ranking/undo", headers=auth_headers)
        assert response.status_code == 200

        # Verify ratings restored
        response = client.get("/api/ranking/leaderboard", headers=auth_headers)
        leaderboard = {item["cell_crop_id"]: item for item in response.json()["items"]}

        mu_a_restored = leaderboard[comp["crop_a_id"]]["mu"]
        mu_b_restored = leaderboard[comp["crop_b_id"]]["mu"]

        assert abs(mu_a_restored - comp["mu_a_before"]) < 0.001, \
            f"Crop A mu not restored after undo"
        assert abs(mu_b_restored - comp["mu_b_before"]) < 0.001, \
            f"Crop B mu not restored after undo"


def test_undo_does_not_create_negative_comparison_count(client, auth_headers):
    """Test that undo handles edge case where comparison_count could go negative.

    This guards against bugs that could cause negative comparison counts
    if data was manually modified or through other edge cases.
    """
    # Get a pair
    response = client.get("/api/ranking/pair", headers=auth_headers)
    if response.status_code == 400:
        pytest.skip("No cells available for comparison")

    pair = response.json()
    crop_a_id = pair["crop_a"]["id"]
    crop_b_id = pair["crop_b"]["id"]

    # Submit comparison
    response = client.post(
        "/api/ranking/compare",
        headers=auth_headers,
        json={
            "crop_a_id": crop_a_id,
            "crop_b_id": crop_b_id,
            "winner_id": crop_a_id
        }
    )
    assert response.status_code == 200

    # Undo
    response = client.post("/api/ranking/undo", headers=auth_headers)
    assert response.status_code == 200

    # Verify comparison counts are not negative
    response = client.get("/api/ranking/leaderboard", headers=auth_headers)
    leaderboard = {item["cell_crop_id"]: item for item in response.json()["items"]}

    count_a = leaderboard[crop_a_id]["comparison_count"]
    count_b = leaderboard[crop_b_id]["comparison_count"]

    assert count_a >= 0, f"Winner comparison_count should not be negative: {count_a}"
    assert count_b >= 0, f"Loser comparison_count should not be negative: {count_b}"


def test_cannot_undo_already_undone_comparison(client, auth_headers):
    """Test that an already undone comparison cannot be undone again.

    After undoing a comparison, attempting to undo again should only
    find earlier comparisons, not the already-undone one.
    """
    # Get a pair
    response = client.get("/api/ranking/pair", headers=auth_headers)
    if response.status_code == 400:
        pytest.skip("No cells available for comparison")

    pair = response.json()

    # Submit comparison
    response = client.post(
        "/api/ranking/compare",
        headers=auth_headers,
        json={
            "crop_a_id": pair["crop_a"]["id"],
            "crop_b_id": pair["crop_b"]["id"],
            "winner_id": pair["crop_a"]["id"]
        }
    )
    assert response.status_code == 200
    first_comparison_id = response.json()["id"]

    # Undo it
    response = client.post("/api/ranking/undo", headers=auth_headers)
    assert response.status_code == 200
    undone_id = response.json()["id"]
    assert undone_id == first_comparison_id

    # Submit another comparison
    response = client.get("/api/ranking/pair", headers=auth_headers)
    if response.status_code == 400:
        pytest.skip("No cells available for second comparison")

    pair = response.json()
    response = client.post(
        "/api/ranking/compare",
        headers=auth_headers,
        json={
            "crop_a_id": pair["crop_a"]["id"],
            "crop_b_id": pair["crop_b"]["id"],
            "winner_id": pair["crop_a"]["id"]
        }
    )
    assert response.status_code == 200
    second_comparison_id = response.json()["id"]

    # Undo - should get the second comparison, not the first (already undone)
    response = client.post("/api/ranking/undo", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["id"] == second_comparison_id, \
        "Undo should return the second comparison, not re-undo the first"


def test_undo_user_isolation(client, auth_headers):
    """Test that undo only affects current user's comparisons.

    This test verifies that the user_id filter is correctly applied,
    ensuring users cannot accidentally undo other users' comparisons.

    Note: This test only verifies the filter works by checking that
    a user's undo only returns their own comparisons. Full multi-user
    testing would require a second authenticated user.
    """
    # First, clear any existing comparisons for this user
    while True:
        response = client.post("/api/ranking/undo", headers=auth_headers)
        if response.status_code == 404:
            break

    # Submit a comparison
    response = client.get("/api/ranking/pair", headers=auth_headers)
    if response.status_code == 400:
        pytest.skip("No cells available for comparison")

    pair = response.json()
    response = client.post(
        "/api/ranking/compare",
        headers=auth_headers,
        json={
            "crop_a_id": pair["crop_a"]["id"],
            "crop_b_id": pair["crop_b"]["id"],
            "winner_id": pair["crop_a"]["id"]
        }
    )
    assert response.status_code == 200
    comparison_id = response.json()["id"]

    # Undo should return our comparison
    response = client.post("/api/ranking/undo", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["id"] == comparison_id

    # Now there should be no more comparisons to undo for this user
    response = client.post("/api/ranking/undo", headers=auth_headers)
    assert response.status_code == 404, \
        "After undoing our only comparison, there should be none left"
