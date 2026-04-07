import argparse
import csv
import json
import os
import random
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from google import genai
from google.genai import types

from gerador_ia.paths import JSON_INPUT_DIR, RAW_OUTPUT_DIR, resolve_project_path


MODEL_NAME = "gemini-3.1-flash-image-preview"
DEFAULT_DELAY_SECONDS = 1.5
DEFAULT_OUTPUT_DIR = RAW_OUTPUT_DIR
IMAGE_EXT = ".png"
DEFAULT_ASPECT_RATIO = "16:9"
DEFAULT_IMAGE_SIZE = "1K"
DEFAULT_MAX_RETRIES = 4
DEFAULT_START_INDEX = 1
DEFAULT_MANIFEST_MODE = "append"


def slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_-]+", "_", text, flags=re.UNICODE)
    return text.strip("_")


def safe_filename_part(text: str, fallback: str = "sem_nome") -> str:
    cleaned = slugify(text)
    return cleaned if cleaned else fallback


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def load_json_file(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def extract_scenes(data: Dict[str, Any], source_name: str) -> List[Dict[str, Any]]:
    scenes: List[Dict[str, Any]] = []

    if isinstance(data, dict) and "cenas" in data and isinstance(data["cenas"], list):
        tipo = data.get("tipo", source_name)
        for item in data["cenas"]:
            scene = dict(item)
            scene["_source_tipo"] = tipo
            scene["_source_file"] = source_name
            scenes.append(scene)
        return scenes

    if isinstance(data, dict) and "jsons" in data and isinstance(data["jsons"], list):
        for block in data["jsons"]:
            tipo = block.get("tipo", source_name)
            for item in block.get("cenas", []):
                scene = dict(item)
                scene["_source_tipo"] = tipo
                scene["_source_file"] = source_name
                scenes.append(scene)
        return scenes

    raise ValueError(f"JSON invalido em '{source_name}'. Esperado 'cenas' ou 'jsons'.")


def resolve_scene_name(scene: Dict[str, Any]) -> str:
    for key in ("nome", "texto_guia", "referencia", "scene_name", "scene_id", "id"):
        value = scene.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "cena"


def resolve_scene_id(scene: Dict[str, Any], fallback_index: Optional[int] = None) -> str:
    for key in ("scene_id", "id", "referencia"):
        value = scene.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    if fallback_index is None:
        return ""

    return f"scene_{fallback_index:03d}"


def resolve_prompt(scene: Dict[str, Any]) -> str:
    for key in ("prompt_imagem", "prompt", "image_prompt"):
        value = scene.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError(f"Cena sem prompt_imagem/prompt: {scene}")


def resolve_order(scene: Dict[str, Any], fallback_index: int) -> int:
    ordem = scene.get("ordem")
    if isinstance(ordem, int):
        return ordem
    if isinstance(ordem, str) and ordem.isdigit():
        return int(ordem)
    return fallback_index


def build_output_filename(scene: Dict[str, Any], ordem: int, index: int) -> str:
    source_tipo = safe_filename_part(scene.get("_source_tipo", "json"))
    scene_id = safe_filename_part(resolve_scene_id(scene, fallback_index=index))
    scene_name = safe_filename_part(resolve_scene_name(scene))

    if scene_name == scene_id:
        return f"{ordem:03d}_{scene_id}_{source_tipo}{IMAGE_EXT}"

    return f"{ordem:03d}_{scene_id}_{scene_name}_{source_tipo}{IMAGE_EXT}"


def save_manifest_row(
    writer: csv.DictWriter,
    scene: Dict[str, Any],
    ordem: int,
    filename: str,
    prompt: str,
    status: str,
    error: str = "",
) -> None:
    writer.writerow(
        {
            "source_file": scene.get("_source_file", ""),
            "source_tipo": scene.get("_source_tipo", ""),
            "ordem": ordem,
            "scene_id": resolve_scene_id(scene),
            "nome": resolve_scene_name(scene),
            "tempo": scene.get("tempo", ""),
            "tempo_inicio": scene.get("tempo_inicio", ""),
            "tempo_fim": scene.get("tempo_fim", ""),
            "duracao_segundos": scene.get("duracao_segundos", ""),
            "arquivo": filename,
            "status": status,
            "erro": error,
            "prompt": prompt,
        }
    )


def is_quota_exhausted_error(error_text: str) -> bool:
    text = error_text.lower()
    quota_markers = (
        "resource_exhausted",
        "exceeded your current quota",
        "quota exceeded",
        "billing account",
        "billing",
        "spending cap",
        "insufficient funds",
        "credit balance",
    )
    return any(marker in text for marker in quota_markers)


def extract_retry_after_seconds(error_text: str) -> Optional[float]:
    retry_after_patterns = (
        r"retry[_\s-]*after[:=\s]+(\d+(?:\.\d+)?)\s*s",
        r"retry[_\s-]*after[:=\s]+(\d+(?:\.\d+)?)\s*seconds?",
        r"seconds:\s*(\d+)",
    )

    for pattern in retry_after_patterns:
        match = re.search(pattern, error_text, flags=re.IGNORECASE)
        if match:
            return float(match.group(1))

    return None


def is_retryable_error(error_text: str) -> bool:
    if is_quota_exhausted_error(error_text):
        return False

    text = error_text.lower()
    retryable_markers = (
        "429",
        "rate limit",
        "too many requests",
        "503",
        "unavailable",
        "deadline expired",
        "deadline exceeded",
        "timed out",
        "internal error",
    )
    return any(marker in text for marker in retryable_markers)


def generate_image_with_gemini(
    client: genai.Client,
    prompt: str,
    output_path: Path,
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
    image_size: str = DEFAULT_IMAGE_SIZE,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> None:
    last_error: Optional[Exception] = None

    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=[prompt],
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=types.ImageConfig(
                        aspect_ratio=aspect_ratio,
                        image_size=image_size,
                    ),
                ),
            )

            if not response.candidates:
                prompt_feedback = getattr(response, "prompt_feedback", None)
                raise RuntimeError(
                    f"Resposta sem candidates. prompt_feedback={prompt_feedback}"
                )

            candidate = response.candidates[0]
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", []) if content else []

            for part in parts:
                inline_data = getattr(part, "inline_data", None)
                if inline_data and getattr(inline_data, "data", None):
                    output_path.write_bytes(inline_data.data)
                    return

            finish_reason = getattr(candidate, "finish_reason", None)
            finish_message = getattr(candidate, "finish_message", None)
            prompt_feedback = getattr(response, "prompt_feedback", None)

            raise RuntimeError(
                "Nenhuma imagem foi retornada pela API. "
                f"finish_reason={finish_reason} | "
                f"finish_message={finish_message} | "
                f"prompt_feedback={prompt_feedback}"
            )

        except Exception as exc:
            last_error = exc
            error_text = str(exc)

            if not is_retryable_error(error_text) or attempt == max_retries - 1:
                raise

            retry_after_seconds = extract_retry_after_seconds(error_text)
            wait_seconds = retry_after_seconds or ((2 ** attempt) + random.uniform(0, 1))
            print(
                f"    Retry {attempt + 1}/{max_retries - 1} em {wait_seconds:.1f}s "
                f"por erro temporario: {error_text}"
            )
            time.sleep(wait_seconds)

    if last_error:
        raise last_error


def collect_json_files(input_path: Path) -> List[Path]:
    if input_path.is_file() and input_path.suffix.lower() == ".json":
        return [input_path]

    if input_path.is_dir():
        json_files = sorted(path for path in input_path.glob("*.json") if path.is_file())
        image_files = [
            path for path in json_files if not path.stem.lower().startswith("videos_")
        ]
        return image_files or json_files

    raise FileNotFoundError(f"Nao encontrei: {input_path}")


def load_all_scenes(json_files: List[Path]) -> List[Dict[str, Any]]:
    all_scenes: List[Dict[str, Any]] = []

    for file_path in json_files:
        data = load_json_file(file_path)
        scenes = extract_scenes(data, file_path.name)
        all_scenes.extend(scenes)

    def sort_key(scene: Dict[str, Any]) -> Tuple[str, str, int]:
        return (
            str(scene.get("_source_file", "")),
            str(scene.get("_source_tipo", "")),
            resolve_order(scene, 999999),
        )

    all_scenes.sort(key=sort_key)
    return all_scenes


def build_manifest_path(input_path: Path, output_dir: Path) -> Path:
    if input_path.is_file():
        return output_dir / f"manifest_{input_path.stem}.csv"
    return output_dir / "manifest_lote.csv"


def select_scenes(
    scenes: List[Dict[str, Any]],
    start_index: int,
    limit: Optional[int],
) -> Tuple[List[Dict[str, Any]], int]:
    if start_index < 1:
        raise ValueError("--start-index deve ser >= 1.")

    start_offset = start_index - 1
    if start_offset >= len(scenes):
        raise ValueError(
            f"--start-index={start_index} fora do intervalo. Total de cenas: {len(scenes)}."
        )

    selected_scenes = scenes[start_offset:]
    if limit is not None:
        if limit < 1:
            raise ValueError("--limit deve ser >= 1.")
        selected_scenes = selected_scenes[:limit]

    return selected_scenes, start_offset


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Gera imagens em lote a partir de JSON usando Gemini API."
    )
    parser.add_argument(
        "--input",
        required=True,
        help=(
            "Arquivo JSON unico ou pasta com varios JSONs. "
            f"Exemplo: {JSON_INPUT_DIR}"
        ),
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_DIR.relative_to(DEFAULT_OUTPUT_DIR.parents[2])),
        help=(
            "Pasta de saida relativa ao projeto ou caminho absoluto. "
            "Padrao: data/outputs/raw"
        ),
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help="Delay entre requisicoes em segundos.",
    )
    parser.add_argument(
        "--aspect-ratio",
        default=DEFAULT_ASPECT_RATIO,
        help="Ex.: 16:9, 9:16, 1:1.",
    )
    parser.add_argument(
        "--image-size",
        default=DEFAULT_IMAGE_SIZE,
        help="Ex.: 1K.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=DEFAULT_MAX_RETRIES,
        help="Numero maximo de tentativas para erros temporarios.",
    )
    parser.add_argument(
        "--start-index",
        type=int,
        default=DEFAULT_START_INDEX,
        help="Comeca da cena N, contando a partir de 1.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Processa no maximo N cenas a partir de --start-index.",
    )
    parser.add_argument(
        "--manifest-mode",
        choices=("append", "write"),
        default=DEFAULT_MANIFEST_MODE,
        help="append preserva o manifesto; write recria o arquivo.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "GEMINI_API_KEY nao encontrado. Defina a variavel de ambiente antes de rodar."
        )

    input_path = resolve_project_path(args.input)
    output_dir = resolve_project_path(args.output)
    ensure_dir(output_dir)

    manifest_path = build_manifest_path(input_path, output_dir)

    json_files = collect_json_files(input_path)
    if not json_files:
        raise FileNotFoundError("Nenhum arquivo JSON encontrado.")

    scenes = load_all_scenes(json_files)
    if not scenes:
        raise ValueError("Nenhuma cena encontrada nos JSONs.")

    selected_scenes, start_offset = select_scenes(
        scenes=scenes,
        start_index=args.start_index,
        limit=args.limit,
    )

    client = genai.Client(api_key=api_key)

    total_ok = 0
    total_skip = 0
    total_error = 0

    manifest_exists = manifest_path.exists()
    manifest_mode = "a" if args.manifest_mode == "append" else "w"

    with manifest_path.open(manifest_mode, newline="", encoding="utf-8-sig") as csvfile:
        fieldnames = [
            "source_file",
            "source_tipo",
            "ordem",
            "scene_id",
            "nome",
            "tempo",
            "tempo_inicio",
            "tempo_fim",
            "duracao_segundos",
            "arquivo",
            "status",
            "erro",
            "prompt",
        ]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

        if manifest_mode == "w" or not manifest_exists or csvfile.tell() == 0:
            writer.writeheader()
            csvfile.flush()

        print(
            f"Processando {len(selected_scenes)} de {len(scenes)} cenas "
            f"(inicio em {args.start_index})."
        )

        for batch_index, scene in enumerate(selected_scenes, start=1):
            global_index = start_offset + batch_index
            ordem = resolve_order(scene, global_index)
            prompt = resolve_prompt(scene)
            filename = build_output_filename(scene, ordem, global_index)
            output_path = output_dir / filename

            print(
                f"[{batch_index}/{len(selected_scenes)} | cena {global_index}/{len(scenes)}] "
                f"Gerando: {filename}"
            )

            if output_path.exists():
                print(f"  SKIP -> {output_path} ja existe")
                save_manifest_row(
                    writer=writer,
                    scene=scene,
                    ordem=ordem,
                    filename=filename,
                    prompt=prompt,
                    status="skip",
                )
                csvfile.flush()
                total_skip += 1
                continue

            try:
                generate_image_with_gemini(
                    client=client,
                    prompt=prompt,
                    output_path=output_path,
                    aspect_ratio=args.aspect_ratio,
                    image_size=args.image_size,
                    max_retries=args.max_retries,
                )
                save_manifest_row(
                    writer=writer,
                    scene=scene,
                    ordem=ordem,
                    filename=filename,
                    prompt=prompt,
                    status="ok",
                )
                csvfile.flush()
                total_ok += 1
                print(f"  OK -> {output_path}")

            except Exception as exc:
                error_text = str(exc)
                save_manifest_row(
                    writer=writer,
                    scene=scene,
                    ordem=ordem,
                    filename=filename,
                    prompt=prompt,
                    status="erro",
                    error=error_text,
                )
                csvfile.flush()
                total_error += 1
                print(f"  ERRO -> {error_text}")

                if is_quota_exhausted_error(error_text):
                    print("\nInterrompido: quota ou billing indisponivel para continuar.")
                    print(
                        "Verifique AI Studio > Billing e Usage, ou rode em lotes "
                        "menores com --start-index e --limit."
                    )
                    break

            time.sleep(args.delay)

    print(f"\nConcluido. Manifesto salvo em: {manifest_path}")
    print(f"Resumo -> OK: {total_ok} | SKIP: {total_skip} | ERRO: {total_error}")


if __name__ == "__main__":
    main()
