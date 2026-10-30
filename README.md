# analisador-de-sensores
Projeto Final de Engenharia de Software: Analisador de qualidade de dados de sensores (temperatura/umidade)

## Sprint 1 - Proposta

### Integrantes
Dirceu Morais da Costa Júnior 
Felipe Kenzo Higashi          
Lucas Henrique Motta          

### Descrição

O sistema recebe um .csv na entrada com leituras de temperatura e umidade exportads de sensores (por exemplo, ESP32).
Ele analisa os dados em etapas com responsabilidades distintas (leitura, validação e análise) e detecta quatro tipos de problema:
- valores fora da faixa física esperada
- lacunas de tempo entre leituras
- leituras travadas (valor repetido sem variação).

A saída é um relatório de confiabilidade do sensor, indicando quantos problemas foram achados, onde ocorreram e um resumo geral da qualidade dos dados.

O público alvo são estudantes de engenharia que usam Arduino/ESP32 em projetos e pequenos produtores com estufas, que usam os sensores sem ter como verificar se os dados são confiáveis.

### Pesquisas que justificam

#### Fonte 1: artigo brasileiro com sensores de temperatura

USBERT, Eduardo Esmínio; AMARAL, Allan Francisco Forzza; CARVALHO, Victório Albani de. **Aplicação de Técnicas de Qualidade da Informação em Sensores na Internet das Coisas (IoT)**. *Revista de Informação e Comunicação (RIC)*, Instituto Federal do Espírito Santo, p. 01-18, 2021. Disponível em: https://ojs.ifes.edu.br/index.php/ric/article/view/769. Acesso em: 30 out. 2026.

**Resumo e relevância:** O artigo investiga a qualidade dos dados de sensores em infraestruturas de IoT, contexto análogo ao de projetos com Arduino.

Os autores afirmam que não eliminar outliers distorce estatísticas como média e variância e pode disparar alertas falsos.

Um analisador que aponte valores fora de faixa, lacunas, leituras travadas e outliers entrega justamente a triagem que o artigo mostra ser necessária.

#### Fonte 2: revisão sistemática (acesso aberto)

TEH, Hui Yie; KEMPA-LIEHR, Andreas W.; WANG, Kevin I-Kai. **Sensor data quality: a systematic review**. *Journal of Big Data*, p. 1-49, fev. 2020. Disponível em: https://journalofbigdata.springeropen.com/articles/10.1186/s40537-020-0285-1. Acesso em: 30 out. 2026.

**Resumo e relevância:** Os autores concluem que os erros mais tratados na literatura são dados faltantes e falhas como outliers, bias (desvio sistemático) e drift (deriva ao longo do tempo), e afirmam que aplicações IoT se tornam inúteis quando a qualidade dos dados é ruim.

Para o nosso projeto, isso confirma que os problemas que o analisador detecta são os erros mais estudados em dados de sensores.
