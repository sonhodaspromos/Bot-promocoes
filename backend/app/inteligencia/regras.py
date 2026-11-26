"""Regras configuráveis do motor (tabela `configuracoes`, grupo "inteligencia" + filtros.desconto_minimo)."""
from __future__ import annotations

from dataclasses import dataclass, field


def _num(cfg: dict, chave: str, padrao: float) -> float:
    try:
        v = cfg.get(chave)
        return padrao if v is None else float(v)
    except (TypeError, ValueError):
        return padrao


@dataclass(frozen=True)
class Regras:
    desconto_minimo: float = 20.0
    desconto_meta: float = 60.0
    desconto_maximo_plausivel: float = 90.0
    comissao_meta: float = 10.0
    comissao_maxima_plausivel: float = 50.0
    peso_desconto: float = 35.0
    peso_comissao: float = 20.0
    peso_popularidade: float = 15.0
    peso_relevancia: float = 15.0
    peso_qualidade: float = 15.0
    nota_excelente: float = 75.0
    nota_boa: float = 50.0
    confianca_minima_excelente: float = 60.0
    historico_minimo_registros: int = 3
    historico_dias: int = 90
    historico_cobertura_dias: int = 30      # cobertura mínima para contestar o "preço de" da loja
    tolerancia_inflacao_pct: float = 5.0
    faixa_preco_min: float = 10.0
    faixa_preco_max: float = 1500.0
    exigir_imagem: bool = False
    janela_repeticao_horas: float = 24.0
    tolerancia_preco_produto_pct: float = 1.0   # diferença aceita entre preço da oferta e do produto
    exigir_desconto_comprovado: bool = False     # produção: só desconto confirmado por histórico próprio ou cupom
    variacao_preco_max_pct: float = 5.0          # diferença máxima aceita entre o menor e o maior preço das variações
    bloquear_desconto_artificial: bool = True    # "sobe e desce" detectado com histórico suficiente = bloqueio
    historico_cobertura_minima_pct: float = 80.0 # % da janela observada para tirar conclusões
    alta_artificial_pct: float = 15.0            # alta recente acima do típico de 90 dias que caracteriza "sobe e desce"

    @property
    def pesos(self) -> dict[str, float]:
        return {"desconto": self.peso_desconto, "comissao": self.peso_comissao,
                "popularidade": self.peso_popularidade, "relevancia": self.peso_relevancia,
                "qualidade": self.peso_qualidade}

    @classmethod
    def de_config(cls, cfg: dict) -> "Regras":
        p = "inteligencia."
        exigir = cfg.get(p + "exigir_imagem")
        r = cls(
            desconto_minimo=_num(cfg, "filtros.desconto_minimo", 20),
            desconto_meta=_num(cfg, p + "desconto_meta", 60),
            desconto_maximo_plausivel=_num(cfg, p + "desconto_maximo_plausivel", 90),
            comissao_meta=_num(cfg, p + "comissao_meta", 10),
            comissao_maxima_plausivel=_num(cfg, p + "comissao_maxima_plausivel", 50),
            peso_desconto=_num(cfg, p + "peso_desconto", 35),
            peso_comissao=_num(cfg, p + "peso_comissao", 20),
            peso_popularidade=_num(cfg, p + "peso_popularidade", 15),
            peso_relevancia=_num(cfg, p + "peso_relevancia", 15),
            peso_qualidade=_num(cfg, p + "peso_qualidade", 15),
            nota_excelente=_num(cfg, p + "nota_excelente", 75),
            nota_boa=_num(cfg, p + "nota_boa", 50),
            confianca_minima_excelente=_num(cfg, p + "confianca_minima_excelente", 60),
            historico_minimo_registros=int(_num(cfg, p + "historico_minimo_registros", 3)),
            historico_dias=int(_num(cfg, p + "historico_dias", 90)),
            historico_cobertura_dias=int(_num(cfg, p + "historico_cobertura_dias", 30)),
            tolerancia_inflacao_pct=_num(cfg, p + "tolerancia_inflacao_pct", 5),
            faixa_preco_min=_num(cfg, p + "faixa_preco_min", 10),
            faixa_preco_max=_num(cfg, p + "faixa_preco_max", 1500),
            exigir_imagem=bool(exigir) if exigir is not None else False,
            janela_repeticao_horas=_num(cfg, p + "janela_repeticao_horas", 24),
            exigir_desconto_comprovado=bool(cfg.get(p + "exigir_desconto_comprovado") or False),
            variacao_preco_max_pct=_num(cfg, p + "variacao_preco_max_pct", 5),
            bloquear_desconto_artificial=cfg.get(p + "bloquear_desconto_artificial") is not False,
            historico_cobertura_minima_pct=_num(cfg, p + "historico_cobertura_minima_pct", 80),
            alta_artificial_pct=_num(cfg, p + "alta_artificial_pct", 15),
        )
        r.validar()
        return r

    def validar(self) -> None:
        if sum(self.pesos.values()) <= 0:
            raise ValueError("A soma dos pesos do motor precisa ser maior que zero.")
        if self.nota_boa > self.nota_excelente:
            raise ValueError("A nota de 'Boa' não pode ser maior que a de 'Excelente'.")
        if self.desconto_minimo >= self.desconto_maximo_plausivel:
            raise ValueError("O desconto mínimo precisa ser menor que o desconto máximo plausível.")
