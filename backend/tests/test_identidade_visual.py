"""Identidade visual: fundo da marca no story, logo no feed, e layout padrão quando os arquivos faltam."""
import io

from PIL import Image

from app.servicos import arte

CFG = {"visual.cor_principal": "#DC1B63", "visual.cor_destaque": "#FDC302", "visual.nome_perfil": "Sonho das Promos",
       "textos.rodape_story": "Link na bio"}
OFERTA = {"titulo": "Produto de teste com um nome bem comprido para quebrar em linhas", "preco_promocional": 241.0,
          "preco_referencia": 419.0, "desconto_pct": 42.5, "marketplace_nome": "Mercado Livre"}


def _perto(a, b, tol=6):  # o JPEG arredonda a cor em 1–2 pontos
    return max(abs(x - y) for x, y in zip(a, b)) <= tol


def _abrir(b):
    return Image.open(io.BytesIO(b)).convert("RGB")


def test_story_usa_o_fundo_da_marca_sem_cobrir_a_faixa():
    fundo = Image.open(arte.PASTA_MARCA / "story_fundo.png").convert("RGB")
    img = _abrir(arte.criar_arte(OFERTA, arte.foto_padrao(), 1080, 1920, CFG))
    assert img.size == (1080, 1920)
    # a faixa de chamada do modelo (parte de baixo) continua igual ao arquivo da marca
    for p in [(540, 1450), (200, 1700), (900, 1600)]:
        assert max(abs(a - b) for a, b in zip(img.getpixel(p), fundo.getpixel(p))) < 40


def test_story_sem_fundo_volta_ao_layout_padrao(monkeypatch, tmp_path):
    monkeypatch.setattr(arte, "PASTA_MARCA", tmp_path)
    img = _abrir(arte.criar_arte(OFERTA, arte.foto_padrao(), 1080, 1920, CFG))
    assert _perto(img.getpixel((160, 1920 - 150)), (253, 195, 2))  # botão do rodapé na cor de destaque


def test_fundo_desligado_na_configuracao():
    img = _abrir(arte.criar_arte(OFERTA, arte.foto_padrao(), 1080, 1920, {**CFG, "visual.usar_fundo_story": False}))
    assert _perto(img.getpixel((160, 1920 - 150)), (253, 195, 2))


def test_fundo_corrompido_nao_derruba_a_arte(monkeypatch, tmp_path):
    (tmp_path / "story_fundo.png").write_bytes(b"isso nao e imagem")
    (tmp_path / "logo.png").write_bytes(b"nem isso")
    monkeypatch.setattr(arte, "PASTA_MARCA", tmp_path)
    assert _abrir(arte.criar_arte(OFERTA, arte.foto_padrao(), 1080, 1350, CFG)).size == (1080, 1350)
    assert _abrir(arte.criar_arte(OFERTA, arte.foto_padrao(), 1080, 1920, CFG)).size == (1080, 1920)


def test_fonte_da_marca_disponivel():
    assert "Poppins" in arte.fonte(40, True).getname()[0]


# ---- legenda do Telegram no formato da marca ----
from app.servicos import textos  # noqa: E402

D = {"promocao_id": 1, "titulo": "Fone <Premium>", "preco_promocional": 89.9, "preco_referencia": 199.9,
     "desconto_pct": 55, "marketplace_nome": "Shopee", "desconto_comprovado": True}


def test_legenda_telegram_no_formato_da_marca():
    t = textos.legenda_telegram(D, "https://s.shopee.com.br/x", {"transparencia.marcar_publi": True,
                                                                  "transparencia.aviso_afiliado": "Posso ganhar comissão."})
    linhas = t.splitlines()
    assert linhas[0].startswith("<b>") and linhas[0].endswith("</b>")  # chamada em negrito no topo
    assert "🛍️ Fone &lt;Premium&gt;" in t
    assert "😱🔻 <b>55% DE DESCONTO</b>" in t and "De: <s>R$ 199,90</s>" in t and "💥 <b>Por: R$ 89,90</b>" in t
    assert "🛒 <b>Compre aqui:</b>\nhttps://s.shopee.com.br/x" in t
    assert "⚠️ <b>Promoção sujeita à alteração de preço e estoque do site</b>" in t
    assert linhas[-1] == "<i>#publi</i>" and "comissão" not in t  # só o #publi, sem o aviso de comissão


def test_legenda_sem_desconto_comprovado_nao_mostra_de_nem_porcentagem():
    t = textos.legenda_telegram({**D, "desconto_comprovado": False}, "https://l", {})
    assert "DE DESCONTO" not in t and "199,90" not in t and "Por: R$ 89,90" in t and "#publi" in t


def test_headline_fixa_por_oferta_e_lista_configuravel():
    assert textos.headline(D, {}) == textos.headline(D, {})
    assert {textos.headline({**D, "promocao_id": i}, {}) for i in range(12)} == set(textos.HEADLINES_TELEGRAM)
    assert textos.headline(D, {"textos.headlines_telegram": ["SÓ ESSA 💖"]}) == "SÓ ESSA 💖"


def test_legenda_cabe_no_limite_do_telegram():
    assert len(textos.legenda_telegram({**D, "titulo": "x" * 3000}, "https://l", {})) <= 1024
