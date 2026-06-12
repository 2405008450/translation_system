"""纯文本文件解析"""

import os
from .base import BaseParser, Document, Paragraph


class TxtParser(BaseParser):

    @staticmethod
    def supported_extensions():
        return [".txt"]

    def parse(self, file_path: str) -> Document:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        paragraphs = []
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped:
                paragraphs.append(Paragraph(text=stripped))

        return Document(
            paragraphs=paragraphs,
            filename=os.path.basename(file_path),
            raw_text=content,
        )
