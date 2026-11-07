# Quicksort para o metodo de quantis
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
import csv
import os
from collections import defaultdict
from pathlib import Path

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