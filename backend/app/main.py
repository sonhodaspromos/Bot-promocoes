"""Linha de comando do bot.

  python -m app.main rodar                # rodada completa (usada pelo GitHub Actions)
  python -m app.main simular              # rodada completa em memória: sem banco, sem internet, modo DEMO
  python -m app.main testar-integracoes   # mostra o modo de cada integração e testa as reais
  python -m app.main comissoes            # importa comissões agora
  python -m app.main arte-exemplo         # gera artes de exemplo na pasta saida/
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import VERSAO
from .cliente_http import ClienteHTTP
from .config import carregar_env, ler_config
from .erros import ErroBot, ErroConfiguracao
from .integracoes import registro
from .integracoes.fontes.demo import DemoFonte
from .integracoes.canais.demo import DemoCanal
from .logs import Log
from .orquestrador import Orquestrador
from .repositorio import SupabaseRepositorio
from .repositorio_memoria import MemoriaRepositorio
from .servicos import arte

SAIDA = Path(__file__).resolve().parents[1] / "saida"


def _montar_real(log: Log):
    carregar_env()
    base = ler_config()  # valida SUPABASE_URL e a chave antes de qualquer coisa
    repo = SupabaseRepositorio(base.supabase_url, base.supabase_chave, ClienteHTTP("supabase", timeout=base.timeout_http))
    config = ler_config(buscar_no_cofre=repo.obter_credencial)
    log.adicionar_segredos(config.segredos)
    cfg = repo.configuracoes()
    integ = registro.montar(config, ordenacao_shopee=int(cfg.get("busca.ordenacao_shopee") or 2))
    return config, repo, integ


def cmd_rodar(args, log: Log) -> int:
    config, repo, integ = _montar_real(log)
    log.info("bot", f"Bot de Ofertas v{VERSAO}", **config.resumo_seguro())
    status, resumo = Orquestrador(repo, integ, log, url_supabase=config.supabase_url).executar(
        origem=args.origem, forcar_comissoes=args.comissoes)
    # saída 1 = o GitHub marca a execução como falha e manda e-mail para o dono do repositório
    return 1 if status == "erro" or resumo.alertas_criticos else 0


def cmd_simular(args, log: Log) -> int:
    repo = MemoriaRepositorio()
    integ = registro.Integracoes(fontes={"demo": DemoFonte()},
                                 canais={c: DemoCanal(c) for c in ("telegram", "instagram_story", "instagram_feed")})
    orq = Orquestrador(repo, integ, log, url_supabase="https://exemplo.supabase.co")
    for _ in range(2):  # 1ª rodada busca e valida; 2ª publica respeitando o intervalo
        status, resumo = orq.executar(origem="teste")
    SAIDA.mkdir(exist_ok=True)
    for codigo, canal in integ.canais.items():
        for i, c in enumerate(canal.publicados[:2], 1):
            (SAIDA / f"simulacao_{codigo}_{i}.jpg").write_bytes(c.imagem_story or c.imagem)
    print(f"\nSIMULAÇÃO (modo DEMO, nada foi publicado): {len(repo.t['promocoes'])} promoções, "
          f"{len(repo.t['historico'])} publicações simuladas. Artes em {SAIDA}/")
    return 0 if status != "erro" else 1


def cmd_testar(args, log: Log) -> int:
    config, repo, integ = _montar_real(log)
    print(f"Banco: {config.supabase_url} ✔ ({len(repo.marketplaces())} marketplaces, {len(repo.canais())} canais)")
    falhou = False
    for item in list(integ.fontes.values()) + list(integ.canais.values()):
        try:
            print("  " + item.testar())
        except ErroBot as e:
            falhou = True
            print(f"  {item.codigo}: ✖ {e}")
    return 1 if falhou else 0


def cmd_comissoes(args, log: Log) -> int:
    config, repo, integ = _montar_real(log)
    from .servicos.comissoes import ServicoComissoes
    n = ServicoComissoes(repo, integ.fontes, log).executar(repo.configuracoes(), forcar=True, dias=args.dias)
    print(f"{n} itens de comissão processados")
    return 0


def cmd_arte(args, log: Log) -> int:
    cfg = MemoriaRepositorio().configuracoes()
    dados = {"titulo": "[DEMO] Fone de Ouvido Bluetooth com Cancelamento de Ruído", "preco_promocional": 59.9,
             "preco_referencia": 149.9, "desconto_pct": 60, "marketplace_nome": "Loja de Demonstração", "demo": True}
    SAIDA.mkdir(exist_ok=True)
    foto = arte.foto_padrao()
    (SAIDA / "exemplo_feed.jpg").write_bytes(arte.criar_arte(dados, foto, 1080, 1350, cfg))
    (SAIDA / "exemplo_story.jpg").write_bytes(arte.criar_arte(dados, foto, 1080, 1920, cfg))
    (SAIDA / "exemplo_imperdivel.jpg").write_bytes(arte.criar_arte(dados, foto, 1080, 1350, cfg, destaque=True))
    print(f"Artes salvas em {SAIDA}/")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="bot-de-ofertas", description="Bot de Ofertas")
    sub = p.add_subparsers(dest="comando")
    r = sub.add_parser("rodar")
    r.add_argument("--origem", default="agendada", choices=["agendada", "manual", "teste"])
    r.add_argument("--comissoes", action="store_true", help="importa comissões nesta rodada")
    sub.add_parser("simular")
    sub.add_parser("testar-integracoes")
    c = sub.add_parser("comissoes")
    c.add_argument("--dias", type=int, default=7)
    sub.add_parser("arte-exemplo")
    args = p.parse_args(argv)
    if not args.comando:
        args = p.parse_args(["rodar"])

    log = Log()
    comandos = {"rodar": cmd_rodar, "simular": cmd_simular, "testar-integracoes": cmd_testar,
                "comissoes": cmd_comissoes, "arte-exemplo": cmd_arte}
    try:
        return comandos[args.comando](args, log)
    except ErroConfiguracao as e:
        log.critico("config", str(e))
        return 2
    except ErroBot as e:
        log.critico("bot", f"{type(e).__name__}: {e}")
        return 1
    except Exception as e:  # nunca deixar um traceback cru (pode conter URL com token) no log público
        log.critico("bot", f"Erro inesperado: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
