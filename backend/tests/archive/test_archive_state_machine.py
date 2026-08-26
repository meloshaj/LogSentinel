import pytest
from backend.app.archive.s3_client import LocalMockStorageClient
from backend.app.archive.verifier import ArchiveVerifier
import hashlib
import io
import pyarrow as pa
import pyarrow.parquet as pq

def test_mock_storage_client():
    client = LocalMockStorageClient(base_dir="/tmp/test_archive_mock")
    
    # Test put
    assert client.put_if_absent("test/obj.txt", b"hello world") == True
    assert client.put_if_absent("test/obj.txt", b"new data") == False
    
    # Test head
    meta = client.head("test/obj.txt")
    assert meta is not None
    assert meta["ContentLength"] == len(b"hello world")
    
    # Test get_stream
    stream = client.get_stream("test/obj.txt")
    assert stream is not None
    assert stream.read() == b"hello world"
    stream.close()
    
    # Test delete
    assert client.delete("test/obj.txt") == True
    assert client.head("test/obj.txt") is None

def test_verifier():
    client = LocalMockStorageClient(base_dir="/tmp/test_archive_mock2")
    verifier = ArchiveVerifier(client)
    
    # Create valid parquet
    schema = pa.schema([('id', pa.int32()), ('val', pa.string())])
    table = pa.Table.from_arrays([
        pa.array([1, 2, 3]),
        pa.array(["a", "b", "c"])
    ], schema=schema)
    
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink)
    raw_bytes = sink.getvalue().to_pybytes()
    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    
    client.put_if_absent("test/data.parquet", raw_bytes)
    
    # Test valid record
    manifest = {
        "object_key": "test/data.parquet",
        "sha256": sha256,
        "row_count": 3
    }
    assert verifier.verify_archive(manifest) == True
    
    # Test corrupt checksum
    corrupt_manifest_1 = {
        "object_key": "test/data.parquet",
        "sha256": "badhash",
        "row_count": 3
    }
    assert verifier.verify_archive(corrupt_manifest_1) == False
    
    # Test wrong row count
    corrupt_manifest_2 = {
        "object_key": "test/data.parquet",
        "sha256": sha256,
        "row_count": 4
    }
    assert verifier.verify_archive(corrupt_manifest_2) == False
