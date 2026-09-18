"""PyMuPDF adapter: block-ordered text per page and PNG rendering for the review screen.

The only module that imports pymupdf; the `PdfTextExtractor` port keeps it replaceable.
"""

from pathlib import Path

import pymupdf

from ari.content.ports import PdfText


class PyMuPdfExtractor:
    name = f"pymupdf-{pymupdf.version[0]}"

    def extract(self, path: Path) -> PdfText:
        with pymupdf.open(path) as document:
            pages = tuple(
                # "blocks" keeps reading order on multi-column exports better than raw text.
                "\n".join(block[4].strip() for block in page.get_text("blocks") if block[4].strip())
                for page in document
            )
        return PdfText(page_count=len(pages), pages=pages, extractor=self.name)

    def render_page(self, path: Path, page_number: int, *, dpi: int = 110) -> bytes:
        with pymupdf.open(path) as document:
            if not 1 <= page_number <= document.page_count:
                raise ValueError("Page hors du document")
            pixmap = document[page_number - 1].get_pixmap(dpi=dpi)
            return bytes(pixmap.tobytes("png"))
