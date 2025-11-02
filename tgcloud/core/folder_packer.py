import os, zipfile, time, math, datetime, random, string, logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from plyer import notification
except ImportError:
    notification = None

MAX_ZIP_SIZE = 1.9 * 1024 * 1024 * 1024  # ~1.9 GB per zip
WORKERS = max(2, os.cpu_count() // 2)

def random_id(n=6):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=n))

def _read_file_bytes(path: Path):
    """Read file bytes safely (for parallel workers)."""
    try:
        return path.read_bytes()
    except Exception as e:
        logging.error(f"Failed to read {path}: {e}")
        return None

def auto_zip_folder(src_folder: Path, dest_dir: Path, base_name: str = "DriveBackup", progress_queue=None):
    """
    Compress folder into sequential ZIP parts (≤1.9 GB each).
    Never splits files. Final ZIP may be smaller.
    """
    src_folder = Path(src_folder)
    if not src_folder.exists():
        logging.error(f"Source folder not found: {src_folder}")
        return []

    # Prepare output folder
    zip_root = dest_dir / "zip_files"
    zip_root.mkdir(parents=True, exist_ok=True)
    session_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + random_id(5)
    out_dir = zip_root / session_id
    out_dir.mkdir(parents=True, exist_ok=True)

    all_files = [f for f in src_folder.rglob("*") if f.is_file()]
    if not all_files:
        logging.warning(f"No files found in {src_folder}")
        return []

    total_bytes = sum(f.stat().st_size for f in all_files)
    total_zips_est = max(1, math.ceil(total_bytes / MAX_ZIP_SIZE))
    bytes_done = 0
    zip_index = 1
    created_zips = []
    start_time = time.time()

    logging.info(f"⚡ Zipping {len(all_files)} files → target ~{total_zips_est} zips")

    def send_progress():
        if not progress_queue:
            return
        elapsed = time.time() - start_time
        pct = bytes_done / total_bytes * 100 if total_bytes else 0
        eta = (elapsed / pct * (100 - pct)) if pct > 0 else 0
        progress_queue.put({
            "type": "progress",
            "pct": round(pct, 2),
            "completed_zips": len(created_zips),
            "total_zips": total_zips_est,
            "current_zip": f"{base_name}_{zip_index:03d}.zip",
            "elapsed": elapsed,
            "eta": eta,
        })

    # Parallel reader pool
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        zip_path = out_dir / f"{base_name}_{zip_index:03d}.zip"
        zf = zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True)
        current_zip_size = 0
        created_zips.append(zip_path)

        future_map = {pool.submit(_read_file_bytes, f): f for f in all_files}

        for fut in as_completed(future_map):
            f = future_map[fut]
            rel = f.relative_to(src_folder)
            data = fut.result()
            if data is None:
                continue

            size = len(data)

            # If next file would exceed 1.9 GB, finalize current zip
            if current_zip_size + size > MAX_ZIP_SIZE and current_zip_size > 0:
                zf.close()
                logging.info(f"🧩 Finalized {zip_path.name} ({current_zip_size/1e6:.1f} MB)")
                zip_index += 1
                current_zip_size = 0
                zip_path = out_dir / f"{base_name}_{zip_index:03d}.zip"
                zf = zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True)
                created_zips.append(zip_path)

            # Write file to zip
            zf.writestr(str(rel), data)
            current_zip_size += size
            bytes_done += size
            if bytes_done % (100 * 1024 * 1024) < size:  # update roughly every 100 MB
                send_progress()

        # Final ZIP (even if tiny)
        zf.close()
        logging.info(f"✅ Finalized {zip_path.name} ({current_zip_size/1e6:.1f} MB)")

    elapsed = time.time() - start_time
    mbps = (bytes_done / 1024 / 1024) / elapsed if elapsed else 0
    logging.info(f"🏁 Completed {len(created_zips)} ZIPs in {elapsed:.1f}s ({mbps:.2f} MB/s)")
    logging.info(f"📦 Output folder: {out_dir}")

    if progress_queue:
        progress_queue.put("DONE")

    if notification:
        try:
            notification.notify(
                title="TGCloud - ZIP Complete",
                message=f"{len(created_zips)} ZIP files created in {out_dir.name}",
                app_name="TGCloud",
                timeout=5
            )
        except Exception:
            pass

    return created_zips
