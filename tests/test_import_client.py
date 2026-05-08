import json
import uuid
from unittest.mock import patch

import pytest
import responses
from clients.import_client import MAX_REQUEST_SIZE_BYTES, ImportClient, ImportFile, calculate_batch_size


class TestImportFile:
    """Tests for ImportFile class."""

    def test_initialization(self):
        """Test ImportFile initialization."""
        upload_key = uuid.uuid4()
        import_file = ImportFile(
            upload_key=upload_key, file_path="N:channel:test_1000_2000.bin.gz", local_path="/path/to/file.bin.gz"
        )

        assert import_file.upload_key == upload_key
        assert import_file.file_path == "N:channel:test_1000_2000.bin.gz"
        assert import_file.local_path == "/path/to/file.bin.gz"

    def test_repr(self):
        """Test ImportFile string representation."""
        upload_key = uuid.uuid4()
        import_file = ImportFile(upload_key, "file_path", "/local/path")

        repr_str = repr(import_file)

        assert "ImportFile" in repr_str
        assert "upload_key=" in repr_str
        assert "file_path=" in repr_str
        assert "local_path=" in repr_str


class TestCalculateBatchSize:
    """Tests for calculate_batch_size function."""

    def test_calculates_batch_size_from_sample(self):
        """Test batch size calculation based on sample files."""
        files = [
            ImportFile(uuid.uuid4(), f"N:channel:id_{i}_1000_2000.bin.gz", f"/path/{i}.bin.gz") for i in range(100)
        ]

        batch_size = calculate_batch_size(files)

        # Should calculate a reasonable batch size
        assert batch_size > 0
        # Batch size is based on 1MB limit, so should be a reasonable value
        assert batch_size > 100  # Files are small, so batch should be large

    def test_uses_up_to_100_samples(self):
        """Test that up to 100 files are sampled for size estimation."""
        # Create files with predictable size
        files = [ImportFile(uuid.uuid4(), f"path_{i}.bin.gz", f"/path/{i}.bin.gz") for i in range(200)]

        batch_size = calculate_batch_size(files)

        # Should work without error
        assert batch_size > 0

    def test_handles_fewer_than_100_files(self):
        """Test with fewer than 100 files."""
        files = [ImportFile(uuid.uuid4(), "path.bin.gz", "/path/file.bin.gz") for _ in range(10)]

        batch_size = calculate_batch_size(files)

        assert batch_size > 0

    def test_respects_max_size_parameter(self):
        """Test that max_size_bytes parameter is respected."""
        files = [ImportFile(uuid.uuid4(), f"file_{i}.bin.gz", f"/path/{i}.bin.gz") for i in range(100)]

        # Small max size should result in smaller batch
        small_batch = calculate_batch_size(files, max_size_bytes=10000)
        large_batch = calculate_batch_size(files, max_size_bytes=10000000)

        assert small_batch < large_batch

    def test_minimum_batch_size_is_one(self):
        """Test that batch size is at least 1."""
        # Create file with very long path
        long_path = "N:channel:" + "x" * 10000 + ".bin.gz"
        files = [ImportFile(uuid.uuid4(), long_path, "/path")]

        batch_size = calculate_batch_size(files, max_size_bytes=100)

        assert batch_size >= 1

    def test_applies_80_percent_safety_margin(self):
        """Test that 80% safety margin is applied."""
        files = [ImportFile(uuid.uuid4(), f"file_{i}.bin.gz", f"/path/{i}.bin.gz") for i in range(100)]

        # Calculate average size per file
        total_size = 0
        for file in files[:100]:
            entry = {"upload_key": str(file.upload_key), "file_path": file.file_path}
            total_size += len(json.dumps(entry)) + 1
        avg_size = total_size / 100

        batch_size = calculate_batch_size(files)

        # Batch size should respect 80% of max (with some tolerance for calculation)
        expected_max = int((MAX_REQUEST_SIZE_BYTES * 0.8) / avg_size)
        assert batch_size <= expected_max + 1


class TestImportClientCreate:
    """Tests for ImportClient.create method."""

    @responses.activate
    def test_create_success(self, mock_session_manager):
        """Test successful import creation."""
        responses.add(
            responses.POST,
            "https://api.test.com/import/manifest?dataset_id=dataset-123",
            json={"id": "import-id-456"},
            status=200,
        )

        client = ImportClient("https://api.test.com", mock_session_manager)
        files = [ImportFile(uuid.uuid4(), "file.bin.gz", "/path/file.bin.gz")]

        result = client.create("integration-1", "dataset-123", "package-1", files)

        assert result == "import-id-456"

    @responses.activate
    def test_create_includes_correct_headers(self, mock_session_manager):
        """Test that create includes correct authorization headers."""
        responses.add(
            responses.POST,
            "https://api.test.com/import/manifest?dataset_id=dataset-123",
            json={"id": "import-id"},
            status=200,
        )

        client = ImportClient("https://api.test.com", mock_session_manager)
        files = [ImportFile(uuid.uuid4(), "file.bin.gz", "/path")]

        client.create("int-1", "dataset-123", "pkg-1", files)

        # Check request headers
        assert responses.calls[0].request.headers["Authorization"] == "Bearer mock-token-12345"
        assert responses.calls[0].request.headers["Content-type"] == "application/json"

    @responses.activate
    def test_create_includes_correct_body(self, mock_session_manager):
        """Test that create sends correct request body."""
        responses.add(
            responses.POST, "https://api.test.com/import/manifest?dataset_id=ds-1", json={"id": "import-id"}, status=200
        )

        client = ImportClient("https://api.test.com", mock_session_manager)
        upload_key = uuid.uuid4()
        files = [ImportFile(upload_key, "test.bin.gz", "/path/test.bin.gz")]

        client.create("integration-123", "ds-1", "pkg-1", files)

        body = json.loads(responses.calls[0].request.body)
        assert body["integration_id"] == "integration-123"
        assert body["package_id"] == "pkg-1"
        assert body["import_type"] == "timeseries"
        assert len(body["files"]) == 1
        assert body["files"][0]["upload_key"] == str(upload_key)
        assert body["files"][0]["file_path"] == "test.bin.gz"

    @responses.activate
    def test_create_raises_on_http_error(self, mock_session_manager):
        """Test that HTTP errors are raised."""
        responses.add(
            responses.POST,
            "https://api.test.com/import/manifest?dataset_id=ds-1",
            json={"error": "Bad request"},
            status=400,
        )

        client = ImportClient("https://api.test.com", mock_session_manager)
        files = [ImportFile(uuid.uuid4(), "file.bin.gz", "/path")]

        with pytest.raises(Exception):
            client.create("int-1", "ds-1", "pkg-1", files)


class TestImportClientAppendFiles:
    """Tests for ImportClient.append_files method."""

    @responses.activate
    def test_append_files_success(self, mock_session_manager):
        """Test successful file append."""
        responses.add(
            responses.POST,
            "https://api.test.com/import/import-123/files?dataset_id=ds-1",
            json={"success": True},
            status=200,
        )

        client = ImportClient("https://api.test.com", mock_session_manager)
        files = [ImportFile(uuid.uuid4(), "file.bin.gz", "/path")]

        result = client.append_files("import-123", "ds-1", files)

        assert result == {"success": True}

    @responses.activate
    def test_append_files_correct_body(self, mock_session_manager):
        """Test that append_files sends correct body."""
        responses.add(
            responses.POST,
            "https://api.test.com/import/import-123/files?dataset_id=ds-1",
            json={"success": True},
            status=200,
        )

        client = ImportClient("https://api.test.com", mock_session_manager)
        upload_key = uuid.uuid4()
        files = [ImportFile(upload_key, "new_file.bin.gz", "/path/new.bin.gz")]

        client.append_files("import-123", "ds-1", files)

        body = json.loads(responses.calls[0].request.body)
        assert "files" in body
        assert body["files"][0]["upload_key"] == str(upload_key)
        assert body["files"][0]["file_path"] == "new_file.bin.gz"


class TestImportClientCreateBatched:
    """Tests for ImportClient.create_batched method."""

    def test_create_batched_empty_files_raises(self, mock_session_manager):
        """Test that empty file list raises ValueError."""
        client = ImportClient("https://api.test.com", mock_session_manager)

        with pytest.raises(ValueError, match="No files provided"):
            client.create_batched("int-1", "ds-1", "pkg-1", [])

    @responses.activate
    def test_create_batched_single_batch(self, mock_session_manager):
        """Test create_batched with files that fit in single batch."""
        responses.add(
            responses.POST,
            "https://api.test.com/import/manifest?dataset_id=ds-1",
            json={"id": "import-123"},
            status=200,
        )

        client = ImportClient("https://api.test.com", mock_session_manager)
        files = [ImportFile(uuid.uuid4(), f"file_{i}.bin.gz", f"/path/{i}") for i in range(10)]

        result = client.create_batched("int-1", "ds-1", "pkg-1", files)

        assert result == "import-123"
        # Should only call create once (no append needed)
        assert len(responses.calls) == 1

    @responses.activate
    def test_create_batched_multiple_batches(self, mock_session_manager):
        """Test create_batched with files requiring multiple batches."""
        responses.add(
            responses.POST,
            "https://api.test.com/import/manifest?dataset_id=ds-1",
            json={"id": "import-123"},
            status=200,
        )
        # Add responses for append calls
        responses.add(
            responses.POST,
            "https://api.test.com/import/import-123/files?dataset_id=ds-1",
            json={"success": True},
            status=200,
        )

        client = ImportClient("https://api.test.com", mock_session_manager)

        # Create many files to force batching
        files = [
            ImportFile(uuid.uuid4(), f"N:channel:long-id_{i}_1234567890_9876543210.bin.gz", f"/path/{i}")
            for i in range(1000)
        ]

        # Mock calculate_batch_size to return a small batch size
        with patch("clients.import_client.calculate_batch_size", return_value=100):
            result = client.create_batched("int-1", "ds-1", "pkg-1", files)

        assert result == "import-123"
        # Should have 1 create + 9 appends (1000 files / 100 batch size = 10 batches)
        assert len(responses.calls) == 10

    @responses.activate
    def test_create_batched_preserves_file_order(self, mock_session_manager):
        """Test that files are processed in order across batches."""
        create_body = None
        append_bodies = []

        def capture_create(request):
            nonlocal create_body
            create_body = json.loads(request.body)
            return (200, {}, json.dumps({"id": "import-123"}))

        def capture_append(request):
            append_bodies.append(json.loads(request.body))
            return (200, {}, json.dumps({"success": True}))

        responses.add_callback(
            responses.POST,
            "https://api.test.com/import/manifest?dataset_id=ds-1",
            callback=capture_create,
            content_type="application/json",
        )
        responses.add_callback(
            responses.POST,
            "https://api.test.com/import/import-123/files?dataset_id=ds-1",
            callback=capture_append,
            content_type="application/json",
        )

        client = ImportClient("https://api.test.com", mock_session_manager)
        files = [ImportFile(uuid.uuid4(), f"file_{i:04d}.bin.gz", f"/path/{i}") for i in range(25)]

        with patch("clients.import_client.calculate_batch_size", return_value=10):
            client.create_batched("int-1", "ds-1", "pkg-1", files)

        # First batch should have files 0-9
        first_batch_paths = [f["file_path"] for f in create_body["files"]]
        assert first_batch_paths == [f"file_{i:04d}.bin.gz" for i in range(10)]

        # Second batch should have files 10-19
        second_batch_paths = [f["file_path"] for f in append_bodies[0]["files"]]
        assert second_batch_paths == [f"file_{i:04d}.bin.gz" for i in range(10, 20)]


class TestImportClientGetPresignUrl:
    """Tests for ImportClient.get_presign_url method."""

    @responses.activate
    def test_get_presign_url_success(self, mock_session_manager):
        """Test successful presign URL retrieval."""
        responses.add(
            responses.GET,
            "https://api.test.com/import/import-123/upload/upload-key-456/presign?dataset_id=ds-1",
            json={"url": "https://s3.amazonaws.com/presigned-url"},
            status=200,
        )

        client = ImportClient("https://api.test.com", mock_session_manager)
        result = client.get_presign_url("import-123", "ds-1", "upload-key-456")

        assert result == "https://s3.amazonaws.com/presigned-url"

    @responses.activate
    def test_get_presign_url_includes_auth(self, mock_session_manager):
        """Test that presign URL request includes authorization."""
        responses.add(
            responses.GET,
            "https://api.test.com/import/import-123/upload/key/presign?dataset_id=ds-1",
            json={"url": "https://s3.amazonaws.com/url"},
            status=200,
        )

        client = ImportClient("https://api.test.com", mock_session_manager)
        client.get_presign_url("import-123", "ds-1", "key")

        assert responses.calls[0].request.headers["Authorization"] == "Bearer mock-token-12345"

    @responses.activate
    def test_get_presign_url_raises_on_error(self, mock_session_manager):
        """Test that HTTP errors are raised."""
        responses.add(
            responses.GET,
            "https://api.test.com/import/import-123/upload/key/presign?dataset_id=ds-1",
            json={"error": "Not found"},
            status=404,
        )

        client = ImportClient("https://api.test.com", mock_session_manager)

        with pytest.raises(Exception):
            client.get_presign_url("import-123", "ds-1", "key")


class TestImportClientRetryBehavior:
    """Tests for retry behavior with session refresh."""

    @responses.activate
    def test_create_retries_on_401(self, mock_session_manager):
        """Test that create retries after 401 and session refresh."""
        # First call returns 401
        responses.add(
            responses.POST,
            "https://api.test.com/import/manifest?dataset_id=ds-1",
            json={"error": "Unauthorized"},
            status=401,
        )
        # Second call succeeds
        responses.add(
            responses.POST,
            "https://api.test.com/import/manifest?dataset_id=ds-1",
            json={"id": "import-123"},
            status=200,
        )

        client = ImportClient("https://api.test.com", mock_session_manager)
        files = [ImportFile(uuid.uuid4(), "file.bin.gz", "/path")]

        result = client.create("int-1", "ds-1", "pkg-1", files)

        assert result == "import-123"
        mock_session_manager.refresh_session.assert_called_once()
        assert len(responses.calls) == 2
