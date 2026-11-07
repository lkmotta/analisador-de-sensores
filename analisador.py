"""Analisador de qualidade de dados de sensores (temperatura/umidade).

Dirceu Morais da Costa Júnior
Felipe Kenzo Higashi
Lucas Henrique Motta

SPRINT 2: este modulo contem apenas o ESQUELETO (assinaturas, constantes e
contratos). As funcoes ainda nao estao implementadas e levantam
NotImplementedError. Os testes em test_analisador.py servem como
especificacao para a Sprint 3.

Registro (dict), uma linha do CSV:
    {
      "id": str,                  # referencia ao registro original
      "sensor_id": str,
      "modelo": str,              # ex.: "DHT22"
      "timestamp": "AAAA-MM-DD HH:MM:SS",
      "temperatura": float | None,
      "umidade": float | None,
      "status": "Imputado",       # so nos registros recompostos (HU3)
      "status_estatistico": {"temperatura": "Normal"|"Outlier", "umidade": ...},
      "suspeita_travamento": {"temperatura": bool, "umidade": bool},
    }
    
Valor ausente ou invalido (None, texto, NaN) encerra sequencias (HU2) e nao
entra em medias (HU3).

Trecho constante suspeito (dict) (campos do CA2.4):
    sensor_id, modelo, variavel, valor, inicio, fim, duracao_segundos,
    quantidade_leituras, ids_registros
"""

# parametros definidos nos criterios de aceitacao
VARIAVEIS = ("temperatura", "umidade")
JANELA_AMOSTRAS = 21             # CA1.1
BARREIRA_QUARTIS = 2             # CA1.1
LIMIAR_GRUBBS = 2.58             # CA1.2 (N=21, alfa=0,05)
MIN_LEITURAS_CONSTANTES = 10     # CA2.2 (configuravel)
LIMITE_GAP_SEGUNDOS = 15         # CA3.1
K_MEDIA_MOVEL = 3                # CA3.2
MAX_FALHAS_CONSECUTIVAS = 5      # CA3.4
INTERVALO_PADRAO_SEGUNDOS = 10   # intervalo esperado das leituras

# status e alertas
NORMAL = "Normal"
OUTLIER = "Outlier"
IMPUTADO = "Imputado"
SENSOR_INOPERANTE = "Sensor Inoperante"


# HU1 deteccao estatistica de outliers
def outlier_quartis(janela, variavel="temperatura"):
    """Conta os valores fora dos limites e exporta registros quando aplicável."""
    from funcAux import quicksort
    if not janela:
        raise ValueError("A janela precisa conter pelo menos uma amostra.")

    registros = isinstance(janela[0], dict)
    valores = [
        float(amostra[variavel]) if registros else float(amostra)
        for amostra in janela
    ]

    janela_ordenada = quicksort(valores)
    q1 = janela_ordenada[len(janela_ordenada) // 4]
    q3 = janela_ordenada[3 * len(janela_ordenada) // 4]
    iqr = q3 - q1

    barreira_q1 = q1 - BARREIRA_QUARTIS * iqr
    barreira_q3 = q3 + BARREIRA_QUARTIS * iqr
    outliers = [
        int(valor < barreira_q1 or valor > barreira_q3)
        for valor in valores
    ]

    if registros:
        import csv

        sensor_ids = {amostra.get("sensor_id") for amostra in janela}
        if len(sensor_ids) != 1 or None in sensor_ids:
            raise ValueError("Todos os registros precisam ter o mesmo sensor_id.")

        sensor_id = sensor_ids.pop()
        fieldnames = list(dict.fromkeys(
            campo for amostra in janela for campo in amostra
        ))
        if "outliers" not in fieldnames:
            fieldnames.append("outliers")
        with open(f"{sensor_id}_{variavel}_quartis.csv", "w", newline="",
                  encoding="utf-8") as arquivo:
            writer = csv.DictWriter(arquivo, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(
                {**amostra, "outliers": outlier}
                for amostra, outlier in zip(janela, outliers)
            )

        quantidade = sum(outliers)
        print(f"Quantidade de outliers: {quantidade}\nconsiderando {len(janela)} amostras com intervalo dos quantis [{barreira_q1:.2f}, {barreira_q3:.2f}]")
        return quantidade

    return sum(outliers)

def outlier_grubbs(janela, variavel="temperatura"):
    """Conta os outliers de Grubbs e exporta os registros quando aplicável."""
    if not janela:
        raise ValueError("A janela precisa conter pelo menos uma amostra.")

    registros = isinstance(janela[0], dict)
    valores = [
        float(amostra[variavel]) if registros else float(amostra)
        for amostra in janela
    ]

    media = sum(valores) / len(valores)
    desvio_padrao = (sum((x - media) ** 2 for x in valores) / len(valores)) ** 0.5
    limite_inferior = media - LIMIAR_GRUBBS * desvio_padrao
    limite_superior = media + LIMIAR_GRUBBS * desvio_padrao
    if desvio_padrao == 0:
        outliers = [0] * len(valores)
    else:
        outliers = [
            int(valor < limite_inferior or valor > limite_superior)
            for valor in valores
        ]

    quantidade = sum(outliers)
    if registros:
        import csv

        sensor_ids = {amostra.get("sensor_id") for amostra in janela}
        if len(sensor_ids) != 1 or None in sensor_ids:
            raise ValueError("Todos os registros precisam ter o mesmo sensor_id.")

        sensor_id = sensor_ids.pop()
        fieldnames = list(dict.fromkeys(
            campo for amostra in janela for campo in amostra
        ))
        if "outliers" not in fieldnames:
            fieldnames.append("outliers")
        with open(f"{sensor_id}_{variavel}_grubbs.csv", "w", newline="",
                  encoding="utf-8") as arquivo:
            writer = csv.DictWriter(arquivo, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(
                {**amostra, "outliers": outlier}
                for amostra, outlier in zip(janela, outliers)
            )

        print(
            f"Quantidade de outliers (Grubbs): {quantidade}\n"
            f"considerando {len(janela)} amostras com intervalo "
            f"[{limite_inferior:.2f}, {limite_superior:.2f}]"
        )

    return quantidade


def classificar_estatistica(janela, variavel="temperatura"):
    """Classifica o intervalo como Outlier se qualquer teste encontrar algum."""
    outliers_quartis = outlier_quartis(janela, variavel=variavel)
    outliers_grubbs = outlier_grubbs(janela, variavel=variavel)
    if outliers_quartis or outliers_grubbs:
        return OUTLIER
    return NORMAL


# HU2 periodos constantes suspeitos
def detectar_trechos_constantes(registros,
                                min_leituras=MIN_LEITURAS_CONSTANTES,
                                variaveis=VARIAVEIS):
    """Retorna a lista de trechos constantes suspeitos.

    - Analisa por sensor_id e por variavel, em ordem cronologica.
    - Considera so registros originais (status != "Imputado") com valor valido.
    - Sequencia = valores exatamente iguais; a 1a leitura conta como amostra.
    - Com >= min_leituras, gera UM trecho (repeticoes seguintes o ampliam).
    - A sequencia termina: valor muda, gap (> 15 s entre originais), valor
      ausente/invalido ou fim dos dados.
    """
    raise NotImplementedError("Sprint 3")


def marcar_suspeitas(registros, min_leituras=MIN_LEITURAS_CONSTANTES):
    """Devolve copias dos registros com "suspeita_travamento" por variavel.

    Todas as leituras do trecho (inclusive as anteriores ao limite) ficam True
    na variavel analisada.
    Nao altera "status_estatistico" nem os valores.
    """
    raise NotImplementedError("Sprint 3")


# HU3 gaps e imputacao
def detectar_gaps(registros, limite_segundos=LIMITE_GAP_SEGUNDOS):
    """Lista de gaps por sensor
    (diferenca dos timestamps consecutivos do mesmo sensor > limite).
    Cada gap: {"sensor_id", "inicio", "fim","duracao_segundos"}.
    """
    raise NotImplementedError("Sprint 3")


def imputar_ausentes(registros, intervalo_segundos=INTERVALO_PADRAO_SEGUNDOS):
    """Recompoe leituras ausentes pela media das k=3 leituras anteriores validas.

    Retornaria {"registros": [...originais + imputados...], "alertas": [...]}.
    - Registros sinteticos recebem status "Imputado".
    - Mais de 5 falhas consecutivas em um gap: nao imputa e adiciona o alerta
      {"alerta": "Sensor Inoperante", "sensor_id": ...} (por sensor).
    """
    raise NotImplementedError("Sprint 3")


# HU4 relatorio final
def gerar_relatorio(registros, trechos=None, gaps=None):
    """Gera o relatorio de qualidade (dict pronto pra json).

    Recebe registros (originais anotados pela HU1/HU2 e imputados da HU3),
    os trechos da HU2 e os gaps da HU3.
    Chaves:
      registros_sinalizados_percentual (float | None), ausencia_de_dados (bool),
      escopo_percentual (str), contagem, por_variavel, trechos,
      por_sensor, por_modelo.
    """
    raise NotImplementedError("Sprint 3")
