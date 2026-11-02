import zipfile
import pytest
from app.media_restore import restore_media_archive
from app.config import settings

NAME = 'asset-1-' + 'a' * 32 + '.png'


def test_media_restore_preserves_bytes_and_never_overwrites(tmp_path):
    archive = tmp_path / 'backup.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr(NAME, b'original bytes')
    destination = tmp_path / 'restored'
    assert restore_media_archive(archive, destination) == 1
    assert (destination / NAME).read_bytes() == b'original bytes'
    with pytest.raises(ValueError, match='must not exist'):
        restore_media_archive(archive, destination)


@pytest.mark.parametrize('name', ['../escaped.png', '/absolute.png', 'subdir/asset.png', '.env'])
def test_invalid_archive_names_create_nothing(tmp_path, name):
    archive = tmp_path / 'bad.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr(name, b'payload')
    destination = tmp_path / 'restored'
    with pytest.raises(ValueError):
        restore_media_archive(archive, destination)
    assert not destination.exists()


def test_archive_quota_is_checked_before_creating_destination(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'media_disk_quota_bytes', 1)
    archive = tmp_path / 'large.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr(NAME, b'too large')
    destination = tmp_path / 'restored'
    with pytest.raises(ValueError, match='quota'):
        restore_media_archive(archive, destination)
    assert not destination.exists()
