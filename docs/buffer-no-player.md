# Buffer no player para tolerância a oscilações de rede

## Objetivo

Reduzir interrupções na reprodução quando houver variações temporárias de rede, mantendo dados suficientes em buffer para que o player continue tocando com estabilidade.

## Problema que isso resolve

Sem buffer, pequenas quedas de throughput, jitter ou latência podem causar:

- pausas frequentes;
- travamentos curtos;
- retomadas manuais desnecessárias;
- pior experiência em redes instáveis.

## Ideia central

O player deve consumir mídia a partir de uma janela antecipada de conteúdo já carregado, em vez de depender apenas do próximo trecho imediato.

Em termos práticos:

- o player baixa alguns segundos antes de reproduzir;
- continua baixando em paralelo enquanto toca;
- só interrompe quando o buffer cai abaixo de um limite mínimo;
- retoma automaticamente quando a reserva volta a um patamar seguro.

## Estratégia sugerida

### 1. Buffer inicial

Antes de iniciar a reprodução, aguardar uma quantidade mínima de mídia carregada.

Isso evita começar a tocar “no limite” e reduz a chance de parada logo após o play.

### 2. Buffer contínuo

Durante a reprodução, manter uma reserva constante de conteúdo adiantado.

O player deve tentar manter essa margem mesmo quando a rede oscila.

### 3. Rebuffer automático

Se a margem cair demais, o player pode pausar temporariamente o consumo até recuperar a reserva.

O objetivo é transformar várias interrupções curtas em uma pausa controlada e menos frequente.

### 4. Buffer adaptativo

O tamanho do buffer pode variar conforme a condição da rede:

- rede estável: buffer menor, com menor latência;
- rede instável: buffer maior, com mais tolerância a falhas;
- reprodução ao vivo: buffer mais curto para evitar atraso excessivo.

### 5. Retry de carregamento

Falhas transitórias de download devem ser tratadas com tentativa de recuperação antes de expor erro ao usuário.

## Onde isso faz mais sentido

### VOD / mídia sob demanda

É o cenário mais favorável para buffer maior, porque atraso adicional costuma ser aceitável.

### Live streaming

Também é viável, mas o buffer precisa ser mais contido para não aumentar demais a latência do ao vivo.

### Player local com mídia remota

Buffer ajuda bastante quando o arquivo depende de acesso em rede ou storage externo.

## Pontos de atenção

- buffer maior melhora estabilidade, mas aumenta latência;
- buffer maior também consome mais memória;
- em conteúdo ao vivo, atraso acumulado pode ser indesejado;
- a solução depende de o protocolo de mídia permitir leitura contínua ou parcial;
- sem controle de estados, a experiência pode oscilar entre reproduzir, pausar e retomar de forma inconsistente.

## Estados úteis

Vale modelar a reprodução com estados explícitos:

- `idle`
- `buffering`
- `playing`
- `rebuffering`
- `paused`
- `error`

Isso facilita decidir quando:

- iniciar;
- interromper temporariamente;
- tentar recuperar;
- expor erro real.

## Critérios para implementação futura

Antes de implementar, vale definir:

- se o caso é live ou on-demand;
- tamanho mínimo do buffer inicial;
- tamanho alvo do buffer durante reprodução;
- limite abaixo do qual o player pausa;
- política de retry para falhas transitórias;
- comportamento quando a conexão não se recupera;
- limite de latência aceitável para o produto.

## Direção recomendada

Para este projeto, a abordagem mais segura é:

- começar com buffer inicial simples e buffer contínuo;
- adicionar rebuffer automático;
- depois evoluir para ajuste adaptativo;
- manter métricas para calibrar os limites reais com uso.

## Resultado esperado

Com essa camada, pequenas oscilações de rede deixam de interromper a reprodução na maioria dos casos, melhorando estabilidade sem exigir recomeço manual do conteúdo.
