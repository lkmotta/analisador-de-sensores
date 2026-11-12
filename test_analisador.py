"""Testes essenciais, ligados aos criterios de aceitacao.

Cobrem as quatro historias, seus fluxos principais e limites relevantes.
Executar: python -m unittest -v test_analisador
"""

import copy
import json
import unittest
from datetime import datetime, timedelta
import analisador as an

INICIO = datetime(2026, 11, 1, 12, 0, 0)

FMT = "%Y-%m-%d %H:%M:%S"

def ts(segundos):
    """timestamp em texto, segundos depois do instante inicial."""
    return (INICIO + timedelta(seconds=segundos)).strftime(FMT)

def reg(seg, temperatura=25.0, umidade=60.0, sensor="S1", modelo="DHT22",
        status=None):
    """Um registro do sensor no instante 'seg'."""
    r = {"id": "%s@%d" % (sensor, seg), "sensor_id": sensor, "modelo": modelo,
         "timestamp": ts(seg), "temperatura": temperatura, "umidade": umidade}
    if status:
        r["status"] = status
    return r

def serie(valores, variavel="temperatura", sensor="S1", modelo="DHT22",
          t0=0, intervalo=10):
    """registros a cada 'intervalo' s
    
    só variavel recebe valores.

    A outra variavel oscila, para nunca formar sequencia constante.
    """
    registros = []
    for i, v in enumerate(valores):
        seg = t0 + i * intervalo
        outra = 40.0 + (i % 7) * 0.5
        par = ({"temperatura": v, "umidade": outra}
               if variavel == "temperatura"
               else {"temperatura": outra, "umidade": v})
        registros.append(reg(seg, sensor=sensor, modelo=modelo, **par))
    return registros

def janela_com_ruido(ultimo, tamanho=21):
    """Janela de 'tamanho' amostras (24, 25, 26 repetidos) + o ultimo valor avaliado."""
    base = [24.0, 25.0, 26.0]
    return [base[i % 3] for i in range(tamanho - 1)] + [ultimo]

def imputados(resultado):
    return [r for r in resultado["registros"] if r.get("status") == "Imputado"]

def anotado(i, sensor="S1", modelo="DHT22", estat_t="Normal", estat_u="Normal",
            susp_t=False, susp_u=False, imputado=False):
    """Registro ja analisado (entrada do relatorio da HU4)."""
    r = reg(i * 10, sensor=sensor, modelo=modelo)
    if imputado:
        r["status"] = "Imputado"
        return r
    r["status_estatistico"] = {"temperatura": estat_t, "umidade": estat_u}
    r["suspeita_travamento"] = {"temperatura": susp_t, "umidade": susp_u}
    return r

def trecho(sensor="S1", modelo="DHT22", variavel="temperatura"):
    return {"sensor_id": sensor, "modelo": modelo, "variavel": variavel,
            "valor": 25.0, "inicio": ts(0), "fim": ts(90),
            "duracao_segundos": 90, "quantidade_leituras": 10,
            "ids_registros": ["%s@%d" % (sensor, s) for s in range(0, 100, 10)]}

class TestHU1Outliers(unittest.TestCase):

    def test_CA1_2_status_outlier_pelo_grubbs(self):
        """CA1.2: valor aceito pelos Quartis mas com G > 2,58 vira 'Outlier'."""
        janela = [-10.0 + i for i in range(20)] + [25.0]
        self.assertFalse(an.outlier_quartis(janela))
        self.assertTrue(an.outlier_grubbs(janela))
        self.assertEqual(an.classificar_estatistica(janela), "Outlier")

    def test_CA1_1_quartis_e_classificacao(self):
        """CA1.1: extremos nas duas direcoes e leitura dentro das barreiras."""
        for valor, esperado in ((200.0, True), (-100.0, True), (25.5, False)):
            with self.subTest(valor=valor):
                janela = janela_com_ruido(valor)
                self.assertEqual(an.outlier_quartis(janela), esperado)
                self.assertEqual(an.classificar_estatistica(janela),
                                 "Outlier" if esperado else "Normal")

    def test_CA1_2_CA1_3_leitura_normal(self):
        """CA1.2/CA1.3: sem violar Quartis ou Grubbs, resultado Normal."""
        janela = list(range(20, 41))
        self.assertFalse(an.outlier_quartis(janela))
        self.assertFalse(an.outlier_grubbs(janela))
        self.assertEqual(an.classificar_estatistica(janela), "Normal")

    def test_CA1_4_janela_constante(self):
        """CA1.3/CA1.4: desvio zero nao causa erro nem outlier estatistico."""
        janela = [25.0] * 21
        self.assertFalse(an.outlier_grubbs(janela))
        self.assertFalse(an.outlier_quartis(janela))
        self.assertEqual(an.classificar_estatistica(janela), "Normal")


class TestHU2PeriodosConstantes(unittest.TestCase):

    def test_CA2_1_sequencias_sao_separadas_por_sensor(self):
        """CA2.1: dois sensores com 6 leituras iguais cada (intercaladas)
        nao se juntam em uma sequencia de 12."""
        s1 = serie([25.0] * 6, sensor="S1")
        s2 = serie([25.0] * 6, sensor="S2")
        intercalado = [r for par in zip(s1, s2) for r in par]
        self.assertEqual(an.detectar_trechos_constantes(intercalado), [])

    def test_CA2_2_limite_e_configuravel(self):
        """CA2.2: MIN_LEITURAS_CONSTANTES pode ser alterado por parametro."""
        registros = serie([25.0] * 5)
        self.assertEqual(an.detectar_trechos_constantes(registros), [])
        self.assertEqual(
            len(an.detectar_trechos_constantes(registros, min_leituras=5)), 1)

    def test_CA2_3_imputados_nao_unem_sequencias_separadas_por_gap(self):
        """CA2.3: 5 originais + 5 imputados iguais + 5 originais iguais não
        formam uma sequencia de 15."""
        antes = serie([25.0] * 5)                                  # 0 a 40
        sinteticos = [reg(s, 25.0, status="Imputado")
                      for s in range(50, 100, 10)]
        depois = serie([25.0] * 5, t0=100)                         # 100 a 140
        self.assertEqual(
            an.detectar_trechos_constantes(antes + sinteticos + depois), [])

    def test_CA2_4_medicoes_e_horarios_sao_preservados(self):
        """CA2.4: a analise nao altera valores nem timestamps de entrada."""
        registros = serie([25.0] * 12)
        original = copy.deepcopy(registros)
        an.detectar_trechos_constantes(registros)
        an.marcar_suspeitas(registros)
        self.assertEqual(registros, original)

    def test_CA2_1_CA2_2_limite_variavel_e_trecho_unico(self):
        """CA2.1/CA2.2: 9 nao bastam; 10 iniciam e 25 ampliam um trecho."""
        for variavel in ("temperatura", "umidade"):
            for quantidade in (9, 10, 25):
                with self.subTest(variavel=variavel, quantidade=quantidade):
                    registros = serie([25.0] * quantidade, variavel=variavel)
                    trechos = an.detectar_trechos_constantes(registros)
                    self.assertEqual(len(trechos), int(quantidade >= 10))
                    if trechos:
                        self.assertEqual(trechos[0]["variavel"], variavel)
                        self.assertEqual(trechos[0]["quantidade_leituras"], quantidade)

    def test_CA2_2_sinalizacao_retroativa_preserva_outlier(self):
        """CA2.2: sinaliza toda a sequencia, sem apagar o resultado da HU1."""
        registros = serie([20.0] + [25.0] * 10 + [22.0])
        registros[4]["status_estatistico"] = {"temperatura": "Outlier",
                                              "umidade": "Normal"}
        resultado = {r["id"]: r for r in an.marcar_suspeitas(registros)}
        for i, registro in enumerate(registros):
            with self.subTest(indice=i):
                flags = resultado[registro["id"]]["suspeita_travamento"]
                self.assertEqual(flags["temperatura"], 1 <= i <= 10)
                self.assertFalse(flags["umidade"])
        self.assertEqual(resultado[registros[4]["id"]]["status_estatistico"],
                         {"temperatura": "Outlier", "umidade": "Normal"})

    def test_CA2_3_interrupcoes_e_reinicio(self):
        """CA2.3: mudanca, ausencia, invalido e gap interrompem a sequencia."""
        cenarios = [(str(valor), serie([25.0] * 5 + [valor] + [25.0] * 10))
                    for valor in (26.0, None, "erro", float("nan"))]
        cenarios.append(("gap", serie([25.0] * 5) + serie([25.0] * 10, t0=100)))
        for motivo, registros in cenarios:
            with self.subTest(motivo=motivo):
                trechos = an.detectar_trechos_constantes(registros)
                self.assertEqual(len(trechos), 1)
                self.assertEqual(trechos[0]["quantidade_leituras"], 10)
                self.assertEqual(trechos[0]["inicio"], registros[-10]["timestamp"])
                self.assertEqual(trechos[0]["fim"], registros[-1]["timestamp"])

    def test_CA2_4_rastreabilidade_em_ordem_cronologica(self):
        """CA2.1/CA2.3/CA2.4: ordenacao, fim do arquivo e campos do trecho."""
        registros = serie([25.0] * 10, sensor="S7", modelo="DS18B20")
        trechos = an.detectar_trechos_constantes(list(reversed(registros)))
        self.assertEqual(len(trechos), 1)
        esperado = trecho(sensor="S7", modelo="DS18B20")
        self.assertEqual({campo: trechos[0][campo] for campo in esperado}, esperado)


class TestHU3GapsEImputacao(unittest.TestCase):

    def test_CA3_1_gap_e_avaliado_por_sensor(self):
        """CA3.1: so o sensor com falha e apontado (S1 regular, S2 com gap)."""
        s1 = [reg(s, sensor="S1") for s in (0, 10, 20, 30)]
        s2 = [reg(s, sensor="S2", modelo="DS18B20") for s in (0, 10, 40)]
        gaps = an.detectar_gaps(s1 + s2)
        self.assertEqual([g["sensor_id"] for g in gaps], ["S2"])

    def test_CA3_4_ate_5_falhas_consecutivas_ainda_e_imputado(self):
        """CA3.4: 5 falhas consecutivas (t = 30..70) ainda sao imputadas."""
        registros = [reg(0, 20.0), reg(10, 22.0), reg(20, 24.0), reg(80, 26.0)]
        res = an.imputar_ausentes(registros)
        self.assertEqual(len(imputados(res)), 5)
        self.assertEqual(res["alertas"], [])

    def test_CA3_4_mais_de_5_falhas_suspende_imputacao_e_alerta(self):
        """CA3.4: 6 falhas consecutivas (t = 30..80): nada e imputado e gera
        o alerta 'Sensor Inoperante'."""
        registros = [reg(0, 20.0), reg(10, 22.0), reg(20, 24.0), reg(90, 26.0)]
        res = an.imputar_ausentes(registros)
        self.assertEqual(imputados(res), [])
        self.assertEqual(len(res["registros"]), 4)
        self.assertEqual([a["alerta"] for a in res["alertas"]],
                         ["Sensor Inoperante"])
        self.assertEqual(res["alertas"][0]["sensor_id"], "S1")

    def test_CA3_4_sensor_inoperante_nao_bloqueia_outros_sensores(self):
        """CA3.4: S1 inoperante nao impede imputar um ausente isolado em S2."""
        s1 = [reg(0, sensor="S1"), reg(10, sensor="S1"), reg(90, sensor="S1")]
        s2 = [reg(0, 20.0, sensor="S2"), reg(10, 22.0, sensor="S2"),
              reg(20, 24.0, sensor="S2"), reg(40, 26.0, sensor="S2")]
        res = an.imputar_ausentes(s1 + s2)
        self.assertEqual([i["sensor_id"] for i in imputados(res)], ["S2"])
        self.assertEqual([a["sensor_id"] for a in res["alertas"]], ["S1"])

    def test_CA3_1_limite_do_gap(self):
        """CA3.1: apenas diferencas superiores a 15 segundos geram gap."""
        for segundos in (10, 15, 16):
            with self.subTest(segundos=segundos):
                gaps = an.detectar_gaps([reg(0), reg(segundos)])
                self.assertEqual(len(gaps), int(segundos > 15))
                if gaps:
                    self.assertEqual(gaps[0]["inicio"], ts(0))
                    self.assertEqual(gaps[0]["fim"], ts(segundos))
                    self.assertEqual(gaps[0]["duracao_segundos"], segundos)

    def test_CA3_2_CA3_3_media_valida_e_marcacao(self):
        """CA3.2/CA3.3: usa as 3 ultimas validas e marca so o sintetico."""
        registros = [reg(0, 100.0, 90.0), reg(10, 20.0, 50.0),
                     reg(20, None, None), reg(30, 22.0, 60.0),
                     reg(40, 27.0, 70.0), reg(60, 30.0, 80.0)]
        resultado = an.imputar_ausentes(registros)
        sinteticos = imputados(resultado)
        self.assertEqual(len(sinteticos), 1)
        novo = sinteticos[0]
        self.assertEqual(novo["timestamp"], ts(50))
        self.assertAlmostEqual(novo["temperatura"], (20.0 + 22.0 + 27.0) / 3)
        self.assertAlmostEqual(novo["umidade"], 60.0)
        self.assertEqual((novo["sensor_id"], novo["modelo"]), ("S1", "DHT22"))
        originais = [r for r in resultado["registros"] if r.get("status") != "Imputado"]
        self.assertEqual(len(originais), len(registros))
        por_id = {r["id"]: r for r in originais}
        for registro in registros:
            for campo, valor in registro.items():
                self.assertEqual(por_id[registro["id"]][campo], valor)


class TestHU4Relatorio(unittest.TestCase):

    def _cenario(self):
        """10 originais e 4 imputados.

        A: Outlier temp   B: Outlier umid   C: suspeita temp
        D: Outlier temp e suspeita umid     E: Outlier nas duas variaveis
        F..J: normais
        """
        originais = [
            anotado(0, estat_t="Outlier"),
            anotado(1, estat_u="Outlier"),
            anotado(2, susp_t=True),
            anotado(3, estat_t="Outlier", susp_u=True),
            anotado(4, estat_t="Outlier", estat_u="Outlier"),
        ] + [anotado(i) for i in range(5, 10)]
        sinteticos = [anotado(i, imputado=True) for i in range(10, 14)]
        return originais + sinteticos

    def test_CA4_2_suspeita_e_independente_do_resultado_estatistico(self):
        """CA4.2: registro Outlier E suspeito na mesma variavel conta nas
        duas contagens (elas podem se sobrepor)."""
        
        rel = an.gerar_relatorio([anotado(0, estat_t="Outlier", susp_t=True)])
        temp = rel["por_variavel"]["temperatura"]
        self.assertEqual(temp["Outlier"], 1)
        self.assertEqual(temp["registros_suspeita_travamento"], 1)

    def _cenario_sensores(self):
        return [
            anotado(0, sensor="S1", modelo="DHT22", estat_t="Outlier"),
            anotado(1, sensor="S1", modelo="DHT22"),
            anotado(2, sensor="S1", modelo="DHT22"),
            anotado(3, sensor="S2", modelo="DS18B20"),
            anotado(4, sensor="S2", modelo="DS18B20"),
            anotado(5, sensor="S3", modelo="DHT22", susp_t=True),
            anotado(6, sensor="S3", modelo="DHT22"),
        ]

    def test_CA4_3_estatisticas_separadas_por_sensor_com_modelo(self):
        """CA4.3: cada sensor tem seu bloco, com o modelo indicado."""
        rel = an.gerar_relatorio(self._cenario_sensores())
        self.assertEqual(set(rel["por_sensor"]), {"S1", "S2", "S3"})
        self.assertEqual(rel["por_sensor"]["S1"]["modelo"], "DHT22")
        self.assertEqual(rel["por_sensor"]["S2"]["modelo"], "DS18B20")
        self.assertEqual(
            rel["por_sensor"]["S1"]["contagem"]["total_registros_originais"], 3)
        self.assertAlmostEqual(
            rel["por_sensor"]["S1"]["registros_sinalizados_percentual"],
            100 / 3, places=2)
        self.assertEqual(
            rel["por_sensor"]["S2"]["registros_sinalizados_percentual"], 0.0)

    def test_CA4_3_gaps_e_trechos_sao_atribuidos_ao_sensor_certo(self):
        """CA4.3: eventos de gap e trechos aparecem so no sensor de origem."""
        gap = {"sensor_id": "S2", "inicio": ts(0), "fim": ts(30),
               "duracao_segundos": 30}
        rel = an.gerar_relatorio(self._cenario_sensores(),
                                 trechos=[trecho(sensor="S3")], gaps=[gap])
        self.assertEqual(rel["por_sensor"]["S2"]["contagem"]["eventos_gap"], 1)
        self.assertEqual(rel["por_sensor"]["S1"]["contagem"]["eventos_gap"], 0)
        self.assertEqual(
            rel["por_sensor"]["S3"]["contagem"]["trechos_suspeitos"], 1)

    def test_CA4_1_CA4_2_CA4_4_relatorio_consolidado(self):
        """CA4.1/CA4.2/CA4.4: percentual sem duplicacao, contagens e JSON."""
        gap = {"sensor_id": "S1", "inicio": ts(0), "fim": ts(30),
               "duracao_segundos": 30}
        
        rel = an.gerar_relatorio(self._cenario(), trechos=[trecho()], gaps=[gap])
        self.assertAlmostEqual(rel["registros_sinalizados_percentual"], 50.0)
        self.assertFalse(rel["ausencia_de_dados"])
        
        esperado = {"total_registros_originais": 10, "registros_suspeita_travamento": 2,
                    "trechos_suspeitos": 1, "eventos_gap": 1, "Imputado": 4,
                    "Outlier": 4, "Normal": 6, "registros_sinalizados": 5,
                    "nao_classificados": 0}
        
        for campo, valor in esperado.items():
            self.assertEqual(rel["contagem"][campo], valor)
        for variavel, outliers, suspeitas, trechos in (
                ("temperatura", 3, 1, 1), ("umidade", 2, 1, 0)):
            with self.subTest(variavel=variavel):
                bloco = rel["por_variavel"][variavel]
                self.assertEqual((bloco["Outlier"], bloco["Normal"]), (outliers, 10-outliers))
                self.assertEqual(bloco["registros_suspeita_travamento"], suspeitas)
                self.assertEqual(bloco["trechos_suspeitos"], trechos)
                
        self.assertEqual(rel["trechos"], [trecho()])
        self.assertIn("outlier", rel["escopo_percentual"].lower())
        self.assertIn("travamento", rel["escopo_percentual"].lower())
        self.assertIsInstance(rel, dict)
        self.assertEqual(json.loads(json.dumps(rel, allow_nan=False)), rel)

    def test_CA4_1_CA4_4_sem_originais(self):
        """CA4.1/CA4.4: sem originais, percentual null e ausencia de dados."""
        for registros in ([], [anotado(0, imputado=True)]):
            with self.subTest(somente_imputados=bool(registros)):
                rel = json.loads(json.dumps(an.gerar_relatorio(registros)))
                self.assertIsNone(rel["registros_sinalizados_percentual"])
                self.assertTrue(rel["ausencia_de_dados"])

    def test_CA4_1_imputados_sinalizados_nao_afetam_estatisticas(self):
        """CA4.1/CA4.2: anotacoes copiadas para imputados nao entram no calculo."""
        sintetico = anotado(1, estat_t="Outlier", susp_t=True, susp_u=True)
        sintetico["status"] = "Imputado"
        rel = an.gerar_relatorio([anotado(0), sintetico])
        
        for resumo in (rel, rel["por_sensor"]["S1"], rel["por_modelo"]["DHT22"]):
            self.assertEqual(resumo["registros_sinalizados_percentual"], 0.0)
            self.assertEqual(resumo["contagem"]["Imputado"], 1)
            self.assertEqual(resumo["contagem"]["registros_sinalizados"], 0)
            for variavel in an.VARIAVEIS:
                self.assertEqual(resumo["por_variavel"][variavel]["Outlier"], 0)
                self.assertEqual(
                    resumo["por_variavel"][variavel]["registros_suspeita_travamento"], 0)

    def test_CA4_2_ausencia_de_classificacao_nao_significa_normal(self):
        """CA4.2: originais sem resultado continuam no total, sem inventar Normal."""
        registros = [reg(0), anotado(1, estat_u=None, susp_t=True)]
        rel = an.gerar_relatorio(registros)
        self.assertEqual(rel["contagem"]["total_registros_originais"], 2)
        self.assertEqual(rel["contagem"]["Normal"], 0)
        self.assertEqual(rel["contagem"]["nao_classificados"], 2)
        self.assertEqual(rel["por_variavel"]["temperatura"]["Normal"], 1)
        self.assertEqual(rel["por_variavel"]["umidade"]["nao_classificados"], 2)
        self.assertEqual(rel["registros_sinalizados_percentual"], 50.0)

    def test_CA4_3_resumo_modelo_usa_totais_e_identifica_sensores(self):
        """CA4.3: modelo usa 2/5 originais, sem tirar media dos percentuais."""
        rel = an.gerar_relatorio(self._cenario_sensores())
        modelo = rel["por_modelo"]["DHT22"]
        self.assertEqual(modelo["sensor_ids"], ["S1", "S3"])
        self.assertEqual(modelo["contagem"]["total_registros_originais"], 5)
        self.assertEqual(modelo["registros_sinalizados_percentual"], 40.0)
        self.assertEqual(rel["por_modelo"]["DS18B20"]["sensor_ids"], ["S2"])

    def test_CA4_3_sensores_sem_originais_preservam_eventos(self):
        """CA4.1/CA4.3: sensor so com evento ou imputado fica com percentual null."""
        gap = {"sensor_id": "S2", "inicio": ts(0), "fim": ts(30),
               "duracao_segundos": 30}
        rel = an.gerar_relatorio([anotado(0, imputado=True)],
                                 trechos=[trecho(sensor="S3")], gaps=[gap])
        
        for sensor in ("S1", "S2", "S3"):
            self.assertTrue(rel["por_sensor"][sensor]["ausencia_de_dados"])
            self.assertIsNone(rel["por_sensor"][sensor]["registros_sinalizados_percentual"])
            
        self.assertIsNone(rel["por_sensor"]["S2"]["modelo"])
        self.assertEqual(rel["por_sensor"]["S2"]["gaps"], [gap])
        self.assertEqual(rel["por_modelo"]["DHT22"]["contagem"]["trechos_suspeitos"], 1)
        self.assertEqual(json.loads(json.dumps(rel, allow_nan=False)), rel)

    def test_CA4_4_entradas_e_resumos_nao_compartilham_eventos_mutaveis(self):
        """CA4.4: consultar ou editar a saida nao altera os dados analisados."""
        registros, trechos = self._cenario(), [trecho()]
        gaps = [{"sensor_id": "S1", "inicio": ts(0), "fim": ts(30),
                 "duracao_segundos": 30}]
        
        original = copy.deepcopy((registros, trechos, gaps))
        rel = an.gerar_relatorio(registros, trechos=trechos, gaps=gaps)
        self.assertEqual((registros, trechos, gaps), original)
        rel["trechos"][0]["ids_registros"].clear()
        rel["gaps"][0]["fim"] = "alterado"
        
        self.assertEqual((registros, trechos, gaps), original)
        self.assertEqual(rel["por_sensor"]["S1"]["trechos"], trechos)
        self.assertEqual(rel["por_modelo"]["DHT22"]["gaps"], gaps)

    def test_CA4_2_integracao_com_trechos_e_marcacoes_da_HU2(self):
        """CA4.2/CA4.4: consome os trechos reais da HU2 sem reanalisa-los."""
        registros = serie([25.0] * 10)
        for registro in registros:
            registro["status_estatistico"] = dict.fromkeys(an.VARIAVEIS, "Normal")
        rel = an.gerar_relatorio(an.marcar_suspeitas(registros),
                                 trechos=an.detectar_trechos_constantes(registros))
        
        self.assertEqual(rel["registros_sinalizados_percentual"], 100.0)
        self.assertEqual(rel["contagem"]["Normal"], 10)
        self.assertEqual(rel["contagem"]["trechos_suspeitos"], 1)
        self.assertEqual(rel["trechos"], [trecho()])

    def test_CA4_3_identificacao_invalida_nao_mistura_sensores(self):
        """CA4.3: rejeita ID ausente e modelos conflitantes para o mesmo sensor."""
        for registros in ([anotado(0, sensor=None)], [anotado(0, sensor="")],
                          [anotado(0), anotado(1, modelo="DS18B20")]):
            
            with self.subTest(registros=registros):
                with self.assertRaises(ValueError):
                    an.gerar_relatorio(registros)


if __name__ == "__main__":
    unittest.main(verbosity=2)
