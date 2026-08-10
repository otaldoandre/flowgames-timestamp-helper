"""
Automação da thumbnail: abre o template, troca o conteúdo dos dois
Smart Objects (imagem sugerida + frame do host), preenche as duas
linhas de texto, e exporta um PNG.

Ainda NÃO reposiciona os grupos por lado (host esquerda/direita) — essa
parte fica pro próximo passo, depois de confirmar que essa base
funciona.

Só roda no Windows, com Photoshop instalado. Não consigo testar isso
no meu ambiente (sem Windows/Photoshop aqui) — a ação
"placedLayerReplaceContents" é o jeito documentado de trocar conteúdo
de Smart Object via script, mas TESTE NUMA CÓPIA do template antes de
confiar, não no arquivo original.

Dependência:
    pip install pywin32

Uso:
    python gerar_thumbnail.py
    (edite as constantes no topo antes de rodar)
"""

import re

import win32com.client

CAMINHO_TEMPLATE = r"C:\Users\andre\Downloads\projetos\flowgames-timestamp-helper\scripts\thumb_corte_template.psd"
CAMINHO_IMAGEM_SUGERIDA = r"C:\Users\andre\Downloads\projetos\flowgames-timestamp-helper\scripts\kratos.jpg"
CAMINHO_FRAME_HOST = r"C:\Users\andre\Downloads\projetos\flowgames-timestamp-helper\scripts\davy-host.png"
TEXTO_LINHA1 = "VAMOS TRAZER"
TEXTO_LINHA2 = "O DUBLADOR AQUI"
CAMINHO_SAIDA_PNG = r"C:\Users\andre\Downloads\projetos\flowgames-timestamp-helper\scripts\thumbnail_saida.png"

# Quanto deslocar o texto pra esquerda, em pixels. Começa em 0 (volta
# pro comportamento original que funcionava) — ajuste aos poucos (ex:
# -50, depois -100...) até a posição ficar como você quer, em vez de
# tentar acertar de primeira.
DESLOCAMENTO_TEXTO_X = 0

# "direita" = layout padrão do template (nenhuma mudança de posição).
# "esquerda" = coordenadas reais tiradas de uma versão que você já
# ajustou manualmente — ponto de partida, não garantia (você comentou
# que às vezes ainda ajusta tamanho de host/texto caso a caso).
LAYOUTS = {
    "direita": {
        "convidado_pos": None,
        "convidado_tamanho": None,
        "txt_pos": None,
    },
    "esquerda": {
        "convidado_pos": (-204, 8),
        "convidado_tamanho": (953, 777),
        "txt_pos": (130, 486),
    },
}


def escapar_caminho_jsx(caminho):
    """Caminho Windows precisa de barra dupla dentro de uma string JS."""
    return caminho.replace("\\", "\\\\")


def escapar_texto_jsx(texto):
    return texto.replace("\\", "\\\\").replace('"', '\\"')


def montar_jsx(caminho_template, caminho_imagem, caminho_frame, linha1, linha2, caminho_saida, deslocamento_texto_x=0, lado="direita", copiar_sombra=False):
    layout = LAYOUTS[lado]

    # .psd editável salvo ao lado do PNG, mesmo nome, extensão diferente
    caminho_saida_psd = re.sub(r"\.png$", ".psd", caminho_saida, flags=re.IGNORECASE)
    if caminho_saida_psd == caminho_saida:  # não tinha .png no final
        caminho_saida_psd = caminho_saida + ".psd"

    trecho_layout = ""
    if layout["convidado_tamanho"]:
        w, h = layout["convidado_tamanho"]
        trecho_layout += f'    redimensionarCamada("CONVIDADO", "Camada 10", {w}, {h});\n'
    if layout["convidado_pos"]:
        x, y = layout["convidado_pos"]
        trecho_layout += f'    moverCamada("CONVIDADO", "Camada 10", {x}, {y});\n'
    if layout["txt_pos"]:
        x, y = layout["txt_pos"]
        trecho_layout += f'    moverGrupo("TXT", {x}, {y});\n'
    return f"""
var etapaAtual = "iniciando";
var doc = null;
try {{
    function obterBoundsPx(camada) {{
        var b = camada.bounds;
        return {{ left: b[0].as("px"), top: b[1].as("px"), right: b[2].as("px"), bottom: b[3].as("px") }};
    }}

    function moverCamada(grupoNome, camadaNome, xAlvo, yAlvo) {{
        var doc = app.activeDocument;
        var grupo = doc.layerSets.getByName(grupoNome);
        var camada = grupo.artLayers.getByName(camadaNome);
        var b = obterBoundsPx(camada);
        camada.translate(xAlvo - b.left, yAlvo - b.top);
    }}

    function redimensionarCamada(grupoNome, camadaNome, larguraAlvo, alturaAlvo) {{
        var doc = app.activeDocument;
        var grupo = doc.layerSets.getByName(grupoNome);
        var camada = grupo.artLayers.getByName(camadaNome);
        var b = obterBoundsPx(camada);
        var larguraAtual = b.right - b.left;
        var alturaAtual = b.bottom - b.top;
        camada.resize((larguraAlvo / larguraAtual) * 100, (alturaAlvo / alturaAtual) * 100, AnchorPosition.TOPLEFT);
    }}

    function moverGrupo(grupoNome, xAlvo, yAlvo) {{
        var doc = app.activeDocument;
        var grupo = doc.layerSets.getByName(grupoNome);
        var b = obterBoundsPx(grupo);
        grupo.translate(xAlvo - b.left, yAlvo - b.top);
    }}

    function copiarEstiloDeCamada(grupoNome, camadaOrigemNome, camadaDestinoNome) {{
        var doc = app.activeDocument;
        var grupo = doc.layerSets.getByName(grupoNome);
        var origem = grupo.artLayers.getByName(camadaOrigemNome);
        var destino = grupo.artLayers.getByName(camadaDestinoNome);

        doc.activeLayer = origem;
        var idCopyEffects = stringIDToTypeID("copyEffects");
        executeAction(idCopyEffects, undefined, DialogModes.NO);

        doc.activeLayer = destino;
        var idPasteEffects = stringIDToTypeID("pasteEffects");
        executeAction(idPasteEffects, undefined, DialogModes.NO);
    }}

    function substituirSmartObject(grupoNome, camadaNome, caminhoArquivo) {{
        var arquivo = new File(caminhoArquivo);
        if (!arquivo.exists) {{
            throw new Error("Imagem não encontrada nesse caminho: " + arquivo.fsName);
        }}

        var doc = app.activeDocument;
        var grupo = doc.layerSets.getByName(grupoNome);
        var camada = grupo.artLayers.getByName(camadaNome);
        doc.activeLayer = camada;

        var idPlaced = stringIDToTypeID("placedLayerReplaceContents");
        var desc = new ActionDescriptor();
        desc.putPath(charIDToTypeID("null"), arquivo);
        executeAction(idPlaced, desc, DialogModes.NO);
    }}

    function definirTexto(grupoNome, camadaNome, textoNovo, deslocamentoX) {{
        var doc = app.activeDocument;
        var grupo = doc.layerSets.getByName(grupoNome);
        var camada = grupo.artLayers.getByName(camadaNome);
        camada.textItem.contents = textoNovo;
        if (deslocamentoX !== 0) {{
            camada.translate(deslocamentoX, 0);
        }}
    }}

    var arquivoTemplate = new File("{escapar_caminho_jsx(caminho_template)}");
    if (!arquivoTemplate.exists) {{
        throw new Error("Template não encontrado nesse caminho: " + arquivoTemplate.fsName);
    }}

    etapaAtual = "abrindo template";
    doc = app.open(arquivoTemplate);

    etapaAtual = "trocando imagem sugerida (IMAGEM / Camada 7)";
    substituirSmartObject("IMAGEM", "Camada 7", "{escapar_caminho_jsx(caminho_imagem)}");

    etapaAtual = "trocando frame do host (CONVIDADO / Camada 10)";
    substituirSmartObject("CONVIDADO", "Camada 10", "{escapar_caminho_jsx(caminho_frame)}");

    etapaAtual = "copiando estilo de sombra (Layer 23 -> Camada 10)";
{"    copiarEstiloDeCamada(\"CONVIDADO\", \"Layer 23\", \"Camada 10\");" if copiar_sombra else "    // desligado por agora — aplica a sombra manualmente no .psd exportado"}

    etapaAtual = "aplicando layout (redimensionar/mover CONVIDADO e TXT)";
{trecho_layout}
    etapaAtual = "definindo texto linha 1";
    definirTexto("TXT", "VAMOS TRAZER", "{escapar_texto_jsx(linha1)}", {deslocamento_texto_x});

    etapaAtual = "definindo texto linha 2";
    definirTexto("TXT", "O DUBLADOR AQUI!", "{escapar_texto_jsx(linha2)}", {deslocamento_texto_x});

    etapaAtual = "exportando PNG";
    var pngOpts = new PNGSaveOptions();
    doc.saveAs(new File("{escapar_caminho_jsx(caminho_saida)}"), pngOpts, true);

    etapaAtual = "salvando PSD";
    var psdOpts = new PhotoshopSaveOptions();
    doc.saveAs(new File("{escapar_caminho_jsx(caminho_saida_psd)}"), psdOpts, true);

    etapaAtual = "fechando documento";
    doc.close(SaveOptions.DONOTSAVECHANGES);

    "OK";
}} catch (e) {{
    if (doc) {{
        try {{
            doc.close(SaveOptions.DONOTSAVECHANGES);
        }} catch (e2) {{
            // se nem isso der certo, segue mesmo assim -- a mensagem
            // de erro principal já é o que importa pra debugar
        }}
    }}
    "ERRO na etapa '" + etapaAtual + "': " + e.message;
}}
"""


def gerar_thumbnail(caminho_template, caminho_imagem, caminho_frame, linha1, linha2, caminho_saida, deslocamento_texto_x=0, lado="direita", copiar_sombra=False):
    # gencache.EnsureDispatch (em vez de Dispatch simples) resolve um
    # problema comum de "argumento ausente" com interfaces COM da
    # Adobe — o late binding do Dispatch simples às vezes não lida bem
    # com parâmetros opcionais, mesmo passando o argumento certo.
    app = win32com.client.gencache.EnsureDispatch("Photoshop.Application")
    script = montar_jsx(caminho_template, caminho_imagem, caminho_frame, linha1, linha2, caminho_saida, deslocamento_texto_x, lado, copiar_sombra)
    resultado = app.DoJavaScript(script)
    return resultado


# "direita" (padrão do template) ou "esquerda" (host no outro lado) —
# escolha com base em qual direção o host tá olhando no frame extraído
# (dá pra automatizar com o detectar_lado_rosto.py depois)
LADO = "direita"

if __name__ == "__main__":
    resultado = gerar_thumbnail(
        CAMINHO_TEMPLATE,
        CAMINHO_IMAGEM_SUGERIDA,
        CAMINHO_FRAME_HOST,
        TEXTO_LINHA1,
        TEXTO_LINHA2,
        CAMINHO_SAIDA_PNG,
        DESLOCAMENTO_TEXTO_X,
        LADO,
    )
    print("Resultado:", resultado)
    if resultado == "OK":
        print(f"Thumbnail salva em: {CAMINHO_SAIDA_PNG}")
    else:
        print("Deu erro — o texto acima mostra a mensagem do Photoshop.")