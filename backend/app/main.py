"""Linha de comando do bot.

  python -m app.main rodar                # rodada completa (usada pelo GitHub Actions)
  python -m app.main simular              # rodada completa em memória: sem banco, sem internet, modo DEMO
  python -m app.main testar-integracoes   # mostra o modo de cada integração e testa as reais
  python -m app.main comissoes            # importa comissões agora
  python -m app.main arte-exemplo         # gera artes de exemplo na pasta saida/
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import VERSAO
from .cliente_http import ClienteHTTP
from .ambiente import conferir_banco, ler_ambiente
from .config import carregar_env, ler_config
from .erros import ErroBot, ErroConfiguracao
from .integracoes import registro
from .integracoes.fontes.demo import DemoFonte
from .integracoes.canais.demo import DemoCanal
from .logs import Log, mascarar
from .monitor_externo import Batimento
from .orquestrador import Orquestrador
from .repositorio import SupabaseRepositorio
from .repositorio_memoria import MemoriaRepositorio
from .servicos import arte

SAIDA = Path(__file__).resolve().parents[1] / "saida"


def _montar_real(log: Log):
    carregar_env()
    amb = ler_ambiente(os.environ)  # sem AMBIENTE definido o bot não roda
    base = ler_config()  # valida SUPABASE_URL e a chave antes de qualquer coisa
    repo = SupabaseRepositorio(base.supabase_url, base.supabase_chave, ClienteHTTP("supabase", timeout=base.timeout_http))
    config = ler_config(buscar_no_cofre=repo.obter_credencial)
    log.adicionar_segredos(config.segredos)
    cfg = repo.configuracoes()
    conferir_banco(amb, cfg)  # banco de outro ambiente: bloqueado
    integ = registro.montar(config, ordenacao_shopee=int(cfg.get("busca.ordenacao_shopee") or 2), ambiente=amb)
    log.info("bot", f"Ambiente: {amb.nome.upper()}", integracoes_ativas=amb.ativas)
    return config, repo, integ, amb


def cmd_rodar(args, log: Log) -> int:
    batimento = Batimento(os.environ.get("HEALTHCHECK_URL"))
    batimento.inicio()
    try:
        config, repo, integ, amb = _montar_real(log)
        log.info("bot", f"Bot de Ofertas v{VERSAO}", **config.resumo_seguro())
        status, resumo = Orquestrador(repo, integ, log, url_supabase=config.supabase_url, ambiente=amb).executar(
            origem=args.origem, forcar_comissoes=args.comissoes)
    except Exception as e:
        batimento.falha(mascarar(f"{type(e).__name__}: {e}", log.segredos))
        raise
    texto = f"status={status} alertas_criticos={resumo.alertas_criticos}"
    # saída 1 = o GitHub marca a execução como falha e manda e-mail para o dono do repositório
    if status == "erro" or resumo.alertas_criticos:
        batimento.falha(texto)
        return 1
    batimento.sucesso(texto)
    return 0


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
    config, repo, integ, _ = _montar_real(log)
    print(f"Banco: {config.supabase_url} ✔ ({len(repo.marketplaces())} marketplaces, {len(repo.canais())} canais)")
    falhou = False
    for item in list(integ.fontes.values()) + list(integ.canais.values()):
        try:
            print("  " + item.testar())
        except ErroBot as e:
            falhou = True
            print(f"  {item.codigo}: ✖ {e}")
    return 1 if falhou else 0


def cmd_modulo(args, log: Log) -> int:
    """vitrine | alertas | whatsapp: executa um módulo agora (respeita as flags recursos.*)."""
    from .modulos import Modulos
    config, repo, integ, amb = _montar_real(log)
    cfg = repo.configuracoes()
    m = Modulos(repo, integ, log, config.supabase_url, amb)
    r = {"vitrine": m.vitrine, "alertas": m.alertas, "whatsapp": m.whatsapp}[args.comando](cfg)
    print(r)
    return 0


def cmd_worker(args, log: Log) -> int:
    """Worker independente da fila de tarefas (vários podem rodar em paralelo)."""
    from .modulos import Modulos
    from .worker import Worker
    config, repo, integ, amb = _montar_real(log)
    handlers = Modulos(repo, integ, log, config.supabase_url, amb).handlers()
    tipos = [t for t in (args.tipos.split(",") if args.tipos else handlers) if t in handlers]
    r = Worker(repo, {t: handlers[t] for t in tipos}, log).rodar(max_tarefas=args.max)
    print(r)
    try:
        repo.gravar_logs(None, log.retirar_pendentes())
    except Exception:
        pass
    return 1 if r["falhas"] else 0


def cmd_comissoes(args, log: Log) -> int:
    config, repo, integ, _ = _montar_real(log)
    from .servicos.comissoes import ServicoComissoes
    servico = ServicoComissoes(repo, integ.fontes, log)
    n = servico.executar(repo.configuracoes(), forcar=True, dias=args.dias)
    print(f"{n} itens de comissão processados")
    if args.reconciliar:
        r = servico.reconciliar(repo.configuracoes(), forcar=True)
        print(f"Reconciliação de 90 dias: {r['divergencias']} divergência(s)")
        return 1 if r["divergencias"] else 0
    return 0


def cmd_validar(args, log: Log) -> int:
    """Validação REAL somente leitura. Não usa registro.montar (que em homologação troca os canais por
    simulados e por isso reprovava tudo): monta clientes reais com HTTP que barra qualquer escrita."""
    from .cliente_http import ClienteHTTPSomenteLeitura
    from .validacao_integracoes import validar
    carregar_env()
    amb = ler_ambiente(os.environ)
    base = ler_config()
    repo = SupabaseRepositorio(base.supabase_url, base.supabase_chave, ClienteHTTP("supabase", timeout=base.timeout_http))
    config = ler_config(buscar_no_cofre=repo.obter_credencial)
    log.adicionar_segredos(config.segredos)
    cfg = repo.configuracoes()
    conferir_banco(amb, cfg)
    alvos = registro.montar_validacao(config, amb, int(cfg.get("busca.ordenacao_shopee") or 2),
                                      permitir_envio_imgbb=args.imgbb)
    print(f"Validando integrações REAIS · ambiente {amb.nome.upper()} · modo SOMENTE LEITURA (nada é publicado)")
    return validar(alvos, repo, log, link_manual=args.link, http=ClienteHTTPSomenteLeitura("validacao"),
                   saida=SAIDA / "validacao_integracoes.json", ambiente=amb.nome)


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
    c.add_argument("--reconciliar", action="store_true", help="relê 90 dias e compara com o banco")
    v = sub.add_parser("validar-integracoes", help="valida as integrações reais (somente leitura) e grava evidência")
    v.add_argument("--imgbb", action="store_true", help="envia 1 imagem de teste de 1 pixel ao ImgBB (expira em 60 s)")
    v.add_argument("--link", help="link de afiliado para conferir a atribuição")
    sub.add_parser("arte-exemplo")
    for nome in ("vitrine", "alertas", "whatsapp"):
        sub.add_parser(nome, help=f"executa o módulo {nome} agora (feature flag recursos.* precisa estar ligada)")
    w = sub.add_parser("worker", help="consome a fila persistente de tarefas")
    w.add_argument("--tipos", help="ex.: vitrine.gerar,alertas.processar (padrão: todos)")
    w.add_argument("--max", type=int, default=50)
    args = p.parse_args(argv)
    if not args.comando:
        args = p.parse_args(["rodar"])

    log = Log()
    comandos = {"rodar": cmd_rodar, "simular": cmd_simular, "testar-integracoes": cmd_testar,
                "comissoes": cmd_comissoes, "arte-exemplo": cmd_arte, "validar-integracoes": cmd_validar,
                "vitrine": cmd_modulo, "alertas": cmd_modulo, "whatsapp": cmd_modulo, "worker": cmd_worker}
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
