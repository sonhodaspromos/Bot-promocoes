"""Configuração do backend.

Ordem de leitura das credenciais (a primeira que existir vence):
  1. variáveis de ambiente (GitHub Secrets em produção)
  2. arquivo .env local (só para desenvolvimento; está no .gitignore)
  3. Supabase Vault, via função obter_credencial() (cadastradas pelo painel)

Cada integração fica em modo REAL (tem credenciais) ou DEMO (não tem). Em DEMO nada é
publicado de verdade e os dados são marcados como demonstração no banco.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .erros import ErroConfiguracao

REAL = "real"
DEMO = "demo"

# integração -> credenciais necessárias (nome da variável de ambiente, nome no Vault)
CREDENCIAIS = {
    "shopee": [("SHOPEE_APP_ID", "APP_ID"), ("SHOPEE_SECRET", "SECRET")],
    "telegram": [("TELEGRAM_TOKEN", "TOKEN"), ("TELEGRAM_CANAL", "CANAL")],
    "instagram": [("IG_TOKEN", "TOKEN"), ("IG_USER_ID", "USER_ID")],
    "imgbb": [("IMGBB_KEY", "KEY")],
    # expansão modular (só valem com a flag recursos.* ligada e validação real aprovada)
    "aliexpress": [("ALIEXPRESS_APP_KEY", "APP_KEY"), ("ALIEXPRESS_APP_SECRET", "APP_SECRET"),
                   ("ALIEXPRESS_TRACKING_ID", "TRACKING_ID")],
    "amazon": [("AMAZON_CREDENTIAL_ID", "CREDENTIAL_ID"), ("AMAZON_CREDENTIAL_SECRET", "CREDENTIAL_SECRET"),
               ("AMAZON_PARTNER_TAG", "PARTNER_TAG")],   # AMAZON_TOKEN_URL/AMAZON_API_URL: opcionais (ver fontes/amazon.py)
    "whatsapp": [("WHATSAPP_TOKEN", "TOKEN"), ("WHATSAPP_PHONE_ID", "PHONE_ID"), ("WHATSAPP_TEMPLATE", "TEMPLATE")],
    # motor de IA (textos): só é usado com recursos.ia ligado
    "anthropic": [("ANTHROPIC_API_KEY", "API_KEY")],
}


def carregar_env(caminho: str | Path | None = None) -> None:
    """Carrega o .env local, se existir (não sobrescreve variáveis já definidas)."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv é opcional em produção
        return
    load_dotenv(caminho or Path(__file__).resolve().parents[2] / ".env", override=False)


@dataclass
class Config:
    supabase_url: str
    supabase_chave: str
    credenciais: dict[str, dict[str, str]] = field(default_factory=dict)
    forcar_demo: bool = False
    timeout_http: float = 20.0

    # ------------------------------------------------------------------ modos
    def modo(self, integracao: str) -> str:
        """REAL se todas as credenciais da integração existem (e o DEMO não foi forçado)."""
        if self.forcar_demo:
            return DEMO
        necessarias = [vault for _, vault in CREDENCIAIS.get(integracao, [])]
        tem = self.credenciais.get(integracao, {})
        return REAL if necessarias and all(tem.get(n) for n in necessarias) else DEMO

    def credencial(self, integracao: str, nome: str) -> str:
        valor = self.credenciais.get(integracao, {}).get(nome, "")
        if not valor:
            raise ErroConfiguracao(f"Credencial {integracao}.{nome} não configurada.")
        return valor

    @property
    def segredos(self) -> list[str]:
        """Todos os valores secretos (usados pelo log para mascarar)."""
        valores = [self.supabase_chave]
        for grupo in self.credenciais.values():
            valores += [v for v in grupo.values() if v]
        return [v for v in valores if v and len(v) >= 6]

    def resumo_seguro(self) -> dict:
        """Resumo para log: nunca contém valores secretos."""
        return {
            "supabase": self.supabase_url,
            "forcar_demo": self.forcar_demo,
            "integracoes": {nome: self.modo(nome) for nome in CREDENCIAIS},
        }


def ler_config(
    ambiente: dict[str, str] | None = None,
    buscar_no_cofre: Callable[[str, str], str | None] | None = None,
) -> Config:
    """Monta a Config. `buscar_no_cofre(integracao, nome)` lê do Vault quando a env não tem."""
    env = os.environ if ambiente is None else ambiente
    url = (env.get("SUPABASE_URL") or "").strip().rstrip("/")
    chave = (env.get("SUPABASE_SERVICE_KEY") or "").strip()
    faltando = [n for n, v in (("SUPABASE_URL", url), ("SUPABASE_SERVICE_KEY", chave)) if not v]
    if faltando:
        raise ErroConfiguracao("Faltam as variáveis obrigatórias: " + ", ".join(faltando))
    if not url.startswith("https://"):
        raise ErroConfiguracao("SUPABASE_URL precisa começar com https://")
    if chave.startswith("sb_publishable_"):
        raise ErroConfiguracao("SUPABASE_SERVICE_KEY é a chave PÚBLICA. Use a chave secreta (sb_secret_...).")

    credenciais: dict[str, dict[str, str]] = {}
    for integracao, itens in CREDENCIAIS.items():
        grupo = {}
        for var_env, nome_vault in itens:
            valor = (env.get(var_env) or "").strip()
            if not valor and buscar_no_cofre:
                valor = (buscar_no_cofre(integracao, nome_vault) or "").strip()
            grupo[nome_vault] = valor
        credenciais[integracao] = grupo

    return Config(
        supabase_url=url,
        supabase_chave=chave,
        credenciais=credenciais,
        forcar_demo=(env.get("MODO_DEMO") or "").strip().lower() in ("1", "true", "sim", "yes"),
        timeout_http=float(env.get("TIMEOUT_HTTP") or 20),
    )
