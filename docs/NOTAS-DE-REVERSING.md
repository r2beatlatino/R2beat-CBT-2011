# Notas de reversing

Cómo se averiguó cada cosa: direcciones dentro del ejecutable, mediciones en vivo,
hipótesis que resultaron falsas y bisects.

Esto vivía como comentarios en el código y se movió acá por dos razones. Una: a
quien sólo quiere levantar un servidor no le sirve de nada y le tapa lo que el
código hace. Dos: a quien sí quiere seguir investigando le sirve más junto que
desparramado.

Cada sección tiene un ancla; en el código quedó el puntero al lado de la línea que
explica.

> Las direcciones (`0x...`) son del ejecutable del cliente CBT 2011. En otras
> versiones del juego no coinciden.



---

## ajustes.py

### <a name="ajustes-bis-el-ff49-tambi-n-vaciaba-el-inventario"></a>(bis) — EL FF49 TAMBIÉN VACIABA EL INVENTARIO

```
# ★★ 2026-08-02 (bis) — EL FF49 TAMBIÉN VACIABA EL INVENTARIO. Es el MISMO bug que la música, en
#   el bloque de al lado: el handler `0x577640` arranca llamando a `0x456270`, que en SUS DOS RAMAS
#   resetea el contenedor `record+0x158` (`0x43d4f0` / `0x455d40`) — y `0x158` es justo donde el N2
#   inserta (`0x458e00`). Mandábamos `N2=0` ⇒ reset sin reposición.
#   Se veía sólo al CAMBIAR DE CANAL por la misma razón que el picker: ahí el cliente PIDE el FF49
#   ~525 ms DESPUÉS del joinchannel, así que llega DESPUÉS del MyRecord que lo había poblado (#37).
#   ⚠ El comentario viejo decía "N2 no se limpia antes, poblarlo duplicaría". Era un diagnóstico
#     equivocado: `0x4592f0` es un push_back, sí, pero el reset ya ocurrió al entrar al handler.
#   R2_FF49_INV=0 vuelve a mandar N2=0 (el comportamiento anterior).
```

### <a name="ajustes-ff49-subsistema-de-notificaci-n-del-client"></a>FF49 — subsistema de NOTIFICACIÓN del cliente (handler 0x577640)

```
# ★★ FF49 — subsistema de NOTIFICACIÓN del cliente (handler 0x577640). El cliente lo pide al entrar
#   al canal; contestarlo (a) deja `idx8c` (=[0x766640]+0x8c) VÁLIDO vía SelectByKey y (b) dispara el
#   "sonidito" del cambio de servidor. Una fila es VISIBLE sólo si key < 10^7 y key%1000 ∈ {900,901}.
#   R2_FF49=0 lo desactiva.
#   ⚠ El PAYLOAD lo arma `LobbyProtocol.build_ff49_roster()` — NO hay una versión suelta acá.
#     Hubo una (keys `[1900,2900,3901]`, copiada de la copia experimental) y era el bug: no incluía
#     al personaje, así que `idx8c` quedaba apuntando a una fila de canal. Se borró a propósito.
```

### <a name="ajustes-cerrar-el-socket-de-login-tras-el-re-login"></a>CERRAR EL SOCKET DE LOGIN TRAS EL RE-LOGIN DE SWITCH DE CANAL

```
# ★★★★★ 2026-08-02 — CERRAR EL SOCKET DE LOGIN TRAS EL RE-LOGIN DE SWITCH DE CANAL.
#   SÍNTOMA: se podía entrar a un canal, pero después el clic de "cambiar de canal" NO HACÍA NADA
#   (ni cartel, ni sonido). Medido con Frida (tools/frida/trazar_canal.py):
#     el botón OK (0x46AB10) arranca con   `cmp byte [[0x769C28]+0x17780],0 ; jne salir`
#     esa bandera la PRENDE  0x56FCC1 = el handler del LOGIN OK (FF01)
#     y la APAGA             0x5701A0 = "LoginServer : <OnClose>"
#   ⇒ la bandera significa "la sesión con el LoginServer sigue ABIERTA", y el cliente sólo deja
#     cambiar de canal cuando el server CERRÓ ese socket.
#   POR QUÉ FALLABA: en el login INICIAL el cliente pide la lista (FF02) y ahí cerrábamos ✓. En el
#   RE-LOGIN del switch el cliente NO pide la lista (ya sabe a qué canal va) ⇒ nunca llegábamos al
#   cierre ⇒ la bandera quedaba en 1 para siempre y todo clic posterior moría en la primera línea.
#   Es el mismo mecanismo de los "dos cierres de socket" del cliente 2013, con otros offsets.
#   R2_RELOGIN_CLOSE=0 vuelve atrás.
```


---

## canciones.py

### <a name="canciones-ff4e-movement-track-fase-2-movimiento-re"></a>── FF4E MOVEMENT TRACK (fase 2) — movimiento REAL capturado (raw, ya sin magic) para el RE...

```
# ── FF4E MOVEMENT TRACK (fase 2) — movimiento REAL capturado (raw, ya sin magic) para el REPLAY de NPCs en pista ──
# ★★ 2026-08-01 — APAGADO POR DEFECTO (era "1"). El replay reproduce UN INTENTO GRABADO (ff4e_track.jsonl) y al
#   terminarlo llama a `_npc_finished` -> dispara el contador de 5s de la sala. Como el track dura lo que duraba
#   LA CANCIÓN EN QUE SE GRABÓ, en una canción más larga el contador salta a mitad de carrera y **el humano queda
#   registrado DNF (frame 0xFFFFFFFF)** aunque esté jugando bien. Eso es lo que ensuciaba el cartel.
#   Además el replay es VERBATIM (no remapea el slot) -> los NPC emiten las MISMAS coords = se mueve uno solo.
#   R2_NPC_MOVE=1 lo reactiva a propósito. `_npc_finished` NO tiene otro call-site: con esto apagado, el contador
#   lo dispara SOLO un finalista humano real.
```

### <a name="canciones"></a>══════════════════════════════════════════════════════════════════════════════

```
# ══════════════════════════════════════════════════════════════════════════════
# NPC AUTOPLAY (paso 1) — el NPC JUEGA el chart REAL de la canción, como el `.npc`
#   del R2Server 2005/VB6 (función AutoPlay @0x4673fe: recorre las notas reales y
#   modula el desempeño con Speed/Accuracy/Stability). Aquí generamos el SCORE FINAL
#   del NPC (combo/pd/star/frame) a partir del nº de notas de la canción + su perfil,
#   en vez de números fijos. El MOVIMIENTO visible en pista (FF4E) es la fase 2.
#   Determinista por (song_id, slot) -> el mismo NPC rinde igual al re-armar el cartel.
# ══════════════════════════════════════════════════════════════════════════════
# ★★ 2026-08-01 — APAGADO POR DEFECTO (era "1"). Con autoplay, `npc_autoplay_score` le daba a los NPC frames de
#   meta de 2400-4700 mientras un humano REAL cruza en ~7800 => los NPC le ganaban SIEMPRE al jugador: llegabas
#   1º y el cartel te ponía 5º. Con esto apagado los NPC usan el score plano (frame 100000+) y quedan SIEMPRE
#   detrás de cualquier humano que termine. R2_NPC_AUTOPLAY=1 lo reactiva.
```

### <a name="canciones-diagn-stico-r2-sim-race-1-perfiles-de-np"></a>DIAGNÓSTICO (R2_SIM_RACE=1): perfiles de NPC que imitan una carrera real de 6 (mezcla de f...

```
# ★ DIAGNÓSTICO (R2_SIM_RACE=1): perfiles de NPC que imitan una carrera real de 6 (mezcla de finalistas y fallados
#   con frame=0xFFFFFFFF+combo real, pd/estrellas variadas). Para reproducir el cartel del VPS en 1 sola PC.
#   frame=0xFFFFFFFF = "fallida" (no cruzó meta).
#   ⚠ El campo "rank" de ESTOS perfiles NO SE USA (el consumidor solo lee frame/combo/pd/star y el puesto lo
#     recalcula por orden de llegada). Quedó escrito 1-based y NO se migró al 0-based del 2026-08-01: es texto
#     muerto, no la fuente de verdad. Para el rank real ver _encode_result.
```

### <a name="canciones-stages-pose-dos-del-usuario-picker-de-st"></a>STAGES POSEÍDOS del usuario (picker de STAGE, análogo a SONGS)

```
# STAGES POSEÍDOS del usuario (picker de STAGE, análogo a SONGS). Los stageids salen de
# rnr_script/stage_lt.txt (atributo Index="N"). El rebuild del picker (0x5670d0, loop 0x5673c9)
# itera el vector player[0x766640]+0x4d0 (vector<u16>), valida stageid < 0x4000, y hace lookup
# en la DB de stages [0x76663c] (call 0x40fba0); si existe, lo muestra. El cliente PRE-SIEMBRA
# el stage 0 (Random) solo (push 0; call 0x458650 en 0x589eed), así que no es obligatorio.
# OJO: stage_lt.txt tiene 22 Index pero **100/101/102/103 tienen Image="" (vacío) sin modelo** =
# placeholders -> el rebuild del picker CRASHEA al cargar el thumbnail vacío. Y 25 = CENTINELA
# (0x19, el handler lo saltea, no entra al vector). Por eso solo mandamos los stages con
# imagen+modelo REALES (1-21). Si la beta mostraba menos (4-7), recortar más.
# + 58 stages de blade integrados 2026-07-07 (indices nativos libres; ver stage_lt.txt).
# Todos con Image+Model reales (evitan 25=centinela y 100-103=placeholders vacios).
```


---

## catalogo.py

### <a name="catalogo-alas-en-sala-re-del-handler-0x5863f0-0x56"></a>ALAS EN SALA (, RE del handler 0x5863F0 + 0x565F50)

```
# ★★ ALAS EN SALA (2026-07-25, RE del handler 0x5863F0 + 0x565F50). MISMO patrón que la cara: el campo
#   nativo existe y lo mandábamos en 0. Aquí el campo es **r22** (u32) del registro FF0C = el `Wing.Id`.
#   Cadena VERIFICADA en el exe prístino:
#     FF0C r22 (read 0x586640) -> desc+0x34 -> 0x56645F -> 0x473710 SetWingById(holder=occ+0x1B4, wingId)
#     0x473710 recorre el wing-manager [0x766804]+0xc8..+0xdc y matchea contra **Wing.Id** (nodo+8);
#     si matchea -> FxEffect (nodo+0x12) -> attach 0x472280; si no -> detach 0x472F30.
#   ★Esa ruta es PER-OCUPANTE y NO consulta el inventario local -> sirve para los avatares ajenos.
#   El gate 0x566449 (`[0x766618]+0x6d90 ^ 0x1fef`) elige rama: en SALA el flag vale 0x1fef (lo setea
#   CREATEROOM 0x584F89 / FF61 0x5885B0) -> cae en 0x473710 = la rama BUENA. En PRÁCTICA vale 0x1fee
#   (0x464637) -> usa 0x4734F0, que escanea el inventario LOCAL (por eso ahí el ala sí se veía).
#   ⚠NO tocar el flag 0x6d90: ponerlo en 0x1fee rompería las alas de ocupantes Y la cara (0x5371D2 y
#   0x53CD7A pisan occ+0x1A1 con 0x454900(charId) cuando el flag != 0x1fef).
#   ⚠El valor es el **Id CHICO** del wing.txt (2,4,5,…), NO el ItemCode ni el ItemEffect.
```

### <a name="catalogo-cara-piel-en-sala-item-de-cara-faceindex-"></a>CARA/PIEL EN SALA: item de cara -> faceIndex (carpeta rnr_char/face/NN/)

```
# ★ CARA/PIEL EN SALA: item de cara -> faceIndex (carpeta rnr_char/face/NN/). Cadena EXACTA que
#   usa el cliente:  item Index --(ItemEffect, shop_lt.txt)--> ItemEffect --(face.txt)--> Face Index.
#   El faceByte = ese faceIndex PLANO (validado client-side por 0x4c5670/0x4c4a00; el índice debe
#   existir como carpeta rnr_char/face/NN en el pak). Ver memoria r2beat-cara-sala.
#
#   ★2026-07-25: ANTES era un dict HARDCODEADO con las 4 caras que traía el CBT de fábrica
#   ({1010076:11, 1010042:5, 1020043:6, 1020077:12}). Al importar caras nuevas del cliente 2013 la
#   cara andaba en tienda/inventario/práctica (todo eso lo resuelve el CLIENTE leyendo face.txt) pero
#   NO en sala multi, porque ahí la apariencia la manda el SERVER y el item no estaba en el dict.
#   Ahora se arma sola cruzando data/shop_lt.txt con data/face.txt = las MISMAS fuentes que el
#   cliente -> importar una cara nueva no requiere tocar este archivo.
```


---

## lobby.py

### <a name="lobby-mini-render-reversado-el-avatar-3d-del-popup"></a>MINI-RENDER (reversado ): el AVATAR 3D del popup NO se arma con la lista worn de

```
# ★ MINI-RENDER (reversado 2026-07-13): el AVATAR 3D del popup NO se arma con la lista worn de
#   abajo (esa cae en un std::vector que el cliente DESCARTA). Se arma con estos 7 u32:
#     5º u32 (+0x30, que el server-dev rotuló 'exp') = CUERPO base -> es el GATE del render:
#       cUserInfoView: SetData 0x450aa0 -> [view+0x1938]=[src+0x30]; RefreshModelTexture 0x44ebe0
#       hace `if [+0x1938]==0: no crea la textura` -> preview TOTALMENTE vacío. Va char_id acá.
#     6º-11º u32 (+0x18/+0x1c/+0x20/+0x24/+0x28/+0x2c) = 6 slots de PIEZAS del avatar.
#   Ninguno de los 7 se muestra como número (los stats visibles son wins/losses/draws/level).
```

### <a name="lobby-cara-nativa-en-sala-re-del-handler-0x5863f0-"></a>CARA NATIVA EN SALA (, RE del handler 0x5863F0) — r25 ES el faceByte

```
# ★★ CARA NATIVA EN SALA (2026-07-25, RE del handler 0x5863F0) — r25 ES el faceByte.
#   Cadena VERIFICADA en el desensamblado del exe prístino:
#     0x5863F0 lee r11 (u32 char_id) y r25 (u8 cara, read 0x58667A)
#     -> 0x58669B call 0x4C5670(char_id, &r25)  = gate: char_id debe ser 0x1FF54/55 (CharType 0)
#        o 0x222F6/0x24A07 (CharType 1); si NO valida -> 0x5866A4 pisa la cara a 0.
#     -> r25 va al descriptor +0x3D -> AddOccupant 0x53C2C0 (vtable+0x28) -> 0x565F50
#        -> 0x566136 escribe occ+0x191, que por el offset del nodo del map ES occ+0x1A1.
#   O sea el FF0C construye al ocupante YA con la cara puesta: NO pasa por el setter 0x530F30
#   ni por el reload 0x53CA60 -> NO hay evento de "cambió la apariencia" -> SIN SONIDO.
#   Antes mandábamos r25=0 y se compensaba con un FF15 por ocupante al entrar (handler 0x58DC60),
#   que sí dispara el reload -> sonido en cada ingreso a sala. Ese hack ya no hace falta acá.
#   El gate ya lo cumplimos: r11 = char_id de la DB (ver ★ CUERPO F EN SALA arriba).
```

### <a name="lobby-cara-mano-en-sala-el-char-id-va-dos-veces-a-"></a>CARA+MANO EN SALA — el char_id va DOS veces a propósito

```
# ★★ CARA+MANO EN SALA (2026-07-12) — el char_id va DOS veces a propósito:
#   • r11 (slots[0]) = cuerpo base -> fija GÉNERO (el memset 0x586554 no borra slots[0]).
#   • r14[0] (primer item worn) -> el CLASIFICADOR de slots del cliente (0x4C43C0) expande el
#     char_id a las piezas base CUERPO/CARA/MANO, igual que MyRecord (cuya lista de apariencia es
#     [char_id]+ropa -> por eso en PRÁCTICA/menú el avatar sale COMPLETO). Al removerlo de r14 (fix
#     'cuerpo F') la sala quedaba SIN cara ni mano: slot1(cara) y el slot de mano nunca se llenaban.
#   Van a slots DISTINTOS (slots[0] vs slot1+), no se pisan -> el género NO regresa.
```

### <a name="lobby-cuerpo-f-en-sala-causa-real-pista-amigo-core"></a>CUERPO F EN SALA — CAUSA REAL (, pista amigo coreano "RoleID=Gender" + su server

```
# ★★ CUERPO F EN SALA — CAUSA REAL (2026-07-06, pista amigo coreano "RoleID=Gender" + su server
#   de referencia importador-musica/output/server1_(1).py). El handler 0x5863F0 lee r11 en
#   [esp+0xbc] = slots[0], y el memset 0x586554 borra slots[1..11] PERO NO slots[0]. O sea slots[0]
#   se puebla EXCLUSIVAMENTE con r11 = es el CUERPO base. El server coreano manda su char_id (v47)
#   EXACTAMENTE en esta posición (tras gender/slot/0/0) y NO lo mete en la ropa. Nosotros mandábamos
#   r11=0 -> slots[0]=0 -> 0x4C5FB0 key=0 -> DEFAULT MALE (por eso solo se notaba en chars F). Fix =
#   poner el char_id de la DB en r11 (slots[0]) y NO prepend-earlo a la ropa (espejo del coreano).
```

### <a name="lobby-b-b-off-40-n-no-es-relleno-es-el-byte-de-lic"></a>`b_B` (off 40+N) NO ES RELLENO: **ES EL BYTE DE LICENCIA**

```
# ★★★★★ 2026-08-02 — `b_B` (off 40+N) NO ES RELLENO: **ES EL BYTE DE LICENCIA**.
#   El handler `0x5780E0` lo lee último y en `0x57843A` lo pasa a `SetLicense` (`0x52BBF0`),
#   que hace `mov byte [cRoom+0x6D36], al`. Mandarlo en 0 => el jugador queda SIN BANDA
#   (campoA 0xC) => el filtro del picker no deja pasar ninguna canción => **PICKER VACÍO**,
#   y al salir de la sala el índice de selección apunta a una lista de 0 elementos.
#   MEDIDO en vivo (tools/frida/trazar_picker.py): la FFC9 del JOINCHANNEL la deja bien y
#   el PRIMER `send_roominfo` la borra; `send_roominfo` corre además en CADA cambio de
#   escenario (FF1B) o de slot (FF87), así que la pisaba una y otra vez.
#   ★ La FFC9 nunca puede causarlo: su handler `0x581A30` ignora el 0 (`test al,al; je`).
#     El FF13 es el ÚNICO que puede poner la licencia en cero.
#   Valores: 1=novato · 2/3=estrella · 4/5=luna · 6/7=sol · fuera de 1..7 => campoA 0xC (libre).
```

### <a name="lobby-v-a-silenciosa-del-modo-frida-el-byte-4-mode"></a>VÍA SILENCIOSA DEL MODO (Frida ): el byte #4 (mode) es el que el handler de FF13 pasa a la...

```
# ★ VÍA SILENCIOSA DEL MODO (Frida 2026-07-01): el byte #4 (mode) es el que el handler de FF13 pasa a la vía
#   SILENCIOSA (inner_id 0x13 -> demux -> 0x53ACF0 -> 0x539450 -> 0x529EB0, escribe cRoom+0x6D84/6D80) SIN popup.
#   Debe ir el byMode (0x10=equipo+vel, 0x14=equipo+item, 0x00=solo+vel, 0x04=solo+item), NO el modo raw del
#   CREATEROOM (0-3): mandar raw 3 lo decodificaba como SOLO. Así el FF13 solo fija el modo correcto y NO hace
#   falta el FF4B (que era el que abría el popup). (Confirmado: byMode del cable == struct+9 que lee 0x53ACF0.)
```

### <a name="lobby-cabecera-extendida-del-canal-libre-campob-6"></a>CABECERA EXTENDIDA DEL CANAL LIBRE (campoB==6)

```
# ★★★★★ CABECERA EXTENDIDA DEL CANAL LIBRE (campoB==6). El handler FF28 hace, JUSTO acá:
#     0x587962  mov ecx,[0x766608] ; call 0x465550   -> al = [+0x13e5] = campoB del canal
#     0x58796D  cmp al,6 ; jne ...                   -> si ES 6: lee 4 bytes + 4 bytes y los pasa a 0x4B4860
#     0x5879A8  ...luego recién lee el [count:u8]
#   Si el canal anuncia campoB=6 y NO mandamos estos 8 bytes, el cliente se come el count y 7 bytes de la
#   primera fila como si fueran la cabecera ⇒ TODO el cartel sale desfasado. Ésa es la mitad que faltaba
#   del canal libre: la otra mitad es anunciarlo con status 0x47/0x48/0x49 (ver LIBRE_STATUS).
#   Contenido: 2×u32 desconocidos (van a 0x4B4860). Se mandan en 0 hasta saber qué son.
```

### <a name="lobby-2-byte-flags-de-solo-equipo-velocidad-tem-de"></a>2º byte = FLAGS de solo/equipo×velocidad/ítem (decode 0x43b200): bit4(0x10)=EQUIPO, bit2(0...

```
# ★ 2º byte = FLAGS de solo/equipo×velocidad/ítem (decode 0x43b200): bit4(0x10)=EQUIPO, bit2(0x04)=ÍTEM
#   (velocidad = bit2 en 0). BUG RESUELTO (agente afcfc1e9): antes ponía 0 -> el cliente leía solo+velocidad.
# ★ El byte MODE de la entrada FF6E == el byMode (como FF4B): equipo+vel=0x10, equipo+item=0x14, solo+vel=0x00,
#   solo+item=0x04. VERIFICADO EN VIVO (2026-07-01): con byMode la sala lista con su modo correcto; con el mode
#   RAW (0-3) el cliente prende el ícono de "contraseña/privada" (bits mal interpretados). (El crash con 2+ salas
#   era artefacto de Frida, NO el valor de este byte.)
```

### <a name="lobby-song-id-debe-ser-una-canci-n-que-el-cliente-"></a>song_id DEBE ser una canción que el cliente TENGA en su DB [0x766638]: el append de sala 0...

```
# ★ song_id DEBE ser una canción que el cliente TENGA en su DB [0x766638]: el append de sala 0x463a90
# hace un lookup de la canción (0x40cde0) y si NO la encuentra SALTA el push (je 0x463d04) -> la sala
# NO se agrega a la lista. 102 NO existía -> sala invisible. 518 (Zealot) sí está (SONGS + integrada).
# 1 sala test placeholder (song 518 válida). El cliente lista SALA TEST + salas reales, con PAGINACIÓN (12/pág,
#   ver send_roomlist). Stress test OK: 60 salas → 5 páginas sin crash (el "crash con muchas salas" era Frida).
# ★ SOLO en modo DEV (R2_DEV=1). En producción (VPS) la lista muestra SOLO salas reales.
```

### <a name="lobby-el-ff49-no-es-un-roster-de-servidores-es-un-"></a>EL FF49 **NO** ES UN "ROSTER DE SERVIDORES": ES UN RECORD COMPLETO,

```
# ★★★★★ 2026-08-02 — EL FF49 **NO** ES UN "ROSTER DE SERVIDORES": ES UN RECORD COMPLETO,
#   Y **RESETEA LA MÚSICA POSEÍDA**. Layout real (RE del handler `0x577640`):
#       [selectKey:u32][N1:u16]×{[key:u32][nCampos:u8][campos:u32...]}
#       [N2:u16]×{[item:u32][fin:u32][en_uso:u8][cant:u16]}   <- INVENTARIO
#       [N3:u16]×[songid:u16]                                 <- MÚSICA POSEÍDA  ★
#   Justo antes del bloque de música el handler hace `call 0x455330` = **ClearOwnedSongs**
#   (vacía `[0x766640]+0x4AC`, que es LA fuente del picker) y mete 4 dummies que el rebuild
#   `0x55B210` descarta por ser < 0x64.
#   ⇒ Mandar N3=0 BORRA el picker. Con el MyRecord es el MISMO vector: si el FF49 llega
#     DESPUÉS, gana el FF49 y la lista queda vacía.
#   ESO explicaba el síntoma: en el primer login el FF49 sale ANTES del último MyRecord
#   (repuebla) ✅; en el CAMBIO DE CANAL el cliente PIDE el FF49 ~525 ms DESPUÉS del
#   JOINCHANNEL, así que llegaba último y borraba ❌. Nada se "perdía" en el reconnect:
#   lo borrábamos nosotros. Y por eso `send_menu_myrecord()` no alcanzó — agrega paquetes
#   ANTES, y el borrado venía DESPUÉS.
#   ⇒ Mandando la MISMA lista que el campo #32 del MyRecord, el FF49 queda IDEMPOTENTE y
#     el orden deja de importar.
#   ⚠⚠ EL MISMO BUG ESTABA EN N2 (INVENTARIO) — corregido el 2026-08-02 (bis).
#     Antes acá decía "N2 no se limpia antes, poblarlo duplicaría". FALSO: `0x4592F0` es un
#     push_back, pero el handler ya reseteó el contenedor `record+0x158` al entrar
#     (`0x456270` -> `0x43d4f0`/`0x455d40`, en sus DOS ramas), que es donde ese push_back
#     inserta. Con `N2=0` el inventario quedaba VACÍO al cambiar de canal, igual que el
#     picker: el MyRecord lo poblaba con el campo #37 y el FF49 llegaba después y lo borraba.
#   ⇒ Mandamos el MISMO set que el #37, con el MISMO empaquetado (`pack_item_record`), así
#     el FF49 queda idempotente TAMBIÉN para el inventario y el orden deja de importar.
```

### <a name="lobby-licencia-setlicense-handler-cliente-0x581a30"></a>Licencia (SetLicense, handler cliente 0x581A30 -> player+0x6d36 -> ícono 0x502578)

```
# Licencia (SetLicense, handler cliente 0x581A30 -> player+0x6d36 -> ícono 0x502578):
#   02=estrella  04=luna  06=sol  (01=novato; debe ser !=0). En Libre = 06 (sol) para desbloquear todo.
# ★★★ 2026-08-01 — licencia HONESTA: sale de la banda REAL del jugador (su exp), no de una constante.
#   Antes: `LICENSE_BYTE`(7=Sol) para todos y `LIBRE_LIC`(6) en Libre => cualquiera jugaba todo.
#   R2_LICENCIA_HONESTA=0 restaura el comportamiento viejo. El bisect `R2_LIBRE_LIC` sólo pisa en Libre.
# ★ En LIBRE la licencia va FORZADA (LIBRE_LIC): el canal no tiene banda propia — el cliente le pone
#   categoría 0xC = "ninguna" — así que si no se la damos nosotros, el jugador queda sin banda.
#   En los demás canales sale de su banda REAL (honesta) o de la constante vieja.
```

### <a name="lobby-myrecord-en-el-joinchannel-puebla-el-picker-"></a>MyRecord EN EL JOINCHANNEL (puebla el picker #32 SONGS/#34 STAGES JUSTO al entrar al canal...

```
# ★ MyRecord EN EL JOINCHANNEL (puebla el picker #32 SONGS/#34 STAGES JUSTO al entrar al canal).
# El picker widget se recrea al entrar a cRoom ([[r2beat-picker-reset]]); si el MyRecord se mandó solo
# en el menú (primer login), el widget nace vacío. Mandarlo en CADA JOINCHANNEL (primer login Y reconnect
# de switch, donde el cliente ni siquiera pide FF03) lo deja poblado. (cmd 0x03 del menú se mantiene aparte.)
# ★★★★★ 2026-08-02 — HAY DOS MODOS DE ENTRADA Y NO SON EL MISMO. Medido por el usuario:
#   (1) PRIMER LOGIN  = el cliente PIDE `FF03` (menú) y DESPUÉS entra al canal
#                       => recibe DOS MyRecords: el del MENÚ (`db_worn_items`) y el del CANAL
#                          (`[hair,top,bottom,skates]`). ✅ el picker se puebla.
#   (2) CAMBIO DE CANAL = el cliente NUNCA pide `FF03`
#                       => recibía SÓLO el del CANAL. ❌ el picker queda vacío.
#   ⚠ PROBADO Y DESCARTADO: unificar el `parts` de los dos MyRecords ROMPE TAMBIÉN EL (1)
#     (el usuario lo verificó: picker vacío ya sin cambiar de canal). O sea que el del CANAL
#     TIENE que llevar los 4 `parts`, y el del MENÚ el set completo. NO son intercambiables.
#   ⇒ La secuencia del modo (2) tiene que REPLICAR la del (1): primero el MyRecord de MENÚ,
#     después el de CANAL. Ver `send_menu_myrecord()`.
#   ★ Y Libre es INOCENTE: se reprodujo Estrella -> Luna, sin Libre de por medio.
```

### <a name="lobby-el-g-nero-no-viene-como-campo-aparte-va-impl"></a>El género NO viene como campo aparte: va IMPLÍCITO en el char_id

```
# El género NO viene como campo aparte: va IMPLÍCITO en el char_id.
# Confirmado capturando dos creaciones de personaje en vivo:
#   personaje masculino -> char_id 0x0001FF54
#   personaje femenino  -> char_id 0x0001FF55
# O sea, el bit 0: 0 = masculino, 1 = femenino.
```

### <a name="lobby"></a>────────────────────────────────────────────────────────────────

```
# ────────────────────────────────────────────────────────────────
#  LA COMPUERTA, según el RE del cliente (HALLAZGOS.md §16.4/§16.10):
#  El cliente decide en VA 0x50CB43 (cCharSelect_NewUI):
#     count = (end-begin)/0x30 del array de personajes [0x766640]+0xC8
#     count != 0  -> MENÚ (0x424DE0)        count == 0 -> CREACIÓN (0x4602C0)
#  Ese array lo llena el char-build del MyRecord, gobernado por @54 (2 bytes = nº de
#  personajes). Entonces, en la práctica, el campo que controla todo es @54 del MyRecord:
#     @54 == 0            -> array vacío -> pantalla de CREACIÓN
#     @54 >= 1, @56 != 0  -> empuja struct -> MENÚ (entra al lobby)
#  server-work hardcodea @54=1 (por eso SIEMPRE salta la creación). Aquí lo hacemos honesto:
#  @54 sale de la DB (1 si hay personaje, 0 si no).
# ────────────────────────────────────────────────────────────────
```

### <a name="lobby-avisar-los-que-salieron-del-slot"></a>avisar los que salieron del slot

```
# ★2026-07-27: avisar los que salieron del slot. La respuesta de arriba sólo nombra al item
# NUEVO, así que el armario seguía mostrando MARCADA la pieza anterior (se "apilaban" las
# caras en la pestaña Cara) aunque en la DB ya estuviera in_use=0. Se manda el mismo FF1A con
# action=0 por cada uno -> el cliente los despinta con su propio protocolo (sin parche de exe).
#
# ⚠SÓLO para los slots de SLOTS_SIN_EXCLUSIVIDAD (ver arriba). Mandárselo a un slot que el
# cliente YA maneja hace que **limpie el slot ENTERO** en vez del item nombrado: la prenda
# recién puesta se apagaba al instante y hacía falta un segundo clic. Medido en vivo con
# cabellos (equip 220586 + aviso 220501 -> quedaba pelado) y con la mano.
```

### <a name="lobby-ff81-tutorial-misi-n-de-la-escuela-rnr-schoo"></a>FF81 = TUTORIAL / MISIÓN de la ESCUELA (rnr_school)

```
# ★ FF81 = TUTORIAL / MISIÓN de la ESCUELA (rnr_school). BIDIRECCIONAL.
#   Sender C→S cliente 0x57D060 (muchos callers en 0x42xxxx = modo escuela): REPORTA el paso
#   de tutorial. Body chico (~2-3 bytes). El sender NO bloquea (fire-and-forget), a diferencia
#   de FFE2. Recv S→C 0x583820 (CMD_81): 14 bytes [state:u8][selector:u8][f3:u32][f4:u32][f5:u32],
#   selector 0..5 (jump-table). Escribe [player+0x331]=state (paso). selector 1/2/3="DBERROR",
#   0/4=avance normal, 5=fin/DB. states 6/7/8 = rango terminal (special-cased en sender y recv).
# Persistencia: chars.mission (ya se envía en FF0C r06 y MyRecord). Guardamos el paso reportado
#   (monótono) para que al re-logear la misión figure con el progreso alcanzado.
# ⏳ AÚN NO respondemos S→C (formato de f3/f4/f5 sin confirmar en vivo -> evitar over-read/crash
#   como pasó con FFE2). Primero capturamos los bytes reales de la lección 1 acá en el log.
```

### <a name="lobby-ff9e-crashdump-del-cliente-estilo-wer-268b-p"></a>FF9E = CRASHDUMP del cliente (estilo WER, ~268B): pila con direcciones de retorno +

```
# ★ FF9E = CRASHDUMP del cliente (estilo WER, ~268B): pila con direcciones de retorno +
#   (a veces) el path UTF-16 del módulo que falló. El cliente lo MANDA al reventar; NO espera
#   respuesta (no se contesta, igual que antes). Antes caía en "sin handler"; acá lo DECODIFICAMOS
#   y dejamos en el log las direcciones dentro de R2Beat.exe (.text 0x401000-0x6D0000) y el módulo,
#   para diagnosticar crashes de gameplay sin tener que leer el hex a mano.
```

### <a name="lobby-ff54-el-cliente-pide-la-lista-de-entrada-del"></a>FF54 = el cliente PIDE la lista de entrada del lobby (recv handler cliente 0x57A910)

```
# ★ FF54 = el cliente PIDE la lista de entrada del lobby (recv handler cliente 0x57A910).
# La manda en OnEnter, JUSTO después de FF45 (que ya respondemos). El handler parsea la
# respuesta como: [count:u8] + por entrada [namelen:u8][name][u32 a][u32 b][u32 c].
# IGNORARLA deja el objeto-lista del lobby sin su respuesta S→C -> al saltar la animación de
# bienvenida (sin el colchón de tiempo) se accede a medio armar -> CRASH (RE agente a0702eba).
# Como el switch repite este mismo tramo (relogin), también lo cubre. Respuesta MÍNIMA válida
# = lista VACÍA (count=0). Espejo del FF45. (FF4A=clic y FF40[0x21]=post-join quedan TODO.)
```

### <a name="lobby-chat-req-c-s-chattype-1-texto-echo-s-c-handl"></a>CHAT: req C→S [chatType:1][texto]. Echo S→C (handler cliente 0x5778B0)

```
# CHAT: req C→S [chatType:1][texto]. Echo S→C (handler cliente 0x5778B0):
# [namelen:1][name][msglen:1][msg][speakerId:1][chatType:1] -> muestra "nick: msg".
# FORMATO ENTRANTE REAL (confirmado en captura clean.jsonl): [msglen:1][msg]. El 1er byte es
# el LARGO del mensaje, NO un chatType. BUG ARREGLADO: antes chat_type=data[0]=largo -> "a"(1)/
# "aa"(2)/"hola"(4) disparaban colores/efectos 1/2/4. Ahora chatType FIJO = chat normal.
```

### <a name="lobby-gameover-fin-de-carrera-modelo-event-driven-"></a>GAMEOVER / FIN DE CARRERA — MODELO EVENT-DRIVEN (, RE aa70a7fe verificado + captura viva 2...

```
# ★★ GAMEOVER / FIN DE CARRERA — MODELO EVENT-DRIVEN (2026-07-01, RE aa70a7fe verificado + captura viva 2PC).
#   Score C→S FF28 = [frame:u32][combo:u16][pd:u8][star:u8][coin:u16] (+hash). HALLAZGOS CLAVE del RE:
#   • La "FALLIDA" es 100% CLIENT-SIDE: al llegar el contador local (FF27, game+0xB4C=300 frames ~5s) a 0 sin
#     haber llegado, el héroe entra en estado 6 (pose congelada) y reproduce sign_fail (0x4C2147). NO la pide
#     el cartel. WIN/LOSE del cartel es por EQUIPO (byte[0]=winteam vs tu equipo); rank==7 NO controla overlay.
#   • EL BUG ERA PURO TIMING: el cartel FF28 fuerza carrera→resultado; si llega ANTES de que el contador del
#     no-finalista llegue a 0, el héroe nunca entra en estado 6 -> nunca sale fallida -> muestra "perdiste".
#   • FINISH vs FAIL en el cable = el JUDGMENT: llegada real trae combo/pd/star REALES; el fallo (congelado en
#     countdown-0) manda FF28 con combo=pd=star=0 y frame real. (Capturado: meta 5318/239/87/100; fallo 5725/0/0/0.)
#   FIX: esperar a que TODOS los jugadores manden su FF28 (llegada o fallo=señal de que ya se congelaron) y
#   recién ahí mandar el cartel (+0.3s de gracia de red). Así el freeze/fallida ya ocurrió local. Fallback 10s
#   por si alguien se desconecta y nunca reporta.
```

### <a name="lobby-fix-final-verificado-en-vivo-f2-bonus-precis"></a>=== FIX FINAL (verificado en vivo): f2(bonus precisión / +0x2dc)=0 = PRECISIÓN REAL del su...

```
# === FIX FINAL (verificado en vivo): f2(bonus precisión / +0x2dc)=0 = PRECISIÓN REAL del summary, sin romper el boost. ===
# MAPEO de los 4 slots (confirmado por hook STTRANS): f0=current(+0x2d0), f1=DURACIÓN(int,+0x2d8),
#   f2=BONUS de precisión del boost(+0x2dc, leído como int de los bits, clamp 8), f3=target(+0x2d4).
# El summary (0x4b6090) = (suma/cuenta de grades) + bonus de buffs activos; el boost suma +0x2dc. Con bonus=8 -> ~98
#   (parecía catch-rate). f2=0 -> SIN bonus -> promedio honesto real (92-94). 0 = "la precisión real". Boost OK igual.
#   (Para un bonus auténtico chico mandar el slot como entero: struct.pack("<f I I f",1.08,DUR,2,1.08) -> +2.)
# Ver memoria r2beat-precision-grade-system.
```

### <a name="lobby-ffc8-en-nuestro-cliente-responder-el-ffc8-16"></a>FFC8: en NUESTRO cliente, responder el FFC8 (16 bytes = pos de char, 4 floats) es lo que

```
# FFC8: en NUESTRO cliente, responder el FFC8 (16 bytes = pos de char, 4 floats) es lo que
# DISPARA el pedido de notas FF57 (recv 0x581710). Verificado empíricamente: SIN esta
# respuesta el cliente carga la escena pero nunca pide FF57 -> no arranca la música.
# (Lo que crasheaba NO era esto, era el FF24 que mandábamos tras las notas.)
# ⚠ FFC8 NO es "pos de char" — son los 4 floats del INTERPOLADOR de velocidad del boost
# (recv 0x581710 -> SetByIdx 0x4d4f60 -> StartTransition: setea racer+0x2d0 current/+0x2d4 target,
# clamp [1.06,1.148]). Mandar ceros -> target=0 -> el factor del boost rampa a 0.0 -> impulsor NaN/negro.
# FIX: floats sanos del interpolador (1.08 = factor default). RAÍZ del impulsor roto.
# PLAN D: FFC8 mixto. f0=current(float 1.08), f1=DURACIÓN(int frames), f2=[esp+0x14](float), f3=target(float).
# La duración se lee como ENTERO (fild) y eax≈dur (clamp 666). 403 frames ≈ 23 combos (nativo, sin parche).
```

### <a name="lobby-start-de-partida-multi-bot-n-empezar"></a>START de partida MULTI (botón "empezar")

```
# ★ START de partida MULTI (botón "empezar"). Flujo de SALA (RE 0x578BF0/0x578CC0 + guía como secuencia):
#   host aprieta START -> C→S FF24 (vacío) -> el server responde S→C FF25 "carguen" a todos los ocupantes:
#   [magic:u32][map:u16][song:u16]. El recv FF25 0x578BF0 guarda el magic (clave XOR score/FF4E en
#   0x73d1d4), XORea map/song a game+0x914/+0x898 y hace scene-switch al juego (0x43b9b0).
# ★ MULTI: el server manda FF25 LOADGAME a TODOS los ocupantes de la sala (no solo al host que apretó START),
#   con un MAGIC COMPARTIDO (misma clave XOR de score/FF4E para toda la sala). Antes: self.send_packet ->
#   solo el host arrancaba, el joiner se quedaba en la sala.
```

### <a name="lobby-ff3d-el-cliente-avisa-que-deja-el-canal-para"></a>FF3D = el cliente AVISA que deja el canal para SWITCHEAR (data=01), y MANTIENE el socket

```
# ★ FF3D = el cliente AVISA que deja el canal para SWITCHEAR (data=01), y MANTIENE el socket
# esperando que el server le confirme el cambio (cartel "conectando" colgado). El switch nativo
# NO es reconnect: es server-driven sobre la MISMA conexión (RE 2026-06-29 §G.13).
# EXPERIMENTO B (hipótesis usuario): replicar la secuencia de ENTRADA al canal (la que apaga el
# cartel inicial de "conectando" al login) -> MyRecord(FF03)+FF92+FF49+FF13. Candidatos de "apaga
# cartel + sonido cambio-de-servidor" = FF49. Ajustar SWITCH_REPLY abajo según lo que se observe.
```

### <a name="lobby-ff49-el-cliente-lo-pide-len-0-al-entrar-al-c"></a>FF49 — el cliente lo PIDE (len=0) al entrar al canal y NUNCA se lo contestábamos

```
# ★★ FF49 — el cliente lo PIDE (len=0) al entrar al canal y NUNCA se lo contestábamos
#   ("inner FF49 sin handler (TODO)" en el log). Es el **subsistema de notificación**: el
#   handler `0x577640` limpia la lista, mete N filas en `[0x766640]+0xbc` y llama SelectByKey
#   -> deja `idx8c` VÁLIDO (sin él, el getter `0x4522A0` puede terminar el proceso) y es
#   **el que hace el "sonidito" del cambio de servidor** (identificado en su momento por el
#   usuario, justamente por el sonido).
#   Formato (RE del handler): [selectKey:u32][count1:u16]{[key:u32][fieldCount:u8]}[u16][u16]
#   ⚠ Una fila es VISIBLE sólo si key < 10^7 y key%1000 ∈ {900,901} (MULTIPLAYER.md G.6).
#   Las keys son las MISMAS que ya se habían validado en vivo, para no reinventar el payload.
#   R2_FF49=0 lo desactiva.
#   ★★ 2026-08-02: NO armar el payload acá. Tiene que ser EL MISMO que el proactivo
#   (personaje primero + seleccionado), o `idx8c` queda apuntando a una fila de canal.
```

### <a name="lobby-recolor-de-slots-en-el-host-fix-el-ff13-sile"></a>RECOLOR DE SLOTS EN EL HOST (fix ): el FF13 silencioso setea el modo pero NO recolorea los...

```
# ★ RECOLOR DE SLOTS EN EL HOST (fix 2026-07-01): el FF13 silencioso setea el modo pero NO recolorea los slots
#   (a diferencia del FF4B applier 0x532FC0->0x52FB70 que sí, y que reciben los OTROS). Antes el host quedaba
#   con los slots rojo/azul aunque el título dijera "solo". FIX:
#     • nuevo modo EQUIPO -> send_team_colors (FF37 rojo/azul, sin popup);
#     • nuevo modo SOLO   -> FF4B applier (build_ff4b) resetea los colores a solo. Su popup dispara SOLO en
#       modo equipo (esi==1) -> con target SOLO NO hay popup (verificado: el joiner recibe el mismo FF4B y
#       recolorea a solo sin cartel).
```

### <a name="lobby-sync-de-movimiento-fase-sync"></a>SYNC DE MOVIMIENTO (FASE SYNC )

```
# ★ SYNC DE MOVIMIENTO (FASE SYNC 2026-07-01). El cliente, al mover su avatar en la SALA, manda C→S FF23
#   [kind:u8][x:f32][y:f32][dir:u8][b:u8] = 11 bytes SIN slot (el emisor es implícitamente "me"). El server
#   RELAYA a los OTROS ocupantes S→C FF23 [slot_emisor:u8][kind][x:f32][y:f32][dir][b] = 12 bytes: byte0=slot
#   del que se movió, que el recv 0x578B50 usa como KEY del mapa de ocupantes cRoom+0x2c (find 0x52F050) para
#   mover el avatar REMOTO correcto (x/y f32 LE -> occ+0xc/+0x14; dir @10 dispara la animación dash_ing/stop).
#   ★ NO eco al emisor: el handler NO tiene guard self-slot -> si le devuelvo su FF23, animaría su propio
#   avatar como remoto. (RE agente a87bf8ce: recv=12B con slot al frente; send=11B sin slot; sin self-skip.)
```

### <a name="lobby-transferir-propiedad-de-sala-poder-del-anfit"></a>TRANSFERIR PROPIEDAD DE SALA (poder del anfitrión: clic en un joiner -> "hacer líder")

```
# ★ TRANSFERIR PROPIEDAD DE SALA (poder del anfitrión: clic en un joiner -> "hacer líder"). C→S FF9B [slot].
#   RE a9e1f0ec: recv 0x57f6c0 = ENABLE-ONLY per-target: cuerpo [slot][reserved][status]; con status==0 y
#   slot==mi_slot -> ese cliente se vuelve anfitrión (habilita UI host 0x52a0e0). NO demota al viejo ni avisa
#   a otros. Receta server: (1) host_uid=target; (2) FF9B [slot][00][00] al NUEVO -> muestra anfitrión;
#   (3) FF13 host_pos=nuevo a TODOS (el viejo cae de líder client-side + todos lo ven). Solo el LÍDER puede.
```

### <a name="lobby-cambiar-de-equipo-el-bot-n-hud-manda-c-s-ff1"></a>CAMBIAR DE EQUIPO: el botón HUD manda C→S FF18 [team:u8] (02=azul/team2, 01=rojo/team1)

```
# ★ CAMBIAR DE EQUIPO: el botón HUD manda C→S FF18 [team:u8] (02=azul/team2, 01=rojo/team1). En ESTE cliente
# "equipo = LADO del slot" (rojo=0,1,2 / azul=3,4,5) y cambiar de equipo = MOVERSE a un slot ABIERTO del lado
# destino. El server responde S→C FF37 [team][from][to] (to!=from): 0x53c4c0 MUEVE+recrea el widget del host
# (redibuja, lo que el eco mismo-slot NO hacía) y escribe [me+0x40]=to; luego 0x52fb70 pinta el team. Hay que
# actualizar host_slot -> los FF0C siguientes mandan el host con r08=to (me-detect coherente). (RE ae965ff2.)
```

### <a name="lobby-m-sica-sin-resolver"></a>MÚSICA — SIN RESOLVER

```
# MÚSICA — SIN RESOLVER. Confusión de RE aclarada 2026-06-21: [0x766618]+0xc158 (record
# 0x1B0, render "%02d/40", handler 0x5823C0=FF6E) es la LISTA DE SALAS, NO el picker de
# canciones (prueba: send_roomlist ya llena ese vector y "SALA TEST" aparece, pero el
# picker siguió vacío). FF78(0x587E90)=select de song-ids (no inserta); FF77(0x587B90)=
# mi-info. El control del PICKER DE MÚSICA de sala es OTRO, aún sin identificar. NO
# empujar nada acá hasta hallar el writer real (o capturar el paquete del oráculo coreano).
```

### <a name="lobby-nota-re-el-modo-solo-equipo-no-se-setea-desd"></a>NOTA RE el modo solo/equipo NO se setea desde el server

```
# NOTA RE 2026-06-30: el modo solo/equipo NO se setea desde el server. Es estado CLIENT-SIDE en
# [cRoom+0x7978] (peek=0=solo), fijado por el diálogo de creación (~6 escritores mov[reg+0x7978],bl:
# 0x540054/0x543dec/0x543eb6 etc.) y RESETEADO a 0 en CREATEROOM recv 0x584F9B si !=3 (cmp ==3).
# PROBADO: echar el modo en el byte de respuesta FF10 (01 00 03) NO cambia +0x7978 (sigue 0). Por eso
# respondemos el ack simple. Falta RE de la máquina de modo del diálogo (por qué cae a solo).
```

### <a name="lobby-ffe2-popup-referido-qui-n-te-invit-del-flujo"></a>FFE2 = popup REFERIDO ("¿quién te invitó?") del flujo de CREACIÓN

```
# FFE2 = popup REFERIDO ("¿quién te invitó?") del flujo de CREACIÓN. El cliente manda
# [len][nick] y BLOQUEA el popup esperando respuesta -> sin handler se ATORA (era el bug).
# El recv 0x581C60 espera [result:u8][len:u8][nick] y ramifica:
#   result==0 -> popup str 656 "%s ha sido registrado como el que lo invitó" + callback
#                0x5711B0 = SceneSwitch(0x43B9B0) -> AVANZA al lobby (destraba el popup).
#   result!=0 -> popup str 655 "No existe el Nickname" (reabre la caja -> se re-atora).
# Aceptamos SIEMPRE (result=0) + eco del nick -> el popup confirma y entra al lobby.
```

### <a name="lobby-ff35-announce-el-handler-cliente-0x570910-pa"></a>FF35 ANNOUNCE: el handler cliente 0x570910 PARSEA [b0][b1][b2][b3][len:1][texto] a un

```
# FF35 ANNOUNCE: el handler cliente 0x570910 PARSEA [b0][b1][b2][b3][len:1][texto] a un
# buffer LOCAL y lo DESCARTA (stub destripado, no muestra el cartel). Mandarlo es inútil
# -> NO mandar. El cartel basura/flash al entrar NO es esto (viene de otra parte del cliente).
# NO empujar FF04 aquí. REFUTADO EN VIVO 2026-06-21: FF04 NO es la lista de canales
# (ese RE estaba mal). FF04 es la respuesta de CREAR PERSONAJE; pushearlo en el menú
# hace que el cliente crea que apareció un personaje -> muestra 1 entrada FANTASMA
# invisible + dispara el popup de REFERIDO. La lista de canales viene por OTRA vía
# (sin identificar). send_serverlist() queda sin usar (no llamar).
```

### <a name="lobby-popup-servidores-ff49-cmd-0x49-handler-0x577"></a>POPUP "Servidores" = FF49 (cmd 0x49, handler 0x577640): UN paquete que limpia la lista,

```
# ★★ POPUP "Servidores" = FF49 (cmd 0x49, handler 0x577640): UN paquete que limpia la lista,
# mete N filas en [0x766640]+0xbc Y llama SelectByKey -> fija idx8c válido (SIN crash, sin parche).
# Formato: [selectKey:u32][count1:u16]{[channelId:u32][fieldCount:u8=0]}[count2:u16=0][count3:u16=0].
# Fila VISIBLE (no filtrada) sii key<10^7 Y key%1000 ∈ {900,901}. Ver MULTIPLAYER.md G.6.
# ★ FIX BIENVENIDA/VOZ 2026-07-03 (Frida): la lista [PMGR+0xc8] (structs 0x30) que lee 0x4521d0
#   MEZCLA canales con el PERSONAJE. El render de la bienvenida y la voz por género leen
#   list[idx8c].f0 y lo usan como char. Con select=1900 idx8c apunta a un CANAL -> char default male.
#   El char real llega DESPUÉS de este FF49, así que NO se puede seleccionar por char_id acá
#   (SelectByKey no lo encuentra -> idx8c=0xFFFF -> CRASH). SOLUCIÓN: meter el char_id como PRIMERA
#   entrada de ESTE mismo FF49 (garantiza que está en la lista al SelectByKey) y seleccionarlo.
# ★ 2026-08-02: el armado se movió a `build_ff49_roster()` para que el handler del
#   PEDIDO (cmd 0x49) mande EXACTAMENTE lo mismo. Ver el comentario de ese método.
```

### <a name="lobby-ff03-myrecord"></a>FF03 = MyRecord

```
# FF03 = MyRecord. CONFIRMADO por el diag (DSP 3 h=0x589740): cmd3 va al handler
# MyRecord 0x589740 (NO es nic — la teoría nic fue un error; el handler no valida nic
# ni desconecta, solo se cuelga si un count u16 queda basura). Entregar el MyRecord
# BIEN SERIALIZADO (build_myrecord_honesto, 82 campos var-len, counts=0, música=0).
# El blob viejo de 142b desincronizaba -> loop "Music Not Found"/crash.
```


---

## login.py

### <a name="login-este-u32-es-la-clave-de-identidad-del-canal-"></a>ESTE u32 ES LA **CLAVE DE IDENTIDAD** DEL CANAL, no un "uid"

```
# ★★★★★ 2026-08-01 — ESTE u32 ES LA **CLAVE DE IDENTIDAD** DEL CANAL, no un "uid".
#   El cliente (`0x46D4B0`, find-or-insert sobre el vector ordenado de canales) usa
#   `low16(A)` como clave: la busca con `0x46A0B0` y, si la encuentra, ACTUALIZA la entrada
#   en vez de insertar una nueva. Mandando 0 en todas, los 4 canales colapsaban en UNA sola
#   fila (ganaba el último) y desaparecían de la lista SIN ningún error.
#   Por eso se había parcheado el exe (4 bytes: 0x46D24E/0x46D45B/0x46D4EB/0x46D6B6) para que
#   la clave saliera del PUERTO en vez de este campo. Con un id único acá **el parche sobra**
#   y la lista funciona nativa. El campo A no se usa para nada más en el cliente (verificado:
#   sólo se lee en esos 2 sitios por función, y sólo sus 16 bits bajos).
```

### <a name="login-lobbyserver-list-handler-cliente-0x56fd70"></a>LOBBYSERVER_LIST (handler cliente 0x56FD70)

```
# LOBBYSERVER_LIST (handler cliente 0x56FD70). Formato por entrada (CONFIRMADO):
# [uid:u32][PORT:u16][len1][IP=string1][wordC:u16][chan_id:u16][STATUS:u8][len2:u8][name].
# ⚠ REVERTIDO al 1 CANAL que FUNCIONA. El multi-canal cuelga por TIER-GATING: el auto-connect
# 0x570036 -> 0x46B0E0 selecciona la entrada cuyo byte[+1] (status decodificado) casa con el
# "bucket" que devuelve 0x452610 = el TIER DEL JUGADOR (según su exp/nivel, tabla 0x6E90B8).
# Con 1 canal (0x0F) conecta (por fallback); con 3 el matching de tier vs el nivel del jugador
# falla -> 0x469210 no corre -> IP/port vacíos -> LOADING. (Probado en vivo que 0x03/0x0F/0x0C
# cuelga.) PENDIENTE: calcular el tier del jugador y darle al canal asignado el STATUS cuyo
# byte[+1] matchee ese tier, para multi-canal. Por ahora: 1 canal asignado (auto-connect OK).
# TEST 2026-06 (teoría usuario): mandar los 3 canales. El COMODÍN (0x0F) auto-conecta al login
# (matchea cualquier bucket); luna(0x0C)/sol(0x08) aparecen en el selector "servidor" para cambiar.
```

### <a name="login-field9-campo-buff-del-ff01-nibble-de-misione"></a>FIELD9 (campo "buff" del FF01) = nibble de MISIONES-historia completadas

```
# ★ FIELD9 (campo "buff" del FF01) = nibble de MISIONES-historia completadas. El handler
#   cliente 0x56F990 lo pasa a 0x455A00, que decodifica 3 nibbles y marca el done-flag del
#   vector [player+0x3c] (keys 1..n): nibble0->keys 1..n (grupo 1), nibble1->0x10.. , nibble2->0x100..
#   SIN esto, al re-logear la misión sale "reto" y NO desbloquea la siguiente (el vector queda
#   sin marcar; MyRecord solo restaura el contador +0x331 = LECCIONES, no el done por-misión).
#   VALIDADO EN VIVO (buff=2 -> misiones 1,2 "hechas" + misión 3 desbloqueada).
#   Mapeo desde chars.mission (=contador +0x331 de lecciones): misión 1 = 5 lecciones
#   (mission 1..5), misión 2 = "lección" 6, ... => misión N completa cuando mission == 4+N.
#   Por eso nibble0 = clamp(mission-4, 0, 8). (misiones luna/sol group2/3 = nibbles 1/2: TODO.)
```


---

## paquetes.py

### <a name="paquetes-34-stages-pose-dos-del-usuario-u16-count-"></a>#34 = STAGES POSEÍDOS del usuario: [u16 count] + count×[u16 stageid][u8 flag]. El handler

```
# #34 = STAGES POSEÍDOS del usuario: [u16 count] + count×[u16 stageid][u8 flag]. El handler
# 0x589740 (loop 0x589f10) lee cada entrada (read(2) stageid @0x589f14 + read(1) flag @0x589f22)
# y llama 0x458650 -> push_back u16 a player[0x766640]+0x4d0 -> FUENTE del picker de stage
# (rebuild 0x5670d0, loop 0x5673c9). flag=0 es seguro (va a 0x570f20=metadata en DB [0x76663c]).
# NOTA: el cliente trata stageid 0x19(=25) como centinela y NO lo agrega al vector (cmp 0x589f34),
# solo registra su metadata; los demás stageids de stage_lt.txt sí aparecen. Mandar 0 dejaba el
# PICKER DE STAGE VACÍO (mismo bug histórico que la música #32). ANÁLOGO EXACTO a #32 música.
```

### <a name="paquetes-32-m-sica-pose-da-del-usuario-u16-count-u"></a>#32 = MÚSICA POSEÍDA del usuario: [u16 count][u16 songid]×count

```
# #32 = MÚSICA POSEÍDA del usuario: [u16 count][u16 songid]×count. El handler 0x589740 lee esto
# y llama AddOwnedSong (0x4585B0) -> llena [0x766640]+0x4ac -> es la FUENTE del picker de música
# ([picker+0x2c]). Mandar 0 dejaba el PICKER VACÍO (el muro más viejo del proyecto). Los songids
# deben existir en music_lt.txt/DB [0x766638] o el cliente loguea "Music Not Found" (no crashea,
# solo lo saltea). SONGS salió de music_lt.txt, así que validan. (0,1,3,4 los agrega el cliente solo.)
```

### <a name="paquetes-ff97-server-clock"></a>FF97 = SERVER_CLOCK

```
# FF97 = SERVER_CLOCK. Handler cliente RECV 0x57F4E0: lee [len:1][string ascii], lo parsea como
# NÚMERO (atoi vía 0x409D30) y lo consume en 0x4C4E20 -> objeto jugador [0x766640]/[0x766778].
# Handler LIMPIO: sin SetTimer ni FF3D (a diferencia de FF8C). El cliente lo SUBE vacío (request)
# en la entrada al lobby -> probamos responder con un reloj numérico (string decimal).
# Valor = tick del server en ms (fácil de cambiar si el formato resulta otro).
```

### <a name="paquetes-ff8c-anti-tamper-de-los-tiempos-de-las-11"></a>FF8C = anti-tamper de los TIEMPOS de las 112 animaciones de "fallo" (실패)

```
# FF8C = anti-tamper de los TIEMPOS de las 112 animaciones de "fallo" (실패). El validador
# cliente 0x56BD80 exige EXACTO 112 float32 y compara cada uno BIT-EXACTO contra [anim+0x18],
# que es el float en offset 0x04 del .ani (FPS de la animación). Los 112 valores, en el orden
# de la tabla cliente 0x73DDF8, son un patrón de 14 (left, left_long, right, right_long, up,
# up_long, down, down_long, downleft, downright, upleft, upright, big, fly) repetido ×8
# (boy, girl, girl2, boy2 × frente/espalda). Verificado leyendo los .ani reales del cliente
# (experimentos/client-clean/rnr_ani[2]/*.ani -> float@0x04). Ver [[server-clean-reconstruction]].
```


---

## pktlog.py

### <a name="pktlog-estado-de-los-inner-cmds-c-s-informativos-s"></a>── ESTADO de los inner-cmds C->S informativos / sin handler (re-escaneo ) ──

```
# ── ESTADO de los inner-cmds C->S informativos / sin handler (re-escaneo 2026-06-22) ──
# Confirmado por RE: NINGUNO dispara/depende de otro FF (son self-contained). No bloquean el flujo.
#   0x59 CLIENT_TELEMETRY  -> ACTIVO · NO requiere handler. El cliente SIEMPRE lo manda: payload ASCII
#                             "a\0USN:<n> TIME:<fecha> SERVER:<canal> + logs c2DImage::LoadImage(...)".
#                             Telemetría/diagnóstico fire-and-forget. No-op del server = CORRECTO. (Familia FF107.)
#   0x54 CLIENT_NOTIFY54   -> INACTIVO · sin implementar. Notificación vacía (len=0), propósito sin confirmar.
#                             No espera respuesta; el cliente no se cuelga. Solo se loguea.
#   0x40 SESSION_FORWARD   -> NO IMPLEMENTADO (feature opcional). Canal de ANUNCIOS/chat/susurros: display de
#                             strings (handler cliente sub_415F50; legacy: case 0x0D=bocina, 0x0F=whisper).
#                             Sin él: no hay anuncios/chat global (cosmético). No bloquea.
```


---

## progresion.py

### <a name="progresion-licencia-forzada-en-libre-pedido-del-us"></a>Licencia FORZADA en Libre (pedido del usuario)

```
# ★ Licencia FORZADA en Libre (pedido del usuario). Mapeo verificado contra el propio selector del juego,
#   que lista en este orden: 1 Novato · 2 Novato Estrella · 3 Estrella · 4 Novato Lunar · 5 Luna ·
#   6 Novato Solar · 7 SOL. Es un TOPE (incluye todo lo inferior). R2_LIBRE_LIC lo cambia sin tocar código.
# ★ 2026-08-02: default a **0 = usar la licencia REAL del jugador**. Estaba clavado en 7 (Sol), así
#   que el selector de Libre marcaba "Sol" para todo el mundo sin importar su nivel. El tope tiene
#   que ser la banda propia. `R2_LIBRE_LIC=7` vuelve al forzado (Libre = todos juegan todo).
```

### <a name="progresion-bisect-del-cartel-roto-solo-en-libre-se"></a>BISECT del "cartel roto SOLO en Libre" . Server-side, el camino del cartel es IDÉNTICO en...

```
# ★★ BISECT del "cartel roto SOLO en Libre" (2026-08-01). Server-side, el camino del cartel es IDÉNTICO en los 4
#   canales: lo ÚNICO que difiere en Libre son estas DOS cosas. Con ambas neutralizadas, Libre queda byte-por-byte
#   igual a Sol -> si el CUADRO EXTRA del cartel sigue apareciendo, NO lo causa el server.
#     R2_LIBRE_FF13=0  -> no mandar el FF13 status 0x09 extra del JOINCHANNEL
#     R2_LIBRE_LIC=7   -> mandar la MISMA licencia que los otros canales (LICENSE_BYTE) en vez de 0x06
#   ⚠ Neutralizarlos APAGA a propósito la "banda solar libre" (el selector de licencia y el picker sin gate por
#     nivel). Es para MEDIR, no un estado final: con FF13=0 el selector de categorías no aparece.
# ★★★ 2026-08-01 (noche) — MEDIDO CON FRIDA: nuestro propio FF13 le pone la licencia en CERO.
#   Sonda sobre `SetLicense` (0x52BBF0): al entrar a Libre llega `SetLicense(tier=0)` DESDE `0x57844F`,
#   que es el handler del FF13. El disasm confirma que el byte que viaja a SetLicense es `byte [esp+9]`
#   (0x578429) — y el status 0x09 que ponemos en el offset 40+N NO cae ahí. Resultado: `[0x6d36] = 0`
#   = SIN BANDA. Ése es el estado en el que el selector arranca sin marcar y el cartel se dibuja mal.
#   ★ Y el FF13 NO hace falta para el selector: el modo libre lo activa el CLIENTE solo, por el puerto
#     48035 (0x66B0A0 fuerza campoA=0xC -> guard `[0x6d98]`=0x1FE3, verificado en vivo por la sonda).
#   ⇒ POR DEFECTO YA NO SE MANDA. R2_LIBRE_FF13=1 lo reactiva (para volver a medirlo, no para jugar).
```

### <a name="progresion-muro-de-canal-resuelto"></a>MURO DE CANAL — RESUELTO

```
# ★★ MURO DE CANAL — RESUELTO (RE 2026-07-06, bucket_trace.py + r2). El auto-connect del login corre el
# MATCHER 0x46b0e0(bucket): itera la lista FF02 y toma la 1ª entrada cuyo (fieldA=byte+1, fieldB=byte+0x34)
# —decodificados del STATUS por 0x56FEEA— cumpla el criterio del BUCKET del jugador (0x451480 sobre su exp,
# tabla 0x6E90B8). Mapa COMPLETO bucket -> criterio -> STATUS que lo satisface:
#   b1: fieldA==3            -> status 0x01
#   b2: fieldB==2            -> status 0x04
#   b3: fieldA==0 & fieldB==0-> status 0x0F   (Estrella clásico)
#   b4: fieldB==3            -> status 0x05
#   b5: fieldA==1 & fieldB==0-> status 0x0C   (Luna clásico)
#   b6: fieldB==4            -> status 0x07
#   b7: fieldA==2 & fieldB==0-> status 0x08   (Sol clásico)
# Buckets por nivel (word=0 del wrapper 0x452610): 1-10->1 11-20->2 21-30->3 31-40->4 41-60->5 61-70->6 71+->7.
# Antes solo mandábamos 0x0F/0x0C/0x08 (buckets 3/5/7) => niveles 1-20,31-40,61-70 no matcheaban => CUELGUE.
# FIX server-side (sin tocar el exe; 0x451480/0x452610 están MUY compartidos, no se pueden parchear): por cada
# lobby mandamos VARIAS entradas al MISMO puerto con los status que cubren su banda de niveles. Agrupación
# pedida por el usuario (2026-07-06): Estrella=niveles 1-30 (b1,2,3) · Luna=31-60 (b4,5) · Sol=61+ (b6,7).
#   ⚠ VÍA SERVER-SIDE DESCARTADA (probado en vivo 2026-07-06): el cliente DEDUPLICA los canales por fieldA
#   (categoría: 0=Estrella 1=Luna 2=Sol 0xC=Libre) quedándose con el ÚLTIMO fieldB, y DESCARTA fieldA no
#   reconocido (p.ej. status 0x01=fieldA 3 se cae). Mandar multi-status no cubre buckets 1,2,4,6 y encima
#   rompe el fieldB=0 de los clásicos. La solución es el PARCHE de 7 bytes en el matcher 0x46b0e0 del exe
#   (mapea buckets huérfanos a su categoría: b1,b2->Estrella b4->Luna b6->Sol). Ver docs/RECOMPENSAS_Y_CANALES.md.
#   Por eso aquí cada lobby lleva SOLO su status clásico (1 nodo por categoría, como el cliente espera).
# ★★★★★ 2026-08-01 — REABIERTA LA VÍA SERVER-SIDE (para poder sacar la cueva del muro de canal).
#   Se había descartado con "el cliente DEDUPLICA los canales por fieldA y se queda con el último".
#   ESO ERA UN DIAGNÓSTICO EQUIVOCADO: no deduplicaba por fieldA — colapsaba porque mandábamos el
#   **u32 de CLAVE en 0 en TODAS las entradas** (ver el comentario del `struct.pack("<I", chan_id)` más
#   abajo): el `0x46D4B0` hace *buscar-o-insertar* por `low16(A)` y cada entrada pisaba a la anterior.
#   Con clave única por canal (ya arreglado y verificado: salen los 4 canales) el multi-status debería
#   entrar bien, y entonces la cueva `0x452610`→`0x6E52A0` (que colapsa el bucket a 3/5/7) SOBRA.
#   Criterio de cada bucket del matcher 0x46B0E0 -> status que lo satisface:
#     b1 campoA==3 -> 0x01 · b2 campoB==2 -> 0x04 · b3 campoA0&campoB0 -> 0x0F (Estrella clásico)
#     b4 campoB==3 -> 0x05 · b5 campoA1&campoB0 -> 0x0C (Luna) · b6 campoB==4 -> 0x07
#     b7 campoA2&campoB0 -> 0x08 (Sol)
#   R2_MULTISTATUS=0 vuelve a 1 status por canal (el esquema que necesita la cueva).
```

### <a name="progresion-hallazgo-el-puerto-48035-est-horneado-e"></a>HALLAZGO: **el PUERTO 48035 está HORNEADO EN EL CLIENTE**

```
# ★★★ HALLAZGO 2026-08-01 (RE de 0x467A70 -> thunk 0x66B0A0): **el PUERTO 48035 está HORNEADO EN EL CLIENTE**.
#   0x66B0A0:  mov eax,[ecx+0xf24] ; movzx eax,ax ; cmp eax,0xbba3 ; jne +2 ; mov bl,0xc ; jmp 0x467a75
#   0xbba3 = 48035. Si el puerto del canal es 48035, el cliente **FUERZA campoA = 0xC** ignorando el status que
#   mandemos. Es la ÚNICA aparición de esa constante en todo el exe = así identifica el cliente al canal libre.
#   ⇒ Corolario: `R2_LIBRE_STATUS` NO puede volver "normal" a Libre — el cliente lo pisa. Lo ÚNICO que saca al
#     cliente del modo libre es CAMBIARLE EL PUERTO. Por eso existe R2_LIBRE_PORT: mismo canal, mismo trato del
#     server, otro puerto ⇒ el cliente deja de reconocerlo como libre. Es LA prueba que aísla el modo libre.
```

### <a name="progresion-crash-del-men-viene-de-la-lista-que-rec"></a>crash del menú viene de la lista que recorre el picker (EDI=0xffff, stride 0x2cc)

```
# crash del menú viene de la lista que recorre el picker (EDI=0xffff, stride 0x2cc).
# Canales-servidor (FF02 LOBBYSERVER_LIST, handler cliente 0x56FD70). Los 3 apuntan al MISMO lobby
# (LOCAL_IP:LOBBY_PORT); difieren en chan_id, nombre y STATUS. STATUS con type-extra=0 => canal NORMAL
# (no SOL): 0x0F=tier0(novato), 0x01=tier3, 0x0C=tier1. El cliente fija [chan+0x13E5]=type-extra al
# hacer FF06 JOINCHANNEL; type 0 => la lista de salas usa la rama NON-SOL (la que renderiza build_roomlist).
# STATUS -> ícono de CANAL (switch 0x56FEEA -> tier en entry+1): 0x03=estrella VERDE (novato, además
# setea flag novato [0x766608+0x11f1]=1), 0x0F=estrella AMARILLA (tier0), 0x0C=LUNA (tier1=moon),
# 0x08=SOL(tier2). type-extra=0 = NORMAL (no SOL) -> roomlist rama NON-SOL.
# ★★ BISECT (3ª pata, 2026-08-01). El status del canal es la TERCERA cosa que hace distinto a Libre — se me pasó
#   en el primer bisect (que sólo neutralizó el FF13 extra y la licencia FFC9, y el cartel siguió roto).
#   El cliente decodifica el status a (campoA, campoB) en 0x56FEEA:
#       0x0F→(0,0) Estrella · 0x0C→(1,0) Luna · 0x08→(2,0) Sol · **0x09→(0xC,0) Libre**
#   Es decir: TODOS los canales normales dan campoA 0/1/2, y Libre da campoA = 0xC = **12**. Si el cliente usa
#   campoA como índice de tabla sin validar el techo, 12 se sale del array.
#   R2_LIBRE_STATUS=0x08 le da a Libre el MISMO status que Sol (campoA 2) SIN cambiarle el puerto (48035).
#   ⚠ Con 0x08, Libre deja de ser "libre": el matcher lo trata como canal Sol. Es para MEDIR, no un estado final.
# ★★★★★ 2026-08-01 (noche) — CÓMO ES EL CANAL LIBRE DE VERDAD (RE del gate de entrada 0x467870):
#   El decodificador de status 0x56FEEA tiene una rama que nadie había leído:
#       0x56FFE0  cmp al,0x47 / 0x48 / 0x49
#       0x56FFEC  sub al,0x47            -> campoA = 0 / 1 / 2   (banda NORMAL: estrella/luna/sol)
#       0x56FFEE  mov byte [esp+0x18], 6 -> campoB = 6
#   Y campoB==6 es lo que abre las dos puertas:
#     (a) ENTRADA SIN BANDA: el gate `0x467870` saltea el chequeo de licencia si `0x464820` devuelve 0,
#         y esa función es literalmente `return [canal+0x29dc] != 6`. Con 6 => entra cualquiera, NATIVO.
#         (El camino normal compara mi letra de licencia -> banda (`0x4519C0`) contra el campoA del canal;
#          si no matchea, cartel de error 443/444 y no entra.)
#     (b) EL CARTEL EN MODO LIBRE: el handler FF28 hace `cmp al,6` sobre `[[0x766608]+0x13e5]` (=campoB) y,
#         si es 6, lee **8 BYTES EXTRA (2×u32)** entre el winteam y el count.
#   ⇒ El canal libre REAL = **campoA normal + campoB 6**, no campoA 12.
#   Lo que veníamos usando (status 0x09 -> campoA 0xC=12, campoB 0) es OTRA cosa, sostenida a mano con una
#   cueva de código en el exe (0x467A70 -> 0x66B0A0) que fuerza campoA=12 al ver el puerto 48035. Con 12 el
#   jugador queda SIN banda: sin ícono, sin emblema en el cartel.
#   ⚠ Si se pone 0x47/0x48/0x49 hay que mandar TAMBIÉN los 8 bytes extra del cartel (ver `_encode_result`),
#     o el cliente lee 8 bytes de más y desfasa TODO el cartel. Ésa es la mitad que faltaba.
#   R2_LIBRE_STATUS=0x09 vuelve al esquema viejo (campoA 12 + cueva).
# ★ 2026-08-02: default de vuelta a 0x09. Con 0x47/0x48/0x49 el campoB queda en 6 = CIRCLE BATTLE, y
#   el renderer de la lista `0x46A140` **saltea a propósito** toda entrada con campoB==6 (dos sitios,
#   bytes idénticos al cliente virgen) => Libre DESAPARECÍA de la lista. Era un experimento, no el default.
```

### <a name="progresion"></a>══════════════════════════════════════════════════════════════════════════════════════════...

```
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# ★★★ LICENCIA DEL PICKER (FFC9) — RESTAURADA AL COMPORTAMIENTO HONESTO (2026-08-01)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# QUÉ ESTABA ROTO: el FFC9 mandaba la CONSTANTE `LICENSE_BYTE = 7` (Sol máximo) a TODO el mundo, sin
# mirar su nivel. Eso desbloqueaba toda la música para cualquiera — se dejó así a propósito, pero es
# el sistema de bandas anulado. El server YA calculaba la banda real (`tier_from_exp`) y la usaba para
# el MyRecord y para elegir canal; sólo esta licencia estaba clavada.
#
# ⚠ POR QUÉ IMPORTA MÁS ALLÁ DE LA MÚSICA: la licencia/banda es lo que le da al jugador su CATEGORÍA.
#   En el canal Libre la categoría llega como `0xC` (= "ninguna banda", ver [[r2beat-picker-mechanism]]),
#   y ése es el estado en el que el cartel de resultado se dibuja sin emblema y con los sprites corridos.
#
# Tiers del cliente (verificados): 1=novato · 2=novato-estrella · 3=ESTRELLA · 4=novato-lunar ·
#   5=LUNA · 6=novato-solar · 7=SOL. Es un TOPE: incluye todo lo inferior.
```
