"""Canal de DEMONSTRAÇÃO: não publica nada. Só registra o que seria publicado."""
from __future__ import annotations

from ...modelos import ConteudoPublicacao
from ..base import DEMO, CanalPublicacao


class DemoCanal(CanalPublicacao):
    modo = DEMO

    def __init__(self, codigo: str):
        self.codigo = codigo
        self.publicados: list[ConteudoPublicacao] = []
        self.encerrados: list[tuple[str, str]] = []

    def publicar(self, conteudo):
        self.publicados.append(conteudo)
        return f"demo-{self.codigo}-{len(self.publicados)}"

    def encerrar(self, id_mensagem, titulo, motivo):
        self.encerrados.append((id_mensagem, motivo))

    def testar(self):
        return f"{self.codigo}: MODO DEMONSTRAÇÃO (nada é publicado de verdade)"
