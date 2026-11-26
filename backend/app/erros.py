"""Exceções do bot. Cada falha tem um tipo, para ser tratada (ou isolada) no lugar certo."""


class ErroBot(Exception):
    """Base de todos os erros do bot."""


class ErroConfiguracao(ErroBot):
    """Configuração obrigatória ausente ou inválida (ex.: falta SUPABASE_URL)."""


class IntegracaoIndisponivel(ErroBot):
    """A integração não tem credenciais ou foi desativada: o sistema usa DEMO ou pula."""


class ErroIntegracao(ErroBot):
    """Falha ao falar com um serviço externo.

    temporario=True: vale tentar de novo (timeout, 429, 5xx).
    temporario=False: tentar de novo não resolve (credencial errada, 4xx).
    incerto=True: a requisição pode ter chegado ao serviço (ex.: timeout depois de enviar um post).
        Repetir pode duplicar a publicação, então o item NÃO é tentado de novo automaticamente.
    autenticacao=True: token vencido, revogado ou sem permissão. O canal é pausado na rodada.
    limite=True: o serviço pediu para esperar (cota ou excesso de requisições). O item volta para a fila.
    """

    def __init__(self, mensagem: str, *, servico: str = "", temporario: bool = False, status: int | None = None,
                 incerto: bool = False, autenticacao: bool = False, limite: bool = False, esperar_min: int = 30):
        super().__init__(mensagem)
        self.servico = servico
        self.temporario = temporario
        self.status = status
        self.incerto = incerto
        self.autenticacao = autenticacao
        self.limite = limite
        self.esperar_min = esperar_min


class LinkInvalido(ErroBot):
    """O link de afiliado não passou na validação (domínio, HTTPS ou resposta)."""


class ErroBanco(ErroBot):
    """Erro ao falar com o banco (Supabase)."""

    def __init__(self, mensagem: str, *, codigo: str | None = None, status: int | None = None):
        super().__init__(mensagem)
        self.codigo = codigo
        self.status = status


# Códigos próprios do banco (ver docs/BANCO_DE_DADOS.md)
CODIGOS_REGRA = {
    "P0001": "dono protegido",
    "P0002": "registro imutável",
    "P0003": "configuração protegida",
    "P0010": "link de afiliado não validado",
    "P0011": "domínio não é de afiliado",
    "P0012": "link de outro produto",
    "P0013": "promoção não aprovada",
    "P0014": "canal inativo",
    "P0015": "oferta demo em canal real",
    "P0016": "validação de link é do sistema",
    "P0017": "publicação é do sistema",
    "P0018": "oferta já publicada neste canal na janela de repetição",
    "P0019": "oferta não aprovada pelo motor de inteligência",
    "P0020": "loja desativada",
    "P0021": "link precisa de https",
    "P0022": "produto já tem oferta ativa",
    "P0023": "pontuação é do sistema",
    "P0024": "link sem atribuição de afiliado",
}


class RegraViolada(ErroBanco):
    """O banco recusou a operação por uma regra de negócio (códigos P00xx)."""

    @property
    def descricao(self) -> str:
        return CODIGOS_REGRA.get(self.codigo or "", "regra do banco")


class EscritaBloqueada(ErroBot):
    """Modo de validação somente leitura: uma chamada que publicaria, enviaria ou alteraria algo
    num serviço externo foi barrada antes de sair do bot."""
