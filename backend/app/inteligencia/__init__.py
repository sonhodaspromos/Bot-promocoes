"""Motor de inteligência de ofertas: regras, pontuação (0–100), classificação e explicação."""
from .motor import (BOA, EXCELENTE, NAO_RECOMENDADA, Componente, EntradaAnalise, PontoPreco, ResultadoAnalise,
                    analisar)
from .regras import Regras

__all__ = ["analisar", "Regras", "EntradaAnalise", "PontoPreco", "ResultadoAnalise", "Componente",
           "EXCELENTE", "BOA", "NAO_RECOMENDADA"]
