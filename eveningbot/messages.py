"""Bound Telegram messages without breaking HTML or non-BMP characters."""
from html.parser import HTMLParser

LIMIT = 3500  # Conservative UTF-16-unit budget, below Telegram's 4096 limit.


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def chunks(text: str, limit: int = LIMIT) -> list[str]:
    if limit < 2:
        raise ValueError('limit must be at least 2')
    result, part, units = [], [], 0
    for ch in text:
        size = 2 if ord(ch) > 0xffff else 1
        if part and units + size > limit:
            result.append(''.join(part))
            part, units = [], 0
        part.append(ch)
        units += size
    if part:
        result.append(''.join(part))
    return result or ['—']


def message_parts(markup: str) -> list[dict]:
    if len(markup.encode('utf-16-le')) // 2 <= LIMIT:
        return [{'text': markup or '—', 'parse_mode': 'HTML'}]
    # Long reports use plain text; no cut HTML tags/entities and no lost answer text.
    parser = PlainText()
    parser.feed(markup)
    parser.close()
    return [{'text': text} for text in chunks(''.join(parser.parts))]
