import subprocess
import zipfile

import pytest

from core import documents
from core.documents import MacExtractor


@pytest.mark.parametrize("sizes", [(1025,), (600, 600)])
def test_docx_expansion_is_bounded_before_starting_textutil(tmp_path, monkeypatch, sizes):
    path = tmp_path / "compressed.docx"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, size in enumerate(sizes):
            archive.writestr(f"word/part{index}.xml", b"a" * size)
    monkeypatch.setattr(documents, "MAX_FILE", 1024)
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, b"document", b"")

    monkeypatch.setattr(documents.subprocess, "run", run)
    with pytest.raises(ValueError, match="解压后过大"):
        MacExtractor().extract(path)
    assert not calls
