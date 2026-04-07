import argparse
import csv
import filecmp
import shutil
from pathlib import Path
from typing import Dict, List, Tuple

from gerador_ia.paths import ORGANIZED_OUTPUT_DIR, RAW_OUTPUT_DIR, resolve_project_path


DEFAULT_SOURCE_DIR = RAW_OUTPUT_DIR
DEFAULT_DEST_DIR = ORGANIZED_OUTPUT_DIR
DEFAULT_MANIFEST_GLOB = "manifest_*.csv"
MEDIA_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".gif",
    ".tif",
    ".tiff",
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".webm",
}


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def collect_manifest_files(source_dir: Path, manifest_glob: str) -> List[Path]:
    return sorted(path for path in source_dir.glob(manifest_glob) if path.is_file())


def resolve_target_folder_name(manifest_path: Path) -> str:
    stem = manifest_path.stem
    if stem.startswith("manifest_"):
        return stem[len("manifest_") :]
    return stem


def normalize_cell(value: str) -> str:
    return str(value).strip()


def looks_like_media_filename(value: str) -> bool:
    cleaned = normalize_cell(value)
    if not cleaned:
        return False
    return Path(cleaned).suffix.lower() in MEDIA_EXTENSIONS


def build_scene_key(row: List[str], row_number: int) -> Tuple[str, str, str, str]:
    source_file = normalize_cell(row[0]) if len(row) > 0 else ""
    source_tipo = normalize_cell(row[1]) if len(row) > 1 else ""
    ordem = normalize_cell(row[2]) if len(row) > 2 else ""
    scene_id = normalize_cell(row[3]) if len(row) > 3 else ""

    if source_file or source_tipo or ordem or scene_id:
        return (source_file, source_tipo, ordem, scene_id)

    return ("__row__", str(row_number), "", "")


def extract_candidate_filename(row: List[str], source_dir: Path) -> Tuple[str, bool]:
    candidates = [normalize_cell(cell) for cell in row if looks_like_media_filename(cell)]
    if not candidates:
        return "", False

    for candidate in candidates:
        if (source_dir / candidate).exists():
            return candidate, True

    return candidates[0], False


def load_manifest_filenames(
    manifest_path: Path, source_dir: Path
) -> Tuple[List[str], List[str], int]:
    selected_by_scene: Dict[Tuple[str, str, str, str], Tuple[str, bool]] = {}

    with manifest_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.reader(file)
        header = next(reader, None)
        if not header:
            raise ValueError(f"Manifest vazio: {manifest_path}")

        for row_number, row in enumerate(reader, start=2):
            if not row:
                continue

            scene_key = build_scene_key(row, row_number)
            filename, exists = extract_candidate_filename(row, source_dir)
            if not filename:
                continue

            # Mantem apenas a referencia mais recente por cena. Isso evita que
            # execucoes antigas do mesmo manifest sobrescrevam o resultado atual.
            selected_by_scene[scene_key] = (filename, exists)

    filenames: List[str] = []
    unresolved: List[str] = []
    seen = set()

    for filename, exists in selected_by_scene.values():
        if exists:
            if filename in seen:
                continue
            seen.add(filename)
            filenames.append(filename)
            continue

        unresolved.append(filename)

    return filenames, unresolved, len(selected_by_scene)


def files_match(source_path: Path, dest_path: Path) -> bool:
    if source_path.stat().st_size != dest_path.stat().st_size:
        return False
    return filecmp.cmp(str(source_path), str(dest_path), shallow=False)


def transfer_file(source_path: Path, dest_path: Path, mode: str) -> str:
    if not source_path.exists():
        return "missing"

    if dest_path.exists():
        if files_match(source_path, dest_path):
            if mode == "move":
                source_path.unlink()
                return "removed_duplicate"
            return "skip"
        return "conflict"

    if mode == "move":
        shutil.move(str(source_path), str(dest_path))
        return "moved"

    shutil.copy2(source_path, dest_path)
    return "copied"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Le os arquivos manifest_*.csv em data/outputs/raw, cria uma pasta por "
            "manifesto e move ou copia os arquivos listados."
        )
    )
    parser.add_argument(
        "--source",
        default=str(DEFAULT_SOURCE_DIR.relative_to(DEFAULT_SOURCE_DIR.parents[2])),
        help="Pasta onde estao os manifests e as imagens. Padrao: data/outputs/raw.",
    )
    parser.add_argument(
        "--dest",
        default=str(DEFAULT_DEST_DIR.relative_to(DEFAULT_DEST_DIR.parents[2])),
        help="Pasta de destino. Padrao: data/outputs/organized.",
    )
    parser.add_argument(
        "--mode",
        choices=("move", "copy"),
        default="move",
        help="Move ou copia os arquivos. Padrao: move.",
    )
    parser.add_argument(
        "--manifest-glob",
        default=DEFAULT_MANIFEST_GLOB,
        help="Padrao de busca dos manifests. Padrao: manifest_*.csv.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    source_dir = resolve_project_path(args.source)
    dest_dir = resolve_project_path(args.dest)

    if not source_dir.exists():
        raise FileNotFoundError(f"Pasta de origem nao encontrada: {source_dir}")

    ensure_dir(dest_dir)

    manifest_files = collect_manifest_files(source_dir, args.manifest_glob)
    if not manifest_files:
        raise FileNotFoundError(
            f"Nenhum manifest encontrado em {source_dir} com o padrao {args.manifest_glob}"
        )

    summary: Dict[str, int] = {
        "moved": 0,
        "copied": 0,
        "skip": 0,
        "removed_duplicate": 0,
        "missing": 0,
        "conflict": 0,
        "unresolved_manifest_rows": 0,
    }

    for manifest_path in manifest_files:
        folder_name = resolve_target_folder_name(manifest_path)
        target_dir = dest_dir / folder_name
        ensure_dir(target_dir)

        print(f"\nManifest: {manifest_path.name}")
        print(f"Pasta destino: {target_dir}")

        filenames, unresolved, total_scene_refs = load_manifest_filenames(
            manifest_path, source_dir
        )

        if unresolved:
            summary["unresolved_manifest_rows"] += len(unresolved)
            print(
                f"  Ignoradas {len(unresolved)} referencias sem arquivo existente "
                f"em {source_dir.name}."
            )

        if not filenames:
            print(
                "  Nenhum arquivo existente encontrado para este manifest "
                f"(referencias analisadas: {total_scene_refs})."
            )
            continue

        for filename in filenames:
            source_path = source_dir / filename
            dest_path = target_dir / filename
            result = transfer_file(source_path, dest_path, args.mode)
            summary[result] += 1

            if result == "missing":
                print(f"[MISSING] {filename}")
            elif result == "conflict":
                print(f"[CONFLICT] {filename} -> {dest_path.name} ja existe e e diferente")
            else:
                print(f"[{result.upper()}] {filename} -> {target_dir}")

    print("\nResumo")
    print(f"Modo: {args.mode}")
    print(f"Movidos: {summary['moved']}")
    print(f"Copiados: {summary['copied']}")
    print(f"Ja existentes: {summary['skip']}")
    print(f"Removidos da origem por duplicidade: {summary['removed_duplicate']}")
    print(f"Ausentes na origem: {summary['missing']}")
    print(f"Conflitos: {summary['conflict']}")
    print(
        "Referencias ignoradas no manifest por nao existirem na origem: "
        f"{summary['unresolved_manifest_rows']}"
    )
    print(f"Destino: {dest_dir.resolve()}")


if __name__ == "__main__":
    main()
