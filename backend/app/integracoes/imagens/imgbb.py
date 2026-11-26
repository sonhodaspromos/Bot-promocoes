"""ImgBB · hospedagem de imagens (API oficial: https://api.imgbb.com/). Imagens expiram em 1 dia."""
from __future__ import annotations

import base64

from ...erros import ErroIntegracao
from ...cliente_http import ClienteHTTP
from ..base import HospedagemImagem


class ImgBB(HospedagemImagem):
    def __init__(self, chave: str, http: ClienteHTTP | None = None, expira_seg: int = 86400):
        self.chave, self.expira = chave, expira_seg
        self.http = http or ClienteHTTP("imgbb")

    def enviar(self, imagem: bytes) -> str:
        dados = self.http.post("https://api.imgbb.com/1/upload", data={
            "key": self.chave, "image": base64.b64encode(imagem).decode(), "expiration": self.expira}).json()
        url = (dados.get("data") or {}).get("url")
        if not url or not url.startswith("https://"):
            raise ErroIntegracao("ImgBB não devolveu a URL da imagem", servico="imgbb")
        return url

    def testar(self) -> str:
        """Envia uma imagem de 1 pixel que expira em 60 s (só com autorização: é um envio real)."""
        import io
        from PIL import Image
        buf = io.BytesIO(); Image.new("RGB", (1, 1)).save(buf, "PNG")
        dados = self.http.post("https://api.imgbb.com/1/upload", data={
            "key": self.chave, "image": base64.b64encode(buf.getvalue()).decode(), "expiration": 60}).json()
        if not (dados.get("data") or {}).get("url", "").startswith("https://"):
            raise ErroIntegracao("ImgBB não devolveu a URL da imagem de teste", servico="imgbb")
        return "imgbb: REAL ✔ envio de teste aceito (imagem de 1 pixel, expira em 60 s)"
