from http.server import BaseHTTPRequestHandler
import json
import urllib.parse

from api import stage3 as base


PRAYERS = {
    "serenity": (
        "🙏 <b>Молитва о душевном покое</b>\n\n"
        "Боже, дай мне разум и душевный покой принять то, что я не в силах изменить, "
        "му