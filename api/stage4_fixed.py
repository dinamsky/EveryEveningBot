import importlib
from api import stage3 as base
from api import stage4 as ext
importlib.reload(base)
_original_menu = base.menu

def menu(uid):
    result = _original_menu(uid)
    result['keyboard'].insert(2, [{'text': '🙏 Молитвы'}, {'text': '📘 Большая книга'}])
    return result

base.menu = menu
ext.base = base
handler = ext.handler
