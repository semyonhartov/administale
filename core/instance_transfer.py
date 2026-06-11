import json
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

from core.server_manager import INSTANCES_DIR, ServerManager


EXPORTS_DIR = Path(__file__).parent.parent / "export"
SKIP_ROOT_FILES = {"Assets.zip", "start.bat", "start.sh", "administale.json"}
SKIP_SERVER_FILES = {"HytaleServer.aot.config", "HytaleServer.jar"}
TEXT_SUFFIXES = {
    ".json", ".jsonc", ".txt", ".cfg", ".conf", ".ini", ".properties",
    ".toml", ".yaml", ".yml", ".xml", ".options", ".sh", ".bat", ".md"
}


def _slugify(value: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "server"


def _unique_slug(base_slug: str) -> str:
    slug = base_slug
    suffix = 1
    while (INSTANCES_DIR / slug).exists():
        suffix += 1
        slug = f"{base_slug}-{suffix}"
    return slug


def _copy_tree(src: Path, dst: Path):
    if src.exists():
        shutil.copytree(src, dst, dirs_exist_ok=True)


def _copy_file(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _copy_text_files(src_root: Path, dst_root: Path):
    for path in src_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        rel = path.relative_to(src_root)
        if rel.parts == ("administale.json",):
            continue
        _copy_file(path, dst_root / path.relative_to(src_root))


def _should_skip_export(path: Path, source_root: Path) -> bool:
    rel = path.relative_to(source_root)
    if rel.parts[0] in SKIP_ROOT_FILES:
        return True
    if len(rel.parts) >= 2 and rel.parts[0] == "Server" and rel.parts[1] in SKIP_SERVER_FILES:
        return True
    return rel.parts[0] == "administale.json"


def _write_manifest(export_root: Path, server, data: dict):
    manifest = {
        "name": server.name,
        "slug": server.slug,
        "version": server.version,
        "exported_at": datetime.now().isoformat(),
        **{k: v for k, v in data.items() if k != "server"},
    }
    with open(export_root / "administale-export.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)


def export_instance(server, options: dict) -> list[str]:
    source = Path(server.path)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    export_root = EXPORTS_DIR / f"{server.slug}_{stamp}"
    export_root.mkdir(parents=True, exist_ok=True)

    if options.get("include_all"):
        if options.get("as_archive", True):
            archive_path = EXPORTS_DIR / f"{server.slug}_{stamp}.zip"
            with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for path in source.rglob("*"):
                    if _should_skip_export(path, source):
                        continue
                    zf.write(path, arcname=Path(server.slug) / path.relative_to(source))
            shutil.rmtree(export_root, ignore_errors=True)
            return [str(archive_path)]

        target = export_root / server.slug
        for path in source.rglob("*"):
            if _should_skip_export(path, source):
                continue
            destination = target / path.relative_to(source)
            if path.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                _copy_file(path, destination)
        return [str(target)]

    if options.get("include_config"):
        _copy_text_files(source, export_root / "config")
    if options.get("include_world"):
        _copy_tree(source / "universe", export_root / "universe")
    if options.get("include_mods"):
        _copy_tree(source / "Server" / "mods", export_root / "Server" / "mods")
    if options.get("include_logs"):
        _copy_tree(source / "logs", export_root / "logs")
        _copy_tree(source / "Server" / "logs", export_root / "Server" / "logs")
    if options.get("include_other"):
        for path in source.iterdir():
            if path.name in {"universe", "logs", "Server", "administale.json", *SKIP_ROOT_FILES}:
                continue
            if path.is_file() and path.suffix.lower() not in TEXT_SUFFIXES:
                _copy_file(path, export_root / path.name)

    _write_manifest(export_root, server, options)

    if options.get("as_archive", True):
        archive_path = EXPORTS_DIR / f"{server.slug}_{stamp}.zip"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in export_root.rglob("*"):
                zf.write(path, arcname=path.relative_to(export_root))
        shutil.rmtree(export_root, ignore_errors=True)
        return [str(archive_path)]

    return [str(export_root)]


def _flatten_single_directory(target: Path):
    children = list(target.iterdir())
    if len(children) != 1 or not children[0].is_dir():
        return
    nested = children[0]
    for item in list(nested.iterdir()):
        shutil.move(str(item), str(target / item.name))
    nested.rmdir()


def import_instance(import_path: str):
    source = Path(import_path)
    slug = _unique_slug(_slugify(source.stem))
    target = INSTANCES_DIR / slug
    target.mkdir(parents=True, exist_ok=True)

    if source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source, "r") as zf:
            zf.extractall(target)
        _flatten_single_directory(target)
    else:
        _copy_file(source, target / source.name)

    manager = ServerManager()
    manager.discover_servers()
    return next((server for server in manager.get_all_servers() if Path(server.path).resolve() == target.resolve()), None)
