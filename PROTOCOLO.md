# Protocolo do controlador ENE K5130 (PHN16S-71)

## Dispositivo

| Campo | Valor |
|---|---|
| Nó ACPI | `ENEK5130` (dispositivo HID I2C) |
| VID / PID / versão | `0CF2` / `5130` / `0001` |
| Usage Page / Usage | `0xFF5A` / `0x01` |
| Input / Output / Feature | 0 / 0 / **11 bytes** |
| Linux | `/dev/hidrawN` com `HID_ID=0018:00000CF2:00005130`; `ioctl` `HIDIOCGFEATURE(11)` / `HIDIOCSFEATURE(11)`, sem root com o grupo `input` |

## Relatórios (feature reports de 11 bytes)

| Report | Direção | Formato |
|---|---|---|
| `0xA1` | leitura | alvos: `[A1, qtd, alvo1, alvo2, ...]` |
| `0xA2` | escrita | seleciona alvo para a próxima leitura A3: `[A2, alvo, 0...]` |
| `0xA3` | leitura | capacidades: `[A3, alvo, ?, classe, qtd_zonas, bitmap_modos (u32 LE)]` |
| `0xA4` | escrita | iluminação: `[A4, alvo, modo, brilho 0-100, velocidade 0-9, direção, R, G, B, zonas_lo, zonas_hi]` |

## Leituras reais deste notebook (15/09/2026, BIOS V1.28)

```
A1: A1 03 65 21 83 00 00 00 00 00 00
    -> 3 alvos: 0x65 botão de modo, 0x21 teclado, 0x83 logo da tampa
A3 (0x21): A3 21 01 09 04 FB 07 00 00 00 00
    -> classe 0x09 (teclado), 4 zonas, bitmap de modos 0x07FB
```

## Alvos

| Alvo | Dispositivo |
|---|---|
| `0x21` | teclado (4 zonas, máscara `0x0F`) |
| `0x83` | logo da tampa |
| `0x65` | LED do botão de modo / turbo (1 zona) ✅ testado em 16/09/2026 |

## Modos (projeto de referência)

| Código | Modo | Situação no PHN16S-71 |
|---|---|---|
| `0x02` | Estático | ✅ funciona |
| `0x04` | Respiração | ✅ funciona (no projeto de referência pode ignorar a cor e ciclar arco-íris) |
| `0x05` | Neon | ✅ funciona |
| `0x07` | Onda | ✅ funciona |
| `0x09` | Zoom | ✅ funciona |
| `0x0A` | Meteoro | ✅ funciona |
| `0x01`, `0x06`, `0x08`, `0x0B` | ? | aceitos pelo bitmap de capacidades do teclado (`0x07FB`), ainda não testados |

Direção (byte 5) no teclado do PHN16S-71: `0x00` neutro, `0x01` esquerda→direita, `0x02` direita→esquerda. ✅ funciona.

**Cor nos efeitos:** pelo uso do app (15/09/2026), só o modo **Estático** respeita a cor enviada (bytes R, G, B) e as zonas. Nos efeitos animados (respiração, neon, onda, zoom, meteoro) o hardware usa as cores dele. O app só mostra cor e zonas no Estático, e velocidade e direção só nos efeitos.

**Teste visual de 15/09/2026 (BIOS V1.28), confirmado pelo usuário:**

| # | Pacote enviado | Resultado |
|---|---|---|
| 1 | `A4 21 04 64 03 00 FF 00 00 0F 00` (Respiração vermelha, vel. 3) | ✅ |
| 2 | `A4 21 05 64 05 00 FF 00 00 0F 00` (Neon, vel. 5) | ✅ |
| 3 | `A4 21 07 64 05 00 FF 00 00 0F 00` (Onda, direção neutra) | ✅ |
| 4 | `A4 21 07 64 05 01 FF 00 00 0F 00` (Onda, esquerda→direita) | ✅ |
| 5 | `A4 21 07 64 05 02 FF 00 00 0F 00` (Onda, direita→esquerda) | ✅ |
| 6 | `A4 21 09 64 05 00 00 FF 00 0F 00` (Zoom verde) | ✅ |
| 7 | `A4 21 0A 64 05 00 00 00 FF 0F 00` (Meteoro azul) | ✅ |
| — | Zonas individuais (`0x01` verde, `0x02` azul, `0x04` branco, `0x08` amarelo) | ✅ cada zona com a sua cor |

## Logo da tampa (alvo `0x83`)

Capacidades lidas (`A3 83 01 05 01 3B 00 ...`): classe `0x05`, **1 zona** (máscara `0x01`), modos `0x01, 0x02, 0x04, 0x05, 0x06`.
O botão de modo (alvo `0x65`) informa as mesmas capacidades e usa o mesmo formato de pacote (ver seção abaixo).

No logo o byte 5 é uma flag, não uma direção: `0x01` = estático, `0x02` = efeito.

**Teste visual de 15/09/2026, confirmado pelo usuário:**

| # | Pacote enviado | Resultado |
|---|---|---|
| 1 | `A4 83 02 64 00 01 00 00 FF 01 00` (azul estático, brilho 100) | ✅ |
| 2 | `A4 83 02 1E 00 01 00 FF 00 01 00` (verde estático, brilho 30) | ✅ mais fraco |
| 3 | `A4 83 02 00 00 01 00 FF 00 01 00` (brilho 0) | ✅ apagado |
| 4 | `A4 83 04 64 03 02 FF FF FF 01 00` (respiração branca, vel. 3) | ✅ |
| 5 | `A4 83 05 64 05 02 FF FF FF 01 00` (neon, vel. 5) | ✅ |
| — | `A4 83 02 64 00 01 FF 00 00 01 00` (vermelho estático) | ✅ |

## LED do botão de modo (alvo `0x65`)

Capacidades lidas (`A3 65 01 05 01 3B 00 ...`): iguais às do logo. Classe `0x05`, **1 zona** (máscara `0x01`), modos `0x01, 0x02, 0x04, 0x05, 0x06`.
Mesmo formato de pacote do logo: o byte 5 é flag (`0x01` = estático, `0x02` = efeito), não direção.

**Teste visual de 16/09/2026 (BIOS V1.28), confirmado pelo usuário.** Enviado por HID direto:

| # | Pacote enviado | Resultado |
|---|---|---|
| 1 | `A4 65 02 64 00 01 FF 00 00 01 00` (vermelho estático, brilho 100) | ✅ |
| 2 | `A4 65 02 64 00 01 00 FF 00 01 00` (verde estático) | ✅ |
| 3 | `A4 65 02 64 00 01 00 00 FF 01 00` (azul estático) | ✅ |
| 4 | `A4 65 02 0F 00 01 00 00 FF 01 00` (azul, brilho 15) | ✅ mais fraco |
| 5 | `A4 65 04 64 03 02 FF 00 00 01 00` (respiração, vel. 3) | ✅ |
| 6 | `A4 65 05 64 05 02 FF 00 00 01 00` (neon, vel. 5) | ✅ |
| 7 | `A4 65 02 00 00 01 00 00 00 01 00` (brilho 0) | ✅ apagado |
| 8 | Regravar o modo de desempenho atual (`0x04`) | ✅ o firmware devolveu a cor padrão do botão |

**Cuidado:** o firmware controla a cor desse LED conforme o modo de desempenho. Regravar o modo (inclusive o mesmo) devolve a cor padrão, então uma cor personalizada no botão pode ser sobrescrita a cada troca de modo. Por isso o `predator` reaplica o estado do Claude depois de cada troca de modo.

## Comando que funcionou

```
A4 21 02 64 00 00 FF 00 00 0F 00
│  │  │  │  │  │  └──┴──┘ │  └ zonas_hi
│  │  │  │  │  │  R  G  B  └ zonas_lo = 0x0F (4 zonas)
│  │  │  │  │  └ direção 00 (neutra)
│  │  │  │  └ velocidade 0
│  │  │  └ brilho 0x64 = 100
│  │  └ modo 02 = estático
│  └ alvo 21 = teclado
└ report A4
```

Resultado: teclado vermelho imediatamente, com todos os serviços da Acer parados.

---

# Modo de desempenho, timeout e bateria (sysfs)

| Função | Caminho | Valores |
|---|---|---|
| Modo (kernel `acer_wmi predator_v4=1`) | `/sys/firmware/acpi/platform_profile` | `low-power` Eco, `quiet` Silencioso, `balanced` Equilibrado, `balanced-performance` Desempenho, `performance` Turbo |
| Modo (módulo `facer`) | `/sys/devices/platform/acer-wmi/thermal_profile` | códigos do firmware: `0x00` Silencioso, `0x01` Equilibrado, `0x04` Desempenho, `0x05` Turbo, `0x06` Eco; `thermal_profile_supported` = `0x73` |
| Apagar o teclado após 30 s (só `facer`) | `/sys/devices/platform/acer-wmi/backlight_timeout` | `1` / `0` |
| Limite de carga em 80% | `/sys/bus/wmi/drivers/acer-wmi-battery/health_mode` | `1` / `0` |
| RPM | hwmon `acer`: `fan1_input` (CPU) / `fan2_input` (GPU) | RPM |
| Ventoinhas Auto/Max | sem interface: o `facer` só cria `pwm*_enable` com o quirk `pwm`, que não vale para o PHN16S-71 | — |

Depois de gravar o modo, o `predator` lê de volta até 4 vezes, com 80 ms de intervalo, e só considera aplicado quando o modo lido é igual ao pedido.

**Leitura em 13/10/2026 (kernel 7.2.5, facer DKMS):** A1 = `A1 03 65 21 83`, A3 iguais às da tabela acima. ✅ Gravação de teclado (estático, zonas, onda), logo e botão ✅.
