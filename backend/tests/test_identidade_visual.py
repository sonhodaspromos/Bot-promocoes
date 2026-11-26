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
