"""Backups of the portal's own data (the order folders on the NAS are backed up by whatever backs up the NAS).

A backup zip holds: the order index (consistent copy), config.json, the POS price file and the staff accounts file.
It deliberately does NOT contain the PIN file, the session secret or the POS key (new ones are made after a restore)."""
import os
import shutil
import sqlite3
import tempfile
import time
import zipfile
from contextlib import closing
from pathlib import Path

from . import auth, db, settings


def backup_dir() -> Path:
    d = settings.data_dir() / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_backups() -> list:
    return sorted(backup_dir().glob("portal-*.zip"), reverse=True)


def last_backup():
    b = list_backups()
    return (b[0], b[0].stat().st_mtime) if b else (None, None)


def run_backup() -> Path:
    cfg = settings.get_config()
    stamp = settings.now().strftime("%Y%m%d-%H%M%S")
    dest = backup_dir() / f"portal-{stamp}.zip"
    tmpdir = tempfile.mkdtemp(prefix="jcm_bak_")
    try:
        copy = Path(tmpdir) / "portal.db"
        db.init()
        with closing(sqlite3.connect(db._db_path())) as src, closing(sqlite3.connect(copy)) as out:
            src.backup(out)                                     # safe even while the portal is running
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(copy, "portal.db")
            for p in (settings.CONFIG_PATH, settings.pos_rates_path(), settings.data_dir() / "staff.json",
                      settings.data_dir() / "catalog.json"):
                if Path(p).exists():
                    z.write(p, Path(p).name)
            imgs = settings.data_dir() / "catalog_images"          # pictures staff uploaded to the souvenirs gallery
            if imgs.is_dir():
                for f in imgs.glob("*.jpg"):
                    z.write(f, "catalog_images/" + f.name)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    auth.lock_down(dest)                                       # the zip holds the staff PIN hashes: owner-only
    for old in list_backups()[max(1, int(cfg["backup"]["keep"])):]:
        try:
            old.unlink()
        except OSError:
            pass
    extra = (cfg["backup"].get("extra_dir") or "").strip()
    if extra:
        try:
            Path(extra).mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, Path(extra) / dest.name)
        except OSError as e:
            print(f"[backup] could not copy to {extra}: {e}")
    return dest


def backup_loop(log):
    """Makes a backup when the newest one is older than 24 hours (checked hourly and at start-up)."""
    while True:
        try:
            _, m = last_backup()
            if m is None or time.time() - m > 24 * 3600:
                p = run_backup()
                log.info("backup written: %s", p.name)
        except Exception:
            log.exception("backup failed")
        time.sleep(3600)
