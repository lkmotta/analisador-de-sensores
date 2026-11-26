"""Analisador de qualidade de dados de sensores (temperatura/umidade).

Dirceu Morais da Costa Júnior
Felipe Kenzo Higashi
Lucas Henrique Motta

HU1 e HU2: deteccao estatistica e identificacao de possiveis travamentos.
HU3: identificacao de gaps e preenchimento de leituras ausentes.
HU4: consolidacao do relatorio de qualidade.

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

import copy
import csv
import json
import math
import os
import sys
from abc import ABC, abstractmethod
from collections import defaultdict
from contextlib import chdir
from datetime import datetime, timedelta
from pathlib import Path

# parametros definidos nos criterios de aceitacao
VARIAVEIS = ("temperatura", "umidade")
BARREIRA_QUARTIS = 2             # CA1.1
MIN_LEITURAS_CONSTANTES = 20     # CA2.2 (configuravel)
LIMITE_GAP_SEGUNDOS = 30         # CA3.1
LIMITE_GAP_HU2_SEGUNDOS = 30     # tolerancia da sequencia constante (HU2)
K_MEDIA_MOVEL = 3                # CA3.2
MAX_FALHAS_CONSECUTIVAS = 5      # CA3.4
INTERVALO_PADRAO_SEGUNDOS = 20   # intervalo esperado das leituras

# status e alertas
NORMAL = "Normal"
OUTLIER = "Outlier"
IMPUTADO = "Imputado"
SENSOR_INOPERANTE = "Sensor Inoperante"


# HU1 deteccao estatistica de outliers
def _valores_da_janela(janela, variavel):
    """Devolve os valores numericos e indica se a janela contem registros."""
    if not janela:
        raise ValueError("A janela precisa conter pelo menos uma amostra.")

    registros = isinstance(janela[0], dict)
    valores = [
        float(amostra[variavel]) if registros else float(amostra)
        for amostra in janela
    ]
    return registros, valores


class EstrategiaOutliers(ABC):
    """Contrato para os algoritmos de deteccao de outliers."""

    titulo: str
    descricao_intervalo: str

    @abstractmethod
    def calcular(self, valores):
        """Retorna as marcacoes na ordem da entrada e os dois limites."""


class EstrategiaQuartis(EstrategiaOutliers):
    titulo = "Quantidade de outliers(Quartis)"
    descricao_intervalo = "intervalo dos quantis"

    def calcular(self, valores):
        janela_ordenada = quicksort(valores)
        q1 = janela_ordenada[len(janela_ordenada) // 4]
        q3 = janela_ordenada[3 * len(janela_ordenada) // 4]
        iqr = q3 - q1

        limite_inferior = q1 - BARREIRA_QUARTIS * iqr
        limite_superior = q3 + BARREIRA_QUARTIS * iqr
        outliers = [
            int(valor < limite_inferior or valor > limite_superior)
            for valor in valores
        ]
        return outliers, limite_inferior, limite_superior


class EstrategiaGrubbs(EstrategiaOutliers):
    titulo = "Quantidade de outliers (Grubbs)"
    descricao_intervalo = "intervalo"

    def calcular(self, valores):
        limiar = Limiar_grubbs(len(valores))
        media = sum(valores) / len(valores)
        desvio_padrao = (sum((valor - media) ** 2 for valor in valores)
                         / len(valores)) ** 0.5

        if desvio_padrao == 0:
            return [0] * len(valores), media, media

        limite_inferior = media - limiar * desvio_padrao
        limite_superior = media + limiar * desvio_padrao
        outliers = [
            int(valor < limite_inferior or valor > limite_superior)
            for valor in valores
        ]
        return outliers, limite_inferior, limite_superior

# dicionario em variável constante pra facilitar a escolha da estrategia
_ESTRATEGIAS_OUTLIERS: dict[str, EstrategiaOutliers] = {
    "quartis": EstrategiaQuartis(),
    "grubbs": EstrategiaGrubbs(),
}


def _exportar_outliers(janela, variavel, metodo, marcacoes):
    sensor_ids = {amostra.get("sensor_id") for amostra in janela}
    if len(sensor_ids) != 1 or None in sensor_ids:
        raise ValueError("Todos os registros precisam ter o mesmo sensor_id.")

    sensor_id = sensor_ids.pop()
    fieldnames = list(dict.fromkeys(
        campo for amostra in janela for campo in amostra
    ))
    if "outliers" not in fieldnames:
        fieldnames.append("outliers")

    with open(f"{sensor_id}_{variavel}_{metodo}.csv", "w", newline="",
              encoding="utf-8") as arquivo:
        writer = csv.DictWriter(arquivo, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(
            {**amostra, "outliers": marcou}
            for amostra, marcou in zip(janela, marcacoes)
        )


def analisar_outliers(janela, variavel="temperatura", metodo="ambos",
                      retornar_marcacoes=False):
    """Conta outliers por Quartis, Grubbs ou pelos dois metodos.

    Retorna a quantidade encontrada em cada metodo pedido.
    Se retornar_marcacoes for True, inclui tambem a marca de cada amostra.
    """
    # verificando se o metodo informado é uma strategy disponível
    if not isinstance(metodo, str) or metodo.lower() not in (*_ESTRATEGIAS_OUTLIERS, "ambos"):
        raise ValueError("metodo deve ser 'quartis', 'grubbs' ou 'ambos'.")

    metodo = metodo.lower() # minuscula pra padronizar
    registros, valores = _valores_da_janela(janela, variavel) # pega os valores numericos da janela

    escolhidos = _ESTRATEGIAS_OUTLIERS if metodo == "ambos" else (metodo,) # escolhe as estratégias a serem usadas
    resultado = {}

    for nome in escolhidos:
        estrategia = _ESTRATEGIAS_OUTLIERS[nome]
        # a estratégia identifica os outliers e informa os limites do intervalo analisado
        marcacoes, limite_inferior, limite_superior = estrategia.calcular(valores)
        quantidade = sum(marcacoes)

        # armazenando a quantidade encontrada e (se solicitado) as marcações de cada amostra
        if retornar_marcacoes:
            resultado[nome] = {
                "quantidade": quantidade,
                "marcacoes": marcacoes,
            }
        else:
            resultado[nome] = quantidade

        # se tiver outliers
        if registros:
            _exportar_outliers(janela, variavel, nome, marcacoes) # exporta os resultados pra um CSV
            print(
                f"{estrategia.titulo}: {quantidade}\n"
                f"considerando {len(janela)} amostras com "
                f"{estrategia.descricao_intervalo} "
                f"[{limite_inferior:.2f}, {limite_superior:.2f}]"
            )

    return resultado


def outlier_quartis(janela, variavel="temperatura"):
    """Conta os outliers pelo metodo dos Quartis."""
    return analisar_outliers(janela, variavel, metodo="quartis")["quartis"]


def outlier_grubbs(janela, variavel="temperatura"):
    """Conta os outliers pelo metodo de Grubbs."""
    return analisar_outliers(janela, variavel, metodo="grubbs")["grubbs"]


def classificar_estatistica(janela, variavel="temperatura", metodo="ambos"):
    """Classifica como Outlier se algum metodo selecionado encontrar um."""
    resultado = analisar_outliers(janela, variavel, metodo)
    if any(quantidade > 0 for quantidade in resultado.values()):
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
    - A sequencia termina: valor muda, gap (> 30 s entre originais), valor
      ausente/invalido ou fim dos dados.
    """
    if min_leituras < 1:
        raise ValueError("min_leituras precisa ser pelo menos 1.")

    def parsear_timestamp(valor):
        if isinstance(valor, datetime):
            return valor
        if not isinstance(valor, str) or not valor.strip():
            return None
        try:
            return datetime.fromisoformat(valor)
        except ValueError:
            try:
                return datetime.strptime(valor, "%d/%m/%Y %H:%M:%S")
            except ValueError:
                return None

    def texto_timestamp(registro):
        for campo in ("timestamp", "hora", "Carimbo de data/hora"):
            valor = registro.get(campo)
            if parsear_timestamp(valor) is not None:
                return valor.isoformat(sep=" ") if isinstance(valor, datetime) else str(valor)
        return None

    def timestamp_do(registro):
        for campo in ("timestamp", "hora", "Carimbo de data/hora"):
            instante = parsear_timestamp(registro.get(campo))
            if instante is not None:
                return instante
        return None

    def id_registro(registro, indice):
        if registro.get("id") is not None:
            return registro["id"]
        horario = texto_timestamp(registro)
        return f'{registro.get("sensor_id", "sensor")}@{horario}#{indice}'

    def numero_valido(valor):
        # bool e conversivel para float, mas nao representa uma leitura numerica.
        if isinstance(valor, bool) or valor is None:
            return None
        try:
            numero = float(valor)
        except (TypeError, ValueError, OverflowError):
            return None
        return numero if math.isfinite(numero) else None

    por_sensor = {}
    for indice, registro in enumerate(registros):
        if registro.get("status") == IMPUTADO:
            continue
        instante = timestamp_do(registro)
        if instante is None:
            # Sem horario nao e possivel ordenar nem descrever um trecho.
            continue
        sensor_id = registro.get("sensor_id")
        por_sensor.setdefault(sensor_id, []).append(
            (instante, indice, registro)
        )

    trechos = []
    for sensor_id, leituras in por_sensor.items():
        leituras.sort(key=lambda item: (item[0], item[1]))
        for variavel in variaveis:
            sequencia = []
            valor_sequencia = None
            anterior = None

            def fechar_sequencia():
                if len(sequencia) >= min_leituras:
                    primeira = sequencia[0][2]
                    ultima = sequencia[-1][2]
                    inicio = sequencia[0][0]
                    fim = sequencia[-1][0]
                    valor = numero_valido(primeira.get(variavel))
                    trechos.append({
                        "sensor_id": sensor_id,
                        "modelo": primeira.get("modelo", sensor_id),
                        "variavel": variavel,
                        "valor": valor,
                        "inicio": texto_timestamp(primeira),
                        "fim": texto_timestamp(ultima),
                        "duracao_segundos": (fim - inicio).total_seconds(),
                        "quantidade_leituras": len(sequencia),
                        "ids_registros": [
                            id_registro(item[2], item[1]) for item in sequencia
                        ],
                    })

            for instante, indice, registro in leituras:
                valor = numero_valido(registro.get(variavel))
                gap = (anterior is not None and
                       (instante - anterior[0]).total_seconds() > LIMITE_GAP_HU2_SEGUNDOS)
                if valor is None or gap or (sequencia and valor != valor_sequencia):
                    fechar_sequencia()
                    sequencia = []
                    valor_sequencia = None

                if valor is not None:
                    if not sequencia:
                        valor_sequencia = valor
                    sequencia.append((instante, indice, registro))
                anterior = (instante, indice)

            fechar_sequencia()

    return trechos


def marcar_suspeitas(registros, min_leituras=MIN_LEITURAS_CONSTANTES):
    """Devolve copias dos registros com "suspeita_travamento" por variavel.

    Todas as leituras do trecho (inclusive as anteriores ao limite) ficam True
    na variavel analisada.
    Nao altera "status_estatistico" nem os valores.
    """
    resultado = copy.deepcopy(registros)
    ids_suspeitos = {variavel: set() for variavel in VARIAVEIS}
    for trecho in detectar_trechos_constantes(
            registros, min_leituras=min_leituras):
        ids_suspeitos[trecho["variavel"]].update(trecho["ids_registros"])

    for indice, registro in enumerate(resultado):
        flags = registro.get("suspeita_travamento")
        if not isinstance(flags, dict):
            flags = {}
        flags = dict(flags)
        for variavel in VARIAVEIS:
            ja_suspeito = flags.get(variavel, False)
            identificador = registro.get("id")
            if identificador is None:
                horario = None
                for campo in ("timestamp", "hora", "Carimbo de data/hora"):
                    valor_horario = registro.get(campo)
                    if isinstance(valor_horario, datetime):
                        valor_horario = valor_horario.isoformat(sep=" ")
                    elif isinstance(valor_horario, str):
                        try:
                            datetime.fromisoformat(valor_horario)
                        except ValueError:
                            try:
                                datetime.strptime(valor_horario, "%d/%m/%Y %H:%M:%S")
                            except ValueError:
                                continue
                    else:
                        continue
                    horario = valor_horario
                    break
                identificador = (
                    f'{registro.get("sensor_id", "sensor")}@{horario}#{indice}'
                )
            flags[variavel] = bool(
                ja_suspeito or identificador in ids_suspeitos[variavel]
            )
        registro["suspeita_travamento"] = flags

    return resultado


# HU3 identificacao de gaps e preenchimento de leituras ausentes.
def _horario_hu3(registro):
    """Pega o horario do registro, mesmo que ele esteja salvo como texto."""
    nomes_coluna = ("timestamp", "hora", "Carimbo de data/hora")

    for campo in nomes_coluna:
        valor = registro.get(campo)
        if isinstance(valor, datetime):
            return valor

        if isinstance(valor, str) and valor.strip():
            try:
                return datetime.fromisoformat(valor)
            except ValueError:
                # Alguns arquivos usam dia/mes/ano em vez de ano-mes-dia.
                try:
                    return datetime.strptime(valor, "%d/%m/%Y %H:%M:%S")
                except ValueError:
                    pass

    return None


def _agrupar_hu3(registros):
    """Separa as leituras por sensor e coloca cada grupo em ordem de horario."""
    grupos = defaultdict(list)

    for registro in registros:
        # uma leitura criada pela HU3 nao deve ser contada como original.
        if registro.get("status") == IMPUTADO:
            continue

        horario = _horario_hu3(registro)
        if horario is not None:
            sensor_id = registro.get("sensor_id")
            grupos[sensor_id].append((horario, registro))

    for leituras in grupos.values():
        leituras.sort(key=lambda item: item[0])

    return grupos


def _numero_hu3(valor):
    """Converte a leitura para numero ou devolve None se ela for invalida."""
    if valor is None or isinstance(valor, bool):
        return None

    try:
        numero = float(valor)
    except (TypeError, ValueError, OverflowError):
        return None

    if not math.isfinite(numero):
        return None
    return numero


def analisar_registros(registros, metodo="ambos"):
    """Analisa os outliers por sensor e variavel e marca cada registro."""
    for registro in registros:
        registro["outlier"] = 0

    for leituras in _agrupar_hu3(registros).values():
        for variavel in VARIAVEIS:
            amostras = [
                registro for _, registro in leituras
                if _numero_hu3(registro.get(variavel)) is not None
            ]
            if not amostras:
                continue

            resultados = analisar_outliers(
                amostras, variavel, metodo, retornar_marcacoes=True
            )
            for indice, registro in enumerate(amostras):
                foi_outlier = any(
                    resultado["marcacoes"][indice]
                    for resultado in resultados.values()
                )
                registro.setdefault("status_estatistico", {})[variavel] = (
                    OUTLIER if foi_outlier else NORMAL
                )
                registro["outlier"] |= int(foi_outlier)

    return registros


def detectar_gaps(registros, limite_segundos=LIMITE_GAP_SEGUNDOS):
    """Lista os intervalos maiores que o permitido entre leituras do sensor."""
    gaps = []
    grupos = _agrupar_hu3(registros)

    for sensor_id, leituras in grupos.items():
        # comecamos na segunda leitura, pois precisamos da anterior.
        for indice in range(1, len(leituras)):
            horario_anterior = leituras[indice - 1][0]
            horario_atual = leituras[indice][0]
            segundos_passados = (horario_atual - horario_anterior).total_seconds()

            if segundos_passados > limite_segundos:
                gaps.append({
                    "sensor_id": sensor_id,
                    "inicio": horario_anterior.isoformat(sep=" "),
                    "fim": horario_atual.isoformat(sep=" "),
                    "duracao_segundos": segundos_passados,
                })

    return gaps


def imputar_ausentes(registros, intervalo_segundos=INTERVALO_PADRAO_SEGUNDOS):
    """Preenche gaps de ate 5 leituras usando as 3 ultimas medias disponiveis."""
    if intervalo_segundos <= 0:
        raise ValueError("O intervalo precisa ser positivo.")

    # trabalhamos com uma copia para nao modificar os dados recebidos.
    resultado = copy.deepcopy(list(registros))
    alertas = []
    grupos = _agrupar_hu3(registros)

    for sensor_id, leituras in grupos.items():
        historico = {variavel: [] for variavel in VARIAVEIS}
        sensor_alertado = False

        for indice, (horario, registro) in enumerate(leituras):
            if indice > 0:
                horario_anterior = leituras[indice - 1][0]
                segundos_passados = (horario - horario_anterior).total_seconds()

                # descontamos 1 porque a leitura do final do intervalo ja existe.
                quantidade_ausentes = max(
                    0, math.ceil(segundos_passados / intervalo_segundos) - 1
                )

                if quantidade_ausentes > MAX_FALHAS_CONSECUTIVAS:
                    # gaps grandes nao sao preenchidos; o alerta e por sensor.
                    if not sensor_alertado:
                        alertas.append({
                            "alerta": SENSOR_INOPERANTE,
                            "sensor_id": sensor_id,
                        })
                        sensor_alertado = True

                elif segundos_passados > LIMITE_GAP_SEGUNDOS:
                    for passo in range(1, quantidade_ausentes + 1):
                        novo_horario = horario_anterior + timedelta(
                            seconds=passo * intervalo_segundos
                        )

                        registro_anterior = leituras[indice - 1][1]
                        modelo = registro.get("modelo", registro_anterior.get("modelo"))
                        novo_registro = {
                            "id": f"{sensor_id}@{novo_horario.isoformat()}",
                            "sensor_id": sensor_id,
                            "modelo": modelo,
                            "timestamp": novo_horario.isoformat(sep=" "),
                            "status": IMPUTADO,
                        }

                        for variavel in VARIAVEIS:
                            # [-3:] seleciona ate os tres ultimos valores validos.
                            ultimos_valores = historico[variavel][-K_MEDIA_MOVEL:]
                            if ultimos_valores:
                                media = sum(ultimos_valores) / len(ultimos_valores)
                            else:
                                media = None

                            novo_registro[variavel] = media
                            if media is not None:
                                # a media criada pode ajudar a preencher a proxima.
                                historico[variavel].append(media)

                        resultado.append(novo_registro)

            # depois do gap, adicionamos a leitura original ao historico.
            for variavel in VARIAVEIS:
                numero = _numero_hu3(registro.get(variavel))
                if numero is not None:
                    historico[variavel].append(numero)

    return {"registros": resultado, "alertas": alertas}


# HU4 relatorio final
def gerar_relatorio(registros, trechos=None, gaps=None):
    """Gera o relatorio de qualidade (dict pronto pra json).

    Recebe registros (originais anotados pela HU1/HU2 e imputados da HU3),
    os trechos da HU2 e os gaps da HU3.
    
    - Nao executa novamente as analises;
    - trechos/gaps omitidos equivalem a listas vazias;
    - Nao altera as entradas;
    - Cada item de registros representa uma leitura;
    - IDs devem ser unicos dentro de cada sensor.
    - Ausencia de classificacao não significa Normal.
    Chaves:
      registros_sinalizados_percentual (float | None), ausencia_de_dados (bool),
      escopo_percentual (str), contagem, por_variavel, trechos,
      gaps, por_sensor, por_modelo.
    """
    # convertendo os dados para listas
    registros = list(registros)
    trechos = list(trechos) if trechos is not None else []
    gaps = list(gaps) if gaps is not None else []
    grupos = {}

    for campo, itens in (("registros", registros), ("trechos", trechos), ("gaps", gaps)):
        for item in itens:
            # cada registro ou evento precisa informar qual sensor pertence
            sensor_id = item.get("sensor_id")
            
            if not isinstance(sensor_id, str) or not sensor_id.strip():
                raise ValueError("Cada registro ou evento precisa de um sensor_id textual.")
            grupo = grupos.setdefault(sensor_id, {
                "modelo": None, "registros": [], "trechos": [], "gaps": [],
            })
            modelo = item.get("modelo")
            
            if modelo is not None:
                # o mesmo sensor nao pode ter modelos diferentes
                if not isinstance(modelo, str) or not modelo.strip():
                    raise ValueError("O modelo precisa ser um texto nao vazio.")
                if grupo["modelo"] not in (None, modelo):
                    raise ValueError(f"Modelos divergentes para o sensor {sensor_id}.")
                grupo["modelo"] = modelo
            grupo[campo].append(item)

    # calcula o resumo geral antes dos resumos individuais
    relatorio = _resumir_qualidade(registros, trechos, gaps)
    relatorio["por_sensor"] = {}
    por_modelo = {}
    
    for sensor_id, grupo in grupos.items():
        resumo = _resumir_qualidade(
            grupo["registros"], grupo["trechos"], grupo["gaps"])
        resumo["modelo"] = grupo["modelo"]
        relatorio["por_sensor"][sensor_id] = resumo
        
        # um evento sem modelo continua no resumo do sensor e no geral
        if grupo["modelo"] is None:
            continue
        
        # junta os dados dos sensores que tem modelo informado
        modelo = por_modelo.setdefault(grupo["modelo"], {
            "sensor_ids": [], "registros": [], "trechos": [], "gaps": [],
        })
        modelo["sensor_ids"].append(sensor_id)
        for campo in ("registros", "trechos", "gaps"):
            modelo[campo].extend(grupo[campo])

    relatorio["por_modelo"] = {}
    
    for modelo, grupo in por_modelo.items():
        resumo = _resumir_qualidade(
            grupo["registros"], grupo["trechos"], grupo["gaps"])
        resumo["sensor_ids"] = grupo["sensor_ids"]
        relatorio["por_modelo"][modelo] = resumo
    
    return relatorio


# FUNÇÕES AUXILIARES
def _resumir_qualidade(registros, trechos, gaps):
    """Aplica as mesmas regras ao conjunto geral, a um sensor ou a um modelo."""
    contagem = {
        "total_registros_originais": 0, NORMAL: 0, OUTLIER: 0,
        "nao_classificados": 0, "registros_suspeita_travamento": 0,
        "registros_sinalizados": 0, "trechos_suspeitos": len(trechos),
        "eventos_gap": len(gaps), IMPUTADO: 0,
    }
    por_variavel = {
        variavel: {NORMAL: 0, OUTLIER: 0, "nao_classificados": 0,
                   "registros_suspeita_travamento": 0, "trechos_suspeitos": 0}
        for variavel in VARIAVEIS
    }
    for registro in registros:
        if registro.get("status") == IMPUTADO:
            contagem[IMPUTADO] += 1
            continue
        contagem["total_registros_originais"] += 1
        estatistica = registro.get("status_estatistico") or {}
        suspeitas = registro.get("suspeita_travamento") or {}
        tem_outlier = False
        tem_suspeita = False
        todas_normais = True
        for variavel, resumo in por_variavel.items():
            status = estatistica.get(variavel)
            suspeita = suspeitas.get(variavel) is True
            if status in (NORMAL, OUTLIER):
                resumo[status] += 1
            else:
                resumo["nao_classificados"] += 1
            resumo["registros_suspeita_travamento"] += int(suspeita)
            tem_outlier = tem_outlier or status == OUTLIER
            tem_suspeita = tem_suspeita or suspeita
            todas_normais = todas_normais and status == NORMAL

        if tem_outlier:
            contagem[OUTLIER] += 1
        elif todas_normais:
            contagem[NORMAL] += 1
        else:
            contagem["nao_classificados"] += 1
        contagem["registros_suspeita_travamento"] += int(tem_suspeita)
        contagem["registros_sinalizados"] += int(tem_outlier or tem_suspeita)

    for trecho in trechos:
        por_variavel[trecho["variavel"]]["trechos_suspeitos"] += 1
    total = contagem["total_registros_originais"]
    return {
        "registros_sinalizados_percentual": (
            contagem["registros_sinalizados"] / total * 100 if total else None),
        "ausencia_de_dados": total == 0,
        "escopo_percentual": (
            "Percentual de registros originais com outlier ou suspeita de "
            "travamento, contados uma unica vez. Imputados sao excluidos; "
            "classificacoes ausentes nao sao presumidas normais."),
        "contagem": contagem,
        "por_variavel": por_variavel,
        "trechos": copy.deepcopy(trechos),
        "gaps": copy.deepcopy(gaps),
    }


# quicksort pro metodo de quantis
def quicksort(arr):
    """Ordena a lista arr usando o algoritmo quicksort."""
    if len(arr) <= 1:
        return arr
    else:
        pivot = arr[len(arr) // 2]
        left = [x for x in arr if x < pivot]
        middle = [x for x in arr if x == pivot]
        right = [x for x in arr if x > pivot]
        return quicksort(left) + middle + quicksort(right)

# importar medições de sensores de um arquivo CSV

def importar_dados_csv(caminho_arquivo):
    """Importa um CSV e usa o nome do arquivo como sensor_id se necessário."""
    sensor_id = Path(caminho_arquivo).stem
    with open(caminho_arquivo, newline='', encoding='utf-8-sig') as csvfile:
        reader = csv.DictReader(csvfile)
        dados = []
        for row in reader:
            if not row.get("sensor_id"):
                row["sensor_id"] = sensor_id
            dados.append(row)
        return dados

def separar_dados_por_sensor_e_variavel(dados, caminho_saida):
    """Separa registros normalizados ou CSVs largos por sensor e variável."""
    dados_separados = defaultdict(list)

    for registro in dados:
        sensor_id = registro.get("sensor_id")
        if not sensor_id:
            raise ValueError("Cada registro precisa de um sensor_id.")

        if "variavel" in registro:
            chave = (sensor_id, registro["variavel"])
            dados_separados[chave].append(registro)
            continue

        campos_horario = ("Carimbo de data/hora", "hora")
        variaveis = ("temperatura", "umidade")
        campos_necessarios = campos_horario + variaveis
        ausentes = [campo for campo in campos_necessarios
                    if campo not in registro]
        if ausentes:
            raise ValueError(
                "Registro CSV largo sem as colunas esperadas: "
                + ", ".join(ausentes)
            )

        for variavel in variaveis:
            campos = [campo for campo in registro
                      if campo in campos_horario or campo == variavel]
            dados_separados[(sensor_id, variavel)].append(
                {campo: registro[campo] for campo in campos}
            )

    for (sensor_id, variavel), registros in dados_separados.items():
        nome_arquivo = os.path.join(
            caminho_saida, f"{sensor_id}_{variavel}.csv"
        )
        with open(nome_arquivo, 'w', newline='', encoding='utf-8') as csvfile:
            fieldnames = registros[0].keys()
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(registros)

# calculo do limiar de grubbs para N amostras e alfa=0,05
def Limiar_grubbs(N, alfa=0.05):
    """Calcula o limiar de Grubbs para N amostras e nivel de significancia alfa."""
    if N < 3:
        raise ValueError("N deve ser pelo menos 3 para o teste de Grubbs.")
    from scipy.stats import t
    t_critico = t.ppf(1 - alfa / (2 * N), N - 2)
    limiar = ((N - 1) / math.sqrt(N)) * math.sqrt(t_critico**2 / (N - 2 + t_critico**2))
    return limiar


class FonteLeituras(ABC):
    """Define como uma fonte entrega os registros ao programa."""

    @abstractmethod
    def carregar(self):
        pass


class AdaptadorCSV(FonteLeituras):
    """Aproveita o importador existente e padroniza a coluna de horario."""

    def __init__(self, caminho):
        self.caminho = caminho

    def carregar(self):
        registros = importar_dados_csv(self.caminho)

        for registro in registros:
            # verifica se o horario esta salvo com outro nome
            if not registro.get("timestamp"):
                horario = registro.get("hora") or registro.get("Carimbo de data/hora")
                # se encontrar, copia para o campo timestamp
                if horario:
                    registro["timestamp"] = horario

        return registros


class AnalisadorFacade:
    """Coordena as etapas do analisador e salva os arquivos de saída."""

    def executar(self, arquivo_csv, metodo, pasta_saida):
        arquivo_csv = Path(arquivo_csv).resolve()
        pasta_saida = Path(pasta_saida).resolve()
        fonte: FonteLeituras = AdaptadorCSV(arquivo_csv) # usando o adaptador para carregar os dados do arquivo
        registros = fonte.carregar()

        for linha, registro in enumerate(registros, start=2):
            registro["id"] = registro.get("id") or str(linha)
            registro["modelo"] = registro.get("modelo") or registro["sensor_id"]

        pasta_saida.mkdir(parents=True, exist_ok=True)
        with chdir(pasta_saida):
            analisar_registros(registros, metodo)
            trechos = detectar_trechos_constantes(registros)
            registros = marcar_suspeitas(registros)
            gaps = detectar_gaps(registros)
            resultado = imputar_ausentes(registros)
            relatorio = gerar_relatorio(resultado["registros"], trechos, gaps)
            relatorio["alertas"] = resultado["alertas"]

        arquivo_csv_saida = pasta_saida / f"{arquivo_csv.stem}_analisado.csv"
        campos = list(dict.fromkeys(
            campo for registro in registros for campo in registro
        ))
        with arquivo_csv_saida.open("w", newline="", encoding="utf-8") as arquivo:
            escritor = csv.DictWriter(arquivo, fieldnames=campos)
            escritor.writeheader()
            escritor.writerows(registros)

        arquivo_json_saida = pasta_saida / f"{arquivo_csv.stem}_relatorio.json"
        arquivo_json_saida.write_text(
            json.dumps(relatorio, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        return arquivo_csv_saida, arquivo_json_saida


def main():
    argumentos = sys.argv[1:]
    if len(argumentos) != 3:
        print("Uso: python analisador.py arquivo_dados.csv metodo saida")
        return 2 # erro de uso

    arquivo_csv, metodo, pasta_saida = argumentos
    metodo = metodo.lower()
    if metodo not in (*_ESTRATEGIAS_OUTLIERS, "ambos"):
        print("Metodo deve ser quartis, grubbs ou ambos.")
        return 2 # erro de uso

    try:
        csv_saida, json_saida = AnalisadorFacade().executar(
            arquivo_csv, metodo, pasta_saida
        )
        print(f"Relatorio JSON salvo em: {json_saida}")
        print(f"CSV analisado salvo em: {csv_saida}")
    except (OSError, ValueError) as erro:
        print(f"Erro: {erro}")
        return 1 # erro de execução

    return 0 # deu bom


if __name__ == "__main__":
    raise SystemExit(main())
