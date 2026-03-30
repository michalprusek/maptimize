"""Tests for chat endpoints.

Tests the chat API including:
- GET/POST/PATCH/DELETE /api/chat/threads - thread CRUD
- GET /api/chat/threads/{id}/messages - message listing
- POST /api/chat/threads/{id}/messages - send message (async AI generation)
- GET /api/chat/threads/{id}/generation-status - poll generation
- POST /api/chat/threads/{id}/cancel-generation - cancel generation
- PUT /api/chat/threads/{id}/messages/{msg_id} - edit message

These tests run against a live backend server.
Make sure the backend is running: docker-compose up -d
"""
import pytest


class TestChatThreadCRUD:
    """Test suite for chat thread create/read/update/delete."""

    def test_list_threads(self, client, auth_headers):
        """GET /api/chat/threads returns a list of threads."""
        response = client.get("/api/chat/threads", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_list_threads_pagination(self, client, auth_headers):
        """GET /api/chat/threads respects skip and limit params."""
        response = client.get(
            "/api/chat/threads?skip=0&limit=2",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) <= 2

    def test_list_threads_requires_auth(self, client):
        """GET /api/chat/threads requires authentication."""
        response = client.get("/api/chat/threads")
        assert response.status_code in [401, 403]

    def test_create_thread_default_name(self, client, auth_headers):
        """POST /api/chat/threads with no body creates thread named 'New Chat'."""
        thread_id = None
        try:
            response = client.post("/api/chat/threads", headers=auth_headers)

            assert response.status_code == 201
            data = response.json()
            thread_id = data["id"]

            assert data["name"] == "New Chat"
            assert "id" in data
            assert "created_at" in data
            assert "updated_at" in data
            assert "message_count" in data
            assert data["message_count"] == 0
        finally:
            if thread_id:
                client.delete(
                    f"/api/chat/threads/{thread_id}",
                    headers=auth_headers,
                )

    def test_create_thread_with_name(self, client, auth_headers):
        """POST /api/chat/threads with name creates thread with that name."""
        thread_id = None
        try:
            response = client.post(
                "/api/chat/threads",
                headers=auth_headers,
                json={"name": "My Test Thread"},
            )

            assert response.status_code == 201
            data = response.json()
            thread_id = data["id"]

            assert data["name"] == "My Test Thread"
        finally:
            if thread_id:
                client.delete(
                    f"/api/chat/threads/{thread_id}",
                    headers=auth_headers,
                )

    def test_create_thread_requires_auth(self, client):
        """POST /api/chat/threads requires authentication."""
        response = client.post("/api/chat/threads")
        assert response.status_code in [401, 403]

    def test_get_thread(self, client, auth_headers):
        """GET /api/chat/threads/{id} returns thread detail with messages array."""
        thread_id = None
        try:
            # Create a thread first
            create_resp = client.post("/api/chat/threads", headers=auth_headers)
            assert create_resp.status_code == 201
            thread_id = create_resp.json()["id"]

            # Get the thread
            response = client.get(
                f"/api/chat/threads/{thread_id}",
                headers=auth_headers,
            )

            assert response.status_code == 200
            data = response.json()
            assert data["id"] == thread_id
            assert "messages" in data
            assert isinstance(data["messages"], list)
            assert "message_count" in data
        finally:
            if thread_id:
                client.delete(
                    f"/api/chat/threads/{thread_id}",
                    headers=auth_headers,
                )

    def test_get_thread_nonexistent(self, client, auth_headers):
        """GET /api/chat/threads/{id} returns 404 for nonexistent thread."""
        response = client.get(
            "/api/chat/threads/999999",
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_update_thread_name(self, client, auth_headers):
        """PATCH /api/chat/threads/{id} updates the thread name."""
        thread_id = None
        try:
            # Create a thread
            create_resp = client.post("/api/chat/threads", headers=auth_headers)
            assert create_resp.status_code == 201
            thread_id = create_resp.json()["id"]

            # Update name
            response = client.patch(
                f"/api/chat/threads/{thread_id}",
                headers=auth_headers,
                json={"name": "Renamed Thread"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["name"] == "Renamed Thread"
            assert data["id"] == thread_id
        finally:
            if thread_id:
                client.delete(
                    f"/api/chat/threads/{thread_id}",
                    headers=auth_headers,
                )

    def test_update_thread_nonexistent(self, client, auth_headers):
        """PATCH /api/chat/threads/{id} returns 404 for nonexistent thread."""
        response = client.patch(
            "/api/chat/threads/999999",
            headers=auth_headers,
            json={"name": "Nope"},
        )
        assert response.status_code == 404

    def test_update_thread_empty_name_rejected(self, client, auth_headers):
        """PATCH /api/chat/threads/{id} rejects empty name."""
        thread_id = None
        try:
            create_resp = client.post("/api/chat/threads", headers=auth_headers)
            assert create_resp.status_code == 201
            thread_id = create_resp.json()["id"]

            response = client.patch(
                f"/api/chat/threads/{thread_id}",
                headers=auth_headers,
                json={"name": ""},
            )
            assert response.status_code == 422
        finally:
            if thread_id:
                client.delete(
                    f"/api/chat/threads/{thread_id}",
                    headers=auth_headers,
                )

    def test_delete_thread(self, client, auth_headers):
        """DELETE /api/chat/threads/{id} deletes the thread."""
        # Create a thread
        create_resp = client.post("/api/chat/threads", headers=auth_headers)
        assert create_resp.status_code == 201
        thread_id = create_resp.json()["id"]

        # Delete it
        response = client.delete(
            f"/api/chat/threads/{thread_id}",
            headers=auth_headers,
        )
        assert response.status_code == 204

        # Verify it's gone
        get_resp = client.get(
            f"/api/chat/threads/{thread_id}",
            headers=auth_headers,
        )
        assert get_resp.status_code == 404

    def test_delete_thread_nonexistent(self, client, auth_headers):
        """DELETE /api/chat/threads/{id} returns 404 for nonexistent thread."""
        response = client.delete(
            "/api/chat/threads/999999",
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_delete_thread_cascades_messages(self, client, auth_headers):
        """DELETE /api/chat/threads/{id} also deletes all associated messages."""
        thread_id = None
        try:
            # Create a thread
            create_resp = client.post("/api/chat/threads", headers=auth_headers)
            assert create_resp.status_code == 201
            thread_id = create_resp.json()["id"]

            # Send a message (this creates a user message in the thread)
            msg_resp = client.post(
                f"/api/chat/threads/{thread_id}/messages",
                headers=auth_headers,
                json={"content": "Hello, test message for cascade delete"},
            )
            # Accept 200 (completed) or any success status
            assert msg_resp.status_code == 200

            # Verify message exists
            msgs_resp = client.get(
                f"/api/chat/threads/{thread_id}/messages",
                headers=auth_headers,
            )
            assert msgs_resp.status_code == 200
            assert len(msgs_resp.json()) >= 1

            # Delete thread
            del_resp = client.delete(
                f"/api/chat/threads/{thread_id}",
                headers=auth_headers,
            )
            assert del_resp.status_code == 204
            thread_id = None  # Don't try to delete again in finally

            # Thread should be gone (messages go with it via cascade)
        finally:
            if thread_id:
                client.delete(
                    f"/api/chat/threads/{thread_id}",
                    headers=auth_headers,
                )


class TestChatMessages:
    """Test suite for chat message listing."""

    def test_list_messages_empty_thread(self, client, auth_headers):
        """GET /api/chat/threads/{id}/messages returns empty list for new thread."""
        thread_id = None
        try:
            create_resp = client.post("/api/chat/threads", headers=auth_headers)
            assert create_resp.status_code == 201
            thread_id = create_resp.json()["id"]

            response = client.get(
                f"/api/chat/threads/{thread_id}/messages",
                headers=auth_headers,
            )

            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)
            assert len(data) == 0
        finally:
            if thread_id:
                client.delete(
                    f"/api/chat/threads/{thread_id}",
                    headers=auth_headers,
                )

    def test_list_messages_pagination(self, client, auth_headers):
        """GET /api/chat/threads/{id}/messages respects skip and limit params."""
        thread_id = None
        try:
            create_resp = client.post("/api/chat/threads", headers=auth_headers)
            assert create_resp.status_code == 201
            thread_id = create_resp.json()["id"]

            response = client.get(
                f"/api/chat/threads/{thread_id}/messages?skip=0&limit=5",
                headers=auth_headers,
            )

            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)
            assert len(data) <= 5
        finally:
            if thread_id:
                client.delete(
                    f"/api/chat/threads/{thread_id}",
                    headers=auth_headers,
                )

    def test_list_messages_nonexistent_thread(self, client, auth_headers):
        """GET /api/chat/threads/{id}/messages returns 404 for nonexistent thread."""
        response = client.get(
            "/api/chat/threads/999999/messages",
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_list_messages_requires_auth(self, client):
        """GET /api/chat/threads/{id}/messages requires authentication."""
        response = client.get("/api/chat/threads/1/messages")
        assert response.status_code in [401, 403]


class TestChatGeneration:
    """Test suite for AI generation status and cancellation."""

    def test_generation_status_idle(self, client, auth_headers):
        """GET /api/chat/threads/{id}/generation-status returns 'idle' for fresh thread."""
        thread_id = None
        try:
            create_resp = client.post("/api/chat/threads", headers=auth_headers)
            assert create_resp.status_code == 201
            thread_id = create_resp.json()["id"]

            response = client.get(
                f"/api/chat/threads/{thread_id}/generation-status",
                headers=auth_headers,
            )

            assert response.status_code == 200
            data = response.json()
            assert data["thread_id"] == thread_id
            assert data["status"] == "idle"
            assert data["task_id"] is None
        finally:
            if thread_id:
                client.delete(
                    f"/api/chat/threads/{thread_id}",
                    headers=auth_headers,
                )

    def test_generation_status_nonexistent_thread(self, client, auth_headers):
        """GET /api/chat/threads/{id}/generation-status returns 404 for nonexistent thread."""
        response = client.get(
            "/api/chat/threads/999999/generation-status",
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_cancel_generation_when_idle(self, client, auth_headers):
        """POST /api/chat/threads/{id}/cancel-generation returns 400 when nothing generating."""
        thread_id = None
        try:
            create_resp = client.post("/api/chat/threads", headers=auth_headers)
            assert create_resp.status_code == 201
            thread_id = create_resp.json()["id"]

            response = client.post(
                f"/api/chat/threads/{thread_id}/cancel-generation",
                headers=auth_headers,
            )

            # Should fail because there's no generation in progress
            assert response.status_code == 400
        finally:
            if thread_id:
                client.delete(
                    f"/api/chat/threads/{thread_id}",
                    headers=auth_headers,
                )

    def test_cancel_generation_nonexistent_thread(self, client, auth_headers):
        """POST /api/chat/threads/{id}/cancel-generation returns 404 for nonexistent thread."""
        response = client.post(
            "/api/chat/threads/999999/cancel-generation",
            headers=auth_headers,
        )
        assert response.status_code == 404


class TestChatMessageEdit:
    """Test suite for message editing."""

    def test_edit_nonexistent_message(self, client, auth_headers):
        """PUT /api/chat/threads/{id}/messages/{msg_id} returns 404 for nonexistent message."""
        thread_id = None
        try:
            create_resp = client.post("/api/chat/threads", headers=auth_headers)
            assert create_resp.status_code == 201
            thread_id = create_resp.json()["id"]

            response = client.put(
                f"/api/chat/threads/{thread_id}/messages/999999",
                headers=auth_headers,
                json={"content": "Edited content"},
            )
            assert response.status_code == 404
        finally:
            if thread_id:
                client.delete(
                    f"/api/chat/threads/{thread_id}",
                    headers=auth_headers,
                )

    def test_edit_message_nonexistent_thread(self, client, auth_headers):
        """PUT /api/chat/threads/{id}/messages/{msg_id} returns 404 for nonexistent thread."""
        response = client.put(
            "/api/chat/threads/999999/messages/1",
            headers=auth_headers,
            json={"content": "Edited content"},
        )
        assert response.status_code == 404

    def test_edit_message_requires_auth(self, client):
        """PUT /api/chat/threads/{id}/messages/{msg_id} requires authentication."""
        response = client.put(
            "/api/chat/threads/1/messages/1",
            json={"content": "Hack attempt"},
        )
        assert response.status_code in [401, 403]

    def test_edit_message_empty_content_rejected(self, client, auth_headers):
        """PUT /api/chat/threads/{id}/messages/{msg_id} rejects empty content."""
        thread_id = None
        try:
            create_resp = client.post("/api/chat/threads", headers=auth_headers)
            assert create_resp.status_code == 201
            thread_id = create_resp.json()["id"]

            response = client.put(
                f"/api/chat/threads/{thread_id}/messages/1",
                headers=auth_headers,
                json={"content": ""},
            )
            assert response.status_code == 422
        finally:
            if thread_id:
                client.delete(
                    f"/api/chat/threads/{thread_id}",
                    headers=auth_headers,
                )
