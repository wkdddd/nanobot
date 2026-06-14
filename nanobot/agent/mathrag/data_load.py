"""Convert math knowledge files into Markdown for MathQA RAG.

The converter prefers text-native extraction, then falls back to OCR for
scanned pages/images, and optionally uses Pix2Tex to preserve formulas as
Markdown math spans.
"""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SOURCE_DIR = ".nanobot/math_knowledge"
MARKDOWN_DIR_NAME = "_markdown"

TEXT_SUFFIXES = {".md", ".markdown", ".txt"}
STRUCTURED_SUFFIXES = {".json", ".jsonl"}
PDF_SUFFIXES = {".pdf"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | STRUCTURED_SUFFIXES | PDF_SUFFIXES | IMAGE_SUFFIXES

_MIN_NATIVE_PAGE_CHARS = 80
_MAX_MARKDOWN_CHARS = 2_000_000


@dataclass(slots=True)
class PageConversion:
    page_number: int
    method: str
    text_chars: int = 0
    formulas: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FileConversion:
    source_path: Path
    markdown_path: Path | None
    markdown: str
    pages: list[PageConversion] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.markdown.strip()) and not self.markdown.lstrip().startswith("[error:")


class OptionalDependencyError(RuntimeError):
    """Raised when an optional OCR/PDF dependency is required but unavailable."""


@dataclass(slots=True)
class OCRBlock:
    box: Any
    text: str
    confidence: float = 0.0

    @property
    def top(self) -> float:
        return _box_top(self.box)


class MathKnowledgeMarkdownConverter:
    """Read files from the math knowledge directory and write Markdown outputs."""

    def __init__(
        self,
        workspace: Path,
        *,
        source_dir: str = SOURCE_DIR,
        output_dir_name: str = MARKDOWN_DIR_NAME,
        render_dpi: int = 220,
        min_native_page_chars: int = _MIN_NATIVE_PAGE_CHARS,
        enable_ocr: bool = True,
        enable_formula_ocr: bool = True,
    ) -> None:
        self.workspace = workspace.expanduser().resolve()
        self.source_dir = self.workspace / source_dir
        self.output_dir = self.source_dir / output_dir_name
        self.render_dpi = render_dpi
        self.min_native_page_chars = min_native_page_chars
        self.enable_ocr = enable_ocr
        self.enable_formula_ocr = enable_formula_ocr
        self._paddle_ocr: Any | None = None
        self._pix2tex_model: Any | None = None
        self._ocr_cache: dict[str, list[OCRBlock]] = {}

    def iter_source_files(self) -> list[Path]:
        if not self.source_dir.exists():
            return []
        return sorted(
            p for p in self.source_dir.rglob("*")
            if (
                p.is_file()
                and p.suffix.lower() in SUPPORTED_SUFFIXES
                and MARKDOWN_DIR_NAME not in p.relative_to(self.source_dir).parts
            )
        )

    def convert_all(self, *, write: bool = True) -> list[FileConversion]:
        return [self.convert_file(path, write=write) for path in self.iter_source_files()]

    def convert_file(self, path: Path, *, write: bool = True) -> FileConversion:
        path = path.expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(str(path))
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise ValueError(f"Unsupported math knowledge file type: {suffix}")

        if suffix in TEXT_SUFFIXES:
            result = self._convert_text(path)
        elif suffix in STRUCTURED_SUFFIXES:
            result = self._convert_structured(path)
        elif suffix in PDF_SUFFIXES:
            result = self._convert_pdf(path)
        else:
            result = self._convert_image(path)

        markdown = _normalize_markdown(result.markdown)
        if len(markdown) > _MAX_MARKDOWN_CHARS:
            markdown = markdown[:_MAX_MARKDOWN_CHARS].rstrip() + "\n\n<!-- truncated -->\n"
            result.warnings.append("Markdown was truncated because it exceeded the safety limit.")
        result.markdown = markdown

        if write:
            out = self._output_path_for(path)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(markdown, encoding="utf-8")
            result.markdown_path = out
        return result

    def _output_path_for(self, path: Path) -> Path:
        try:
            rel = path.relative_to(self.source_dir)
        except ValueError:
            rel = Path(path.name)
        return self.output_dir / rel.with_suffix(".md")

    def _convert_text(self, path: Path) -> FileConversion:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return FileConversion(
                source_path=path,
                markdown_path=None,
                markdown=f"[error: {path.name} is not UTF-8 encoded]",
                warnings=["Only UTF-8 math knowledge files are supported."],
            )
        heading = f"# {path.stem}\n\n" if not _starts_with_heading(text) else ""
        return FileConversion(path, None, heading + text)

    def _convert_structured(self, path: Path) -> FileConversion:
        try:
            raw = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return FileConversion(
                source_path=path,
                markdown_path=None,
                markdown=f"[error: {path.name} is not UTF-8 encoded]",
                warnings=["Only UTF-8 math knowledge files are supported."],
            )

        rows: list[Any]
        if path.suffix.lower() == ".jsonl":
            rows = []
            for line in raw.splitlines():
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        else:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                return FileConversion(
                    source_path=path,
                    markdown_path=None,
                    markdown=f"[error: failed to parse JSON: {exc}]",
                )
            rows = data if isinstance(data, list) else [data]

        blocks = [f"# {path.stem}"]
        for i, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or row.get("question") or f"条目 {i}")
            content = row.get("content") or row.get("text") or row.get("body") or ""
            subject = row.get("subject") or ""
            chapter = row.get("chapter") or ""
            tags = row.get("tags") or row.get("knowledge_tags") or []
            problem_types = row.get("problem_types") or row.get("types") or []
            meta = []
            if subject:
                meta.append(f"- 科目：{subject}")
            if chapter:
                meta.append(f"- 章节：{chapter}")
            if tags:
                meta.append(f"- 标签：{', '.join(str(t) for t in tags)}")
            if problem_types:
                meta.append(f"- 题型：{', '.join(str(t) for t in problem_types)}")
            blocks.append(f"## {title}\n\n" + "\n".join(meta + ["", str(content).strip()]).strip())
        return FileConversion(path, None, "\n\n".join(b for b in blocks if b.strip()))

    def _convert_pdf(self, path: Path) -> FileConversion:
        warnings: list[str] = []
        native_markdown = ""
        pages: list[PageConversion] = []

        try:
            native_markdown = self._pdf_to_markdown_with_pymupdf4llm(path)
        except OptionalDependencyError as exc:
            warnings.append(str(exc))
        except Exception as exc:
            warnings.append(f"PyMuPDF4LLM extraction failed: {exc}")

        native_pages = _split_pdf_markdown_pages(native_markdown)
        scanned_pages = [
            i for i, text in enumerate(native_pages, 1)
            if len(_visible_text(text)) < self.min_native_page_chars
        ]

        if native_pages:
            for i, text in enumerate(native_pages, 1):
                method = "pymupdf4llm"
                page_warnings: list[str] = []
                if i in scanned_pages:
                    method = "pymupdf4llm+ocr-needed"
                    page_warnings.append("Native PDF text is sparse; page treated as scanned.")
                pages.append(PageConversion(i, method, len(_visible_text(text)), warnings=page_warnings))

        ocr_pages: dict[int, str] = {}
        formula_pages: dict[int, list[str]] = {}
        if scanned_pages and self.enable_ocr:
            try:
                ocr_pages = self._ocr_pdf_pages(path, scanned_pages)
            except OptionalDependencyError as exc:
                warnings.append(str(exc))
            except Exception as exc:
                warnings.append(f"PaddleOCR scan fallback failed: {exc}")

        if scanned_pages and self.enable_formula_ocr:
            try:
                formula_pages = self._formula_ocr_pdf_pages(path, scanned_pages)
            except OptionalDependencyError as exc:
                warnings.append(str(exc))
            except Exception as exc:
                warnings.append(f"Pix2Tex formula recognition failed: {exc}")

        if native_pages:
            merged: list[str] = [f"# {path.stem}"]
            for i, page_md in enumerate(native_pages, 1):
                parts = [f"## Page {i}"]
                if i in ocr_pages and len(_visible_text(ocr_pages[i])) > len(_visible_text(page_md)):
                    parts.append(ocr_pages[i])
                    _mark_page_method(pages, i, "paddleocr")
                else:
                    parts.append(page_md.strip())
                formulas = formula_pages.get(i) or []
                if formulas:
                    _mark_page_formulas(pages, i, formulas)
                    parts.append(_format_formulas(formulas))
                merged.append("\n\n".join(p for p in parts if p.strip()))
            markdown = "\n\n".join(merged)
        else:
            markdown = f"# {path.stem}"
            if self.enable_ocr:
                try:
                    all_pages = self._pdf_page_numbers(path)
                    ocr_pages = self._ocr_pdf_pages(path, all_pages)
                    formula_pages = self._formula_ocr_pdf_pages(path, all_pages) if self.enable_formula_ocr else {}
                    blocks = [markdown]
                    for i in all_pages:
                        formulas = formula_pages.get(i) or []
                        pages.append(PageConversion(
                            i,
                            "paddleocr",
                            len(_visible_text(ocr_pages.get(i, ""))),
                            formulas=formulas,
                        ))
                        blocks.append(
                            "\n\n".join(
                                p for p in [
                                    f"## Page {i}",
                                    ocr_pages.get(i, ""),
                                    _format_formulas(formulas) if formulas else "",
                                ]
                                if p.strip()
                            )
                        )
                    markdown = "\n\n".join(blocks)
                except OptionalDependencyError as exc:
                    warnings.append(str(exc))
                except Exception as exc:
                    warnings.append(f"PDF OCR conversion failed: {exc}")

        if warnings:
            markdown += "\n\n<!-- conversion warnings:\n" + "\n".join(f"- {w}" for w in warnings) + "\n-->\n"
        return FileConversion(path, None, markdown, pages=pages, warnings=warnings)

    def _convert_image(self, path: Path) -> FileConversion:
        warnings: list[str] = []
        blocks = [f"# {path.stem}"]
        try:
            text = self._ocr_image(path)
            if text:
                blocks.append(text)
        except OptionalDependencyError as exc:
            warnings.append(str(exc))
        except Exception as exc:
            warnings.append(f"PaddleOCR image conversion failed: {exc}")

        if self.enable_formula_ocr:
            try:
                formulas = self._formula_ocr_image(path)
                if formulas:
                    blocks.append(_format_formulas(formulas))
            except OptionalDependencyError as exc:
                warnings.append(str(exc))
            except Exception as exc:
                warnings.append(f"Pix2Tex formula recognition failed: {exc}")

        if warnings:
            blocks.append("<!-- conversion warnings:\n" + "\n".join(f"- {w}" for w in warnings) + "\n-->")
        return FileConversion(path, None, "\n\n".join(blocks), warnings=warnings)

    @staticmethod
    def _pdf_to_markdown_with_pymupdf4llm(path: Path) -> str:
        try:
            import pymupdf4llm
        except ImportError as exc:
            raise OptionalDependencyError(
                "PyMuPDF4LLM is not installed. Install optional math RAG PDF dependencies."
            ) from exc

        try:
            markdown = pymupdf4llm.to_markdown(str(path), page_chunks=True)
        except TypeError:
            markdown = pymupdf4llm.to_markdown(str(path))

        if isinstance(markdown, list):
            blocks: list[str] = []
            for i, item in enumerate(markdown, 1):
                if isinstance(item, dict):
                    text = item.get("text") or item.get("markdown") or ""
                    page = item.get("page") or i
                    blocks.append(f"## Page {page}\n\n{text}")
                else:
                    blocks.append(f"## Page {i}\n\n{item}")
            return "\n\n".join(blocks)
        return str(markdown or "")

    def _ocr_pdf_pages(self, path: Path, page_numbers: list[int]) -> dict[int, str]:
        images = self._render_pdf_pages(path, page_numbers)
        return {page: self._ocr_image(image) for page, image in images.items()}

    def _formula_ocr_pdf_pages(self, path: Path, page_numbers: list[int]) -> dict[int, list[str]]:
        images = self._render_pdf_pages(path, page_numbers)
        return {page: self._formula_ocr_image(image) for page, image in images.items()}

    def _pdf_page_numbers(self, path: Path) -> list[int]:
        try:
            import fitz
        except ImportError as exc:
            raise OptionalDependencyError("PyMuPDF is required to render scanned PDF pages.") from exc
        with fitz.open(str(path)) as doc:
            return list(range(1, len(doc) + 1))

    def _render_pdf_pages(self, path: Path, page_numbers: list[int]) -> dict[int, Path]:
        try:
            import fitz
        except ImportError as exc:
            raise OptionalDependencyError("PyMuPDF is required to render scanned PDF pages.") from exc

        rendered: dict[int, Path] = {}
        temp_dir = Path(tempfile.mkdtemp(prefix="nanobot-mathrag-"))
        with fitz.open(str(path)) as doc:
            zoom = self.render_dpi / 72
            matrix = fitz.Matrix(zoom, zoom)
            for page_number in page_numbers:
                if page_number < 1 or page_number > len(doc):
                    continue
                page = doc[page_number - 1]
                pix = page.get_pixmap(matrix=matrix, alpha=False)
                out = temp_dir / f"{path.stem}-page-{page_number}.png"
                pix.save(str(out))
                rendered[page_number] = out
        return rendered

    def _get_paddle_ocr(self) -> Any:
        if self._paddle_ocr is not None:
            return self._paddle_ocr
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise OptionalDependencyError(
                "PaddleOCR is not installed. Scanned PDFs/images need PaddleOCR."
            ) from exc
        self._paddle_ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
        return self._paddle_ocr

    def _ocr_image(self, path: Path) -> str:
        blocks = self._ocr_blocks(path)
        return "\n".join(block.text for block in sorted(blocks, key=lambda item: item.top)).strip()

    def _ocr_blocks(self, path: Path) -> list[OCRBlock]:
        key = str(path.resolve())
        if key in self._ocr_cache:
            return self._ocr_cache[key]
        ocr = self._get_paddle_ocr()
        result = ocr.ocr(str(path), cls=True)
        blocks: list[OCRBlock] = []
        for page in result or []:
            for item in page or []:
                if not item or len(item) < 2:
                    continue
                box, text_info = item[0], item[1]
                text = text_info[0] if isinstance(text_info, (list, tuple)) and text_info else ""
                confidence = (
                    float(text_info[1])
                    if isinstance(text_info, (list, tuple)) and len(text_info) > 1
                    else 0.0
                )
                if not text:
                    continue
                blocks.append(OCRBlock(box=box, text=str(text), confidence=confidence))
        blocks.sort(key=lambda item: item.top)
        self._ocr_cache[key] = blocks
        return blocks

    def _get_pix2tex_model(self) -> Any:
        if self._pix2tex_model is not None:
            return self._pix2tex_model
        try:
            from pix2tex.cli import LatexOCR
        except ImportError as exc:
            raise OptionalDependencyError(
                "Pix2Tex is not installed. Formula recognition needs pix2tex."
            ) from exc
        self._pix2tex_model = LatexOCR()
        return self._pix2tex_model

    def _formula_ocr_image(self, path: Path) -> list[str]:
        model = self._get_pix2tex_model()
        try:
            from PIL import Image
        except ImportError as exc:
            raise OptionalDependencyError("Pillow is required by Pix2Tex image loading.") from exc
        image = Image.open(path).convert("RGB")
        candidates = _formula_candidate_boxes(self._ocr_blocks(path))
        formulas: list[str] = []
        for box in candidates[:12]:
            crop = _crop_image_to_box(image, box, padding=10)
            latex = _clean_latex(str(model(crop) or "").strip())
            if latex and latex not in formulas:
                formulas.append(latex)

        if formulas:
            return formulas

        latex = _clean_latex(str(model(image) or "").strip())
        return [latex] if latex else []


def convert_math_knowledge_to_markdown(
    workspace: Path,
    *,
    write: bool = True,
    enable_ocr: bool = True,
    enable_formula_ocr: bool = True,
) -> list[FileConversion]:
    converter = MathKnowledgeMarkdownConverter(
        workspace,
        enable_ocr=enable_ocr,
        enable_formula_ocr=enable_formula_ocr,
    )
    return converter.convert_all(write=write)


def _starts_with_heading(text: str) -> bool:
    return bool(re.match(r"^\s{0,3}#{1,6}\s+", text))


def _normalize_markdown(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip() + "\n" if text.strip() else ""


def _visible_text(text: str) -> str:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"[#*_`|>\-\s]", "", text)
    return text.strip()


def _split_pdf_markdown_pages(markdown: str) -> list[str]:
    if not markdown.strip():
        return []
    matches = list(re.finditer(r"(?m)^#{1,3}\s+Page\s+\d+\s*$", markdown))
    if not matches:
        return [markdown]
    pages: list[str] = []
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        pages.append(markdown[start:end].strip())
    return pages


def _format_formulas(formulas: list[str]) -> str:
    blocks = ["### 公式识别"]
    for formula in formulas:
        formula = _clean_latex(formula)
        if not formula:
            continue
        if len(formula) > 48 or "\\" in formula:
            blocks.append(f"$$\n{formula}\n$$")
        else:
            blocks.append(f"${formula}$")
    return "\n\n".join(blocks)


def _clean_latex(latex: str) -> str:
    latex = latex.strip()
    latex = latex.removeprefix("$").removesuffix("$").strip()
    latex = latex.removeprefix(r"\[").removesuffix(r"\]").strip()
    return latex


def _box_top(box: Any) -> float:
    try:
        return min(float(point[1]) for point in box)
    except Exception:
        return 0.0


def _formula_candidate_boxes(blocks: list[OCRBlock]) -> list[Any]:
    candidates: list[Any] = []
    for block in blocks:
        text = block.text.strip()
        if not text:
            continue
        if _looks_like_formula(text):
            candidates.append(block.box)
    return candidates


def _looks_like_formula(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return False
    math_marks = set("=+-*/^_∫∑Σ√∞≤≥≠≈→←↔∂∆πθλμσφΩαβγ")
    mark_count = sum(1 for ch in compact if ch in math_marks)
    digit_count = sum(1 for ch in compact if ch.isdigit())
    latin_count = sum(1 for ch in compact if ch.isascii() and ch.isalpha())
    cjk_count = sum(1 for ch in compact if "\u4e00" <= ch <= "\u9fff")
    if mark_count >= 1 and cjk_count <= max(2, len(compact) // 3):
        return True
    if digit_count >= 2 and latin_count >= 1 and any(ch in compact for ch in "()[]{}"):
        return True
    return "\\" in compact


def _crop_image_to_box(image: Any, box: Any, *, padding: int = 0) -> Any:
    try:
        xs = [float(point[0]) for point in box]
        ys = [float(point[1]) for point in box]
    except Exception:
        return image
    left = max(0, int(min(xs)) - padding)
    top = max(0, int(min(ys)) - padding)
    right = min(image.width, int(max(xs)) + padding)
    bottom = min(image.height, int(max(ys)) + padding)
    if right <= left or bottom <= top:
        return image
    return image.crop((left, top, right, bottom))


def _mark_page_method(pages: list[PageConversion], page_number: int, method: str) -> None:
    for page in pages:
        if page.page_number == page_number:
            page.method = method
            return
    pages.append(PageConversion(page_number, method))


def _mark_page_formulas(
    pages: list[PageConversion], page_number: int, formulas: list[str]
) -> None:
    for page in pages:
        if page.page_number == page_number:
            page.formulas = formulas
            return
    pages.append(PageConversion(page_number, "formula-ocr", formulas=formulas))
