"""Validate and restore media archives into a new isolated directory only."""
import argparse
import re
import stat
import zipfile
from pathlib import Path

from .config import settings


def restore_media_archive(archive_path: Path, destination: Path) -> int:
    destination = destination.resolve()
    if destination.exists():
        raise ValueError('Restore destination must not exist; existing assets are never overwritten')
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        names = set()
        total = 0
        for entry in entries:
            if not re.fullmatch(r'asset-\d+-[0-9a-f]{32}\.(png|jpg|webp|mp4|webm)', entry.filename):
                raise ValueError('Invalid archive asset name or path')
            if entry.filename in names or stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError('Duplicate assets or symlinks are forbidden')
            names.add(entry.filename)
            if not 0 < entry.file_size <= settings.media_max_bytes:
                raise ValueError('Archive asset exceeds file size limit')
            total += entry.file_size
        if total > settings.media_disk_quota_bytes:
            raise ValueError('Archive exceeds media disk quota')
        # Read/CRC-check every member before creating the destination. No
        # extractall: archive names never select a path outside the target.
        for entry in entries:
            with archive.open(entry) as source:
                while source.read(1024 * 1024):
                    pass
        destination.mkdir(parents=True, exist_ok=False)
        for entry in entries:
            with archive.open(entry) as source, (destination / entry.filename).open('xb') as target:
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
        return len(entries)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    print('Restored assets:', restore_media_archive(args.archive, args.destination))
