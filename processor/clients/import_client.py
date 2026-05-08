import json
import logging
import math

import requests

from .base_client import BaseClient

log = logging.getLogger()

MAX_REQUEST_SIZE_BYTES = 1 * 1024 * 1024  # AWS API Gateway payload limit of 10MB
DEFAULT_BATCH_SIZE = 1000  # Default batch size when file list is empty


class ImportFile:
    def __init__(self, upload_key, file_path, local_path):
        self.upload_key = upload_key
        self.file_path = file_path
        self.local_path = local_path

    def __repr__(self):
        return f"ImportFile(upload_key={self.upload_key}, file_path={self.file_path}, local_path={self.local_path})"


class ImportClient(BaseClient):
    def __init__(self, api_host, session_manager):
        super().__init__(session_manager)

        self.api_host = api_host

    @BaseClient.retry_with_refresh
    def create(self, integration_id, dataset_id, package_id, timeseries_files):
        url = f"{self.api_host}/import/manifest?dataset_id={dataset_id}"

        headers = {"Content-type": "application/json", "Authorization": f"Bearer {self.session_manager.session_token}"}

        body = {
            "integration_id": integration_id,
            "package_id": package_id,
            "import_type": "timeseries",
            "files": [{"upload_key": str(file.upload_key), "file_path": file.file_path} for file in timeseries_files],
        }

        try:
            response = requests.post(url, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()

            return data["id"]
        except requests.HTTPError as e:
            log.error(f"failed to create import with error: {e}")
            raise e
        except json.JSONDecodeError as e:
            log.error(f"failed to decode import response with error: {e}")
            raise e
        except Exception as e:
            log.error(f"failed to get import with error: {e}")
            raise e

    @BaseClient.retry_with_refresh
    def append_files(self, import_id, dataset_id, timeseries_files):
        """
        Append files to an existing import manifest.

        Args:
            import_id: The import manifest ID
            dataset_id: The dataset ID
            timeseries_files: List of ImportFile objects to append

        Returns:
            dict: Response from the API
        """
        url = f"{self.api_host}/import/{import_id}/files?dataset_id={dataset_id}"

        headers = {"Content-type": "application/json", "Authorization": f"Bearer {self.session_manager.session_token}"}

        body = {
            "files": [{"upload_key": str(file.upload_key), "file_path": file.file_path} for file in timeseries_files]
        }

        try:
            response = requests.post(url, headers=headers, json=body)
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as e:
            log.error(f"failed to append files to import {import_id} with error: {e}")
            raise e
        except json.JSONDecodeError as e:
            log.error(f"failed to decode append files response with error: {e}")
            raise e
        except Exception as e:
            log.error(f"failed to append files to import {import_id} with error: {e}")
            raise e

    def create_batched(self, integration_id, dataset_id, package_id, timeseries_files):
        """
        Create an import manifest with batched file additions to avoid API Gateway size limits.

        For large file lists (e.g., 50,000+ files), this method:
        1. Creates the initial import with the first batch of files
        2. Appends remaining files in subsequent batches using the /import/{id}/files endpoint

        Args:
            integration_id: The workflow/integration ID
            dataset_id: The dataset ID
            package_id: The package ID
            timeseries_files: List of all ImportFile objects

        Returns:
            str: The import ID
        """
        if not timeseries_files:
            raise ValueError("No files provided for import")

        batch_size = calculate_batch_size(timeseries_files)
        total_files = len(timeseries_files)
        total_batches = math.ceil(total_files / batch_size)

        log.info(
            f"dataset_id={dataset_id} creating import manifest with {total_files} files in {total_batches} batch(es) (batch_size={batch_size})"
        )

        first_batch = timeseries_files[:batch_size]
        import_id = self.create(integration_id, dataset_id, package_id, first_batch)

        log.info(f"import_id={import_id} created manifest with initial batch of {len(first_batch)} files")

        for batch_num in range(1, total_batches):
            start_idx = batch_num * batch_size
            end_idx = min(start_idx + batch_size, total_files)
            batch = timeseries_files[start_idx:end_idx]

            self.append_files(import_id, dataset_id, batch)
            log.info(f"import_id={import_id} appended batch {batch_num + 1}/{total_batches} with {len(batch)} files")

        return import_id

    @BaseClient.retry_with_refresh
    def get_presign_url(self, import_id, dataset_id, upload_key):
        url = f"{self.api_host}/import/{import_id}/upload/{upload_key}/presign?dataset_id={dataset_id}"

        headers = {"Content-type": "application/json", "Authorization": f"Bearer {self.session_manager.session_token}"}

        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()

            return data["url"]
        except requests.HTTPError as e:
            log.error(f"failed to generate pre-sign URL for import file with error: {e}")
            raise e
        except json.JSONDecodeError as e:
            log.error(f"failed to decode pre-sign URL response with error: {e}")
            raise e
        except Exception as e:
            log.error(f"failed to generate pre-sign URL for import file with error: {e}")
            raise e


def calculate_batch_size(sample_files, max_size_bytes=MAX_REQUEST_SIZE_BYTES):
    """
    Calculate the optimal batch size for manifest files based on actual payload size.

    Args:
        sample_files: List of ImportFile objects to estimate size from
        max_size_bytes: Maximum request size in bytes (default: 10MB API Gateway limit)

    Returns:
        int: Number of files per batch
    """
    if not sample_files:
        return DEFAULT_BATCH_SIZE

    # Calculate actual size of a sample file entry
    sample_size = 0
    sample_count = min(100, len(sample_files))
    for file in sample_files[:sample_count]:
        entry = {"upload_key": str(file.upload_key), "file_path": file.file_path}
        sample_size += len(json.dumps(entry)) + 1  # +1 for comma separator

    avg_bytes_per_file = sample_size / sample_count

    # calculate batch size with safety margin (80% of limit)
    # to allow for request content overhead
    usable_size = max_size_bytes * 0.8
    batch_size = int(usable_size / avg_bytes_per_file)

    # Ensure at least 1 file per batch
    return max(1, batch_size)
