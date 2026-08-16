# Proxmox NAS GUI
AI kódolással készült / AI generated code!

🇬🇧 English: [README.md](README.md)

Unraid-stílusú SMB (Samba) megosztás- és **mergerfs pool**-kezelő webes
felület Proxmox VE-hez.

A Proxmox-ból hiányzik az Unraid kényelmes, kattintható Samba-kezelése. Ez a
projekt ezt pótolja: megosztásokat, felhasználókat és csoportokat hozhatsz
létre pár kattintással, az Unraidből ismert **Export** és **Security**
(Public / Secure / Private) modellel — kiegészítve csoportkezeléssel, amely
az Unraidben nincs is. Emellett [mergerfs](https://github.com/trapexit/mergerfs)
poolokat is kezel: több diszket/mappát fűzhetsz össze egyetlen nagy
fájlrendszerré (mint az Unraid array), és azt azonnal megoszthatod.

![Megosztások lista](docs/hu/shares.png)

## Funkciók

- **Megosztások**: létrehozás/szerkesztés/törlés, útvonal-tallózóval;
  új mappa vagy **ZFS dataset** létrehozása közvetlenül a felületről
- **Export**: `Nem` / `Igen` / `Igen (rejtett)` — a rejtett megosztás működik,
  de nem látszik a hálózat tallózásakor
- **Hálózati böngészés Windows alatt**: a telepítő az `smbd` mellett az
  `nmbd`-t (NetBIOS böngészés) és a `wsdd2`-t (WS-Discovery) is elindítja,
  hogy a gép megjelenjen a Windows Intéző "Hálózat" nézetében — enélkül a
  megosztások `\\<IP>\<megosztás>` útvonallal kézzel elérhetők, csak a
  böngészős lista nem mutatja a gépet
- **Biztonsági módok** (az Unraid pontos megfelelői):
  - **Publikus** — bárki, jelszó nélkül, írás/olvasás
  - **Védett (Secure)** — vendégek olvashatnak, írásjog felhasználónként /
    csoportonként adható (`write list`)
  - **Privát** — csak a kijelölt felhasználók/csoportok
    (`valid users` + `write list`)
- **Felhasználók**: rendszer- + Samba-felhasználó egy lépésben
  (`useradd` + `smbpasswd`), jelszóváltás, törléskor teljes takarítás
- **Csoportok**: POSIX csoportok tagságkezeléssel, a jogosultsági mátrixban
  `@csoport` néven
- **Jogosultsági mátrix mindkét irányból**: a megosztás oldalán a
  felhasználók/csoportok listája, a felhasználó/csoport oldalán a
  megosztások listája — ugyanaz az adat két nézetben
- **Lomtár** megosztásonként (`vfs_recycle`): a hálózatról törölt fájlok a
  `.Recycle.Bin`-be kerülnek, a felületről üríthető
- **Lemezalvás-kezelés**: lemezenként állítható tétlenségi idő, kézi
  altatás, kereshető állapotnapló és figyelmeztetés arról, mi tartja ébren
  az adott lemezt (lásd lentebb)
- **Kétnyelvű felület** (magyar/angol, egy kattintással váltható)
- **PAM bejelentkezés** a hoszt root jelszavával, HTTPS-en

### mergerfs poolok

- **Pool létrehozás/szerkesztés**: branch-ek (diszkek/mappák) hozzáadása
  tallózóval vagy egy kattintással a kezelt diszk-mountokból, branch-enként
  RW / RO (csak olvasás) / NC (nincs új fájl) móddal
- **Diszkek felcsatolása** a felületről: a GUI listázza a fájlrendszerrel
  rendelkező, még nem csatolt partíciókat, és fstab-bejegyzéssel
  a `/mnt/disks/<név>` alá csatolja őket
- **Üres lemezek/partíciók formázása**: a fájlrendszer nélküli eszközök
  külön listában jelennek meg; a felhasználó ext4 vagy xfs fájlrendszert
  választhat, a GUI `wipefs`+`mkfs`-sel formázza (partíciós tábla
  létrehozása nélkül, közvetlenül az eszközre), majd azonnal fel is
  csatolja. A Proxmox rendszerlemez (és minden rajta lévő partíció/LVM
  kötet) sosem jelenik meg egyik listában sem
- **Presetek + haladó mező**: create policy (mfs, epmfs, ff, pfrd, …) rövid
  magyarázatokkal, minimális szabad hely, `moveonenospc`, plusz szabad
  szöveges mező bármely további mergerfs opcióhoz
- **Gyorsítótár-beállítások** külön mezőkön, mert ezek döntik el az írási
  sebességet: `cache.files` (alapból `auto-full`), `cache.writeback`
  (kis írások összevonása) és `dropcacheonclose`. A `cache.files=off`
  direct_io-t jelent — nincs lapgyorsítótár és nincs osztott mmap sem,
  amitől az mmap-ot használó programok (qBittorrent/libtorrent 2.x,
  sqlite) hibára futnak, az apró darabokban írók pedig a lemez
  képességének töredékét hozzák. A Diagnosztika külön figyelmeztet rá,
  ha egy pool mégis így fut
- **IO passthrough**: ha a kernel (6.9+) és a mergerfs (2.41+) is tudja, a
  `passthrough.io` a mergerfs folyamat teljes kihagyásával, közel natív
  sebességgel olvas/ír. A Diagnosztika észreveszi, ha rendelkezésre áll, és
  egy gombbal be is kapcsolja — a vele ütköző beállításokat (moveonenospc,
  írás-összevonás) kikapcsolva és a poolt újracsatolva
- **Kihasználtsági nézet**: pool- és branch-enkénti (diszkenkénti)
  tárhely-sávok, mint az Unraid Main füle
- **Élő átkonfigurálás**: felcsatolt pool esetén a branch-lista és a
  beállítások a mergerfs xattr-vezérlőjén át azonnal érvényre jutnak,
  újracsatolás nélkül — Samba-használat közben is bővíthető a pool
- **„Megosztás létrehozása ebből a poolból”** gomb: a share-szerkesztő a
  pool csatolási pontjával előtöltve nyílik
- **Törlésvédelem**: amíg egy megosztás a poolra mutat, a pool nem
  törölhető és nem választható le; pool törlésekor a branch-eken lévő
  adatok érintetlenek maradnak

### Bind mountok — prezentációs fa

A fizikai tárolás és az, amit a felhasználók böngésznek, nem kell, hogy
ugyanaz legyen. Ha a fontos adatok egy ZFS poolon (`/mnt/fontos`), a nem
fontosak meg egy mergerfs poolon (`/mnt/bulk`) vannak, az alapból két külön
csatolási pont, tehát két külön megosztás. Bind mounttal viszont egyetlen
fába fűzhetők:

```
/mnt/fontos/kz  →  /mnt/family_pool/kz/fontos
/mnt/bulk/kz    →  /mnt/family_pool/kz/nemfontos
…
```

Így elég **egyetlen megosztást** kiadni a `/mnt/family_pool`-ra, és a
felhasználó a `kz/fontos`, `kz/nemfontos` szerkezetet látja — miközben a
háttérben a kettő két külön fájlrendszeren ül.

- **Sablon-generátor**: megadod a prezentációs gyökeret, a mappaneveket
  (pl. `kz, kzs, kv`) és a szinteket (`fontos` → `/mnt/fontos`,
  `nemfontos` → `/mnt/bulk`), és a GUI élő előnézettel kiszámolja mind a
  6 bind mountot — jelezve, mely forrásmappák hiányoznak még
- **Hiányzó forrásmappák létrehozása** egy pipával: ZFS dataset alatt
  datasetként, egyébként sima mappaként
- **Fa-nézet**: a logikai szerkezet mellett minden levélnél ott a valós
  forrás és a mögötte lévő tároló (`POOL: bulk`, `fs: /mnt/fontos`)
- **Csak olvasható** bind mount, ha a fát nézegetésre adod ki
- **Törlésvédelem mindkét irányban**: a bind nem törölhető, amíg megosztás
  mutat a céljára, és a pool/diszk sem választható le, amíg egy bind onnan
  veszi a forrását — még akkor sem, ha a megosztás csak a bind célján
  keresztül, közvetve függ tőle

| Megosztás szerkesztése | Felhasználó + jogosultsági mátrix |
|---|---|
| ![Share szerkesztő](docs/hu/share-edit.png) | ![User szerkesztő](docs/hu/user-edit.png) |

| mergerfs poolok | Pool szerkesztése |
|---|---|
| ![Poolok](docs/hu/pools.png) | ![Pool szerkesztő](docs/hu/pool-edit.png) |

### Lemezek alvó állapota

A Proxmoxból hiányzik az Unraid-szerű lemezalvás-kezelés, a `hd-idle` pedig
kézzel írt konfigot igényel (`/etc/default/hd-idle`), amit lemezcserénél át
kell írni — és csak azt naplózza, amit ő maga altatott el. Ez az oldal
mindhármat kiváltja.

- **Tétlenségi idő lemezenként**, legördülőből: 15/30/45 perc, 1–6 óra, vagy
  „Soha". A beállítás a lemez `/dev/disk/by-id` nevéhez kötődik, nem a
  `/dev/sdX`-hez — az újraindításkor változhat, és akkor egy másik lemezre
  vonatkozna a házirend
- **Kézi altatás** a tétlenségi idő lejárta előtt, egy gombbal
- **Kereshető napló**: minden elalvás és ébredés bekerül egy helyi SQLite
  adatbázisba (`/var/lib/proxmox-nas-gui/disk-events.db`), okkal együtt —
  tétlenségi idő, kézi altatás (a felhasználó nevével), vagy külső. Szűrhető
  lemezre, eseményre, okra és szövegre, lapozva
- **24 órás idővonal** lemezenként, az események átmeneteiből számolva
- **Élő írási/olvasási sebesség** a pörgő lemezeknél, 2 másodpercenként
  frissítve. A számlálók a `/proc/diskstats`-ból jönnek (kernelmemória, nulla
  lemez-I/O), a sebességet a böngésző számolja két minta különbségéből — így
  a lekérdezés maga sosem ébreszt fel semmit, és egy háttérbe tett fül sem
  torzítja az értéket. A szám mellett **tükrözött sparkline** mutatja az
  utolsó 2 percet — az olvasás a középvonal fölött, az írás alatta, közös
  skálán. Így ránézésre elkülönül a löketszerű forgalom (mentés, scrub) az
  egyenletestől, és rögtön látszik az is, ha egy „alvó" lemezre folyamatosan
  írás érkezik
- **Hőmérséklet**: a pörgő lemezeknél az aktuális érték a kártyán, a küszöbök
  szerint színezve; alvó lemeznél az utolsó ismert érték a korával együtt.
  A figyelő 5 percenként mintát vesz és adatbázisba írja, külön
  **Hőmérséklet** fülön grafikonnal (24 óra / 7 nap / 30 nap / 1 év),
  lemezenkénti min–átlag–max táblázattal és CSV exporttal
- **Figyelmeztetések**: a GUI megnézi, mi tarthatja ébren az adott lemezt, és
  ahol biztonságos, egy gombbal ki is javítja

![Lemezek alvó állapota](docs/hu/sleep.png)

#### Hogyan altat, és miért nem hd-idle-lel?

A `hdparm -y` az ATA STANDBY IMMEDIATE parancsot küldi, amit sok lemez (és
gyakorlatilag minden USB/SAS híd) figyelmen kívül hagy — ráadásul hiba nélkül,
tehát a parancs sikeres kilépése nem bizonyíték. A GUI ezért sorban próbálja a
`hdparm -y` → `sg_start --stop` (SCSI START STOP UNIT) → `sdparm --command=stop`
parancsokat, és **mindegyik után `hdparm -C`-vel ellenőrzi**, hogy a lemez
tényleg készenlétbe került-e. A működő módszert lemezenként megjegyzi, így a
következő altatás már egyetlen parancs.

A tétlenséget a `/proc/diskstats` számlálói adják (kernelmemóriából, nulla
I/O), az energiaállapotot a `hdparm -C` — az ATA CHECK POWER MODE parancsra a
lemez az elektronikájából válaszol, nem pörög fel tőle. **Maga a figyelés
tehát soha nem ébreszti fel a lemezeket.** A figyelő a webalkalmazás
folyamatában fut (egy uvicorn worker), nem külön szolgáltatásként.

Ha a `hd-idle` fut, az oldal tetején figyelmeztetés jelenik meg: amíg fut, ő
is altat, és a napló hiányos marad. Az „Átvétel" gomb leállítja és letiltja,
a `HD_IDLE_OPTS` sorból pedig átveszi a lemezenkénti időzítéseket (a legközelebbi
felkínált értékre kerekítve, amiről jelzést is ad).

#### Hőmérséklet mérése alvó lemez felébresztése nélkül

Ez az egyetlen mérés, ami nem ingyenes: a tétlenség a `/proc/diskstats`-ból,
az energiaállapot a `hdparm -C`-ből jön, de a hőmérséklet valódi
SMART-lekérdezés — pontosan az, amiről a lentebbi `smartd`-figyelmeztetés azt
írja, hogy felpörgeti a lemezt. Ezért két, egymástól független védelem van:

1. **A figyelő csak ébren lévő lemezt kérdez le.** Az energiaállapotot
   ugyanabban a körben már megmérte `hdparm -C`-vel; alvó lemezre a parancsot
   **el sem indítja**.
2. **`smartctl -n standby`** a hálószem, ha a lemez a két lépés között aludt
   el: ilyenkor a smartctl 2-es kóddal kilép anélkül, hogy felpörgetné.

Az adatbázisba **csak valódi mérés** kerül. Egy alvó lemez smartctl-válaszában
a hőmérséklet gyakran `0` — ez „nincs adat", nem fagypont, különben minden
átlagot lehúzna. Ennek szép mellékhatása: **a grafikonon a lyukak maguk az
alvási időszakok**, külön jelölés nélkül látszik, mennyit hűt az altatás.

A rendszerlemez a Hőmérséklet fülön megjelenik (és csak ott): folyamatosan
pörög, tehát tipikusan az a legmelegebb a gépben. Vezérlőt nem kap, és
minden olyan listából kimarad, ami műveletet végezhetne egy lemezen.

#### Mi tartja ébren a lemezt?

Lemezenként az alábbiakat vizsgálja. A ✅ jelölt javítások egy kattintással
lefuttathatók a felületről; a többinél a parancs megjelenik, de szándékosan
nem futtatjuk le — vagy nem ennek az alkalmazásnak a hatásköre, vagy olyan
kompromisszum, amit csak a felhasználó mérlegelhet.

| Ellenőrzés | Miért ébreszt | Javaslat | |
|---|---|---|---|
| `smartd` | 30 percenként SMART-adatot olvas, ami felpörgeti az alvó lemezt | `-n standby,q` a `/etc/smartd.conf` eszközsorára | ✅ |
| ZFS `atime` | `atime=on` mellett az olvasás is írást vált ki | `zfs set atime=off <pool>` | ✅ |
| Csatolási opciók | `relatime` mellett az olvasás is ír a lemezre | `noatime` a GUI saját fstab-sorába + élő remount | ✅ |
| mergerfs gyorsítótár | minden könyvtárlistázás megérinti az összes branch-et | `cache.statfs=60,cache.attr=300,cache.entry=300` | |
| `zfs-auto-snapshot` | negyedóránkénti pillanatkép = metaadat-írás | `zfs set com.sun:auto-snapshot=false <dataset>` | |
| Proxmox-tároló | a `pvestatd` 10 másodpercenként lekérdez minden tárolót | `pvesm set <id> --disable 1` | |
| ZFS scrub | havonta órákra ébren tartja a pool minden lemezét | csak tájékoztatás | |
| ZFS zvol | futó VM/konténer folyamatos I/O-t okoz | csak tájékoztatás | |
| `updatedb` | a napi indexelés végigjárja a fájlrendszert | az útvonal felvétele a `PRUNEPATHS` közé | |

**A mergerFS-ről őszintén:** a mergerfs
[saját dokumentációja](https://github.com/trapexit/mergerfs/wiki/Limit-Drive-Spinup)
kimondja, hogy pool szinten nem lehet megbízhatóan megakadályozni a
felpörgést — a mergerfs proxy, nem gyorsítótár, és egy `readdir` definíció
szerint minden branch-et megérint. A cache-opciók csak az *ismétlődő*
metaadat-kéréseket szűrik. A valódi tanács ezért az, hogy indexelőt,
médiaszervert vagy mentést a mögöttes útvonalra irányíts, ne a poolra.

![Hőmérséklet-előzmény](docs/hu/temps.png)

| Mi tartja ébren? | Állapotnapló |
|---|---|
| ![Figyelmeztetések](docs/hu/sleep-warnings.png) | ![Napló](docs/hu/sleep-log.png) |

**A rendszerlemez** — ahogy a formázásnál is — meg sem jelenik az oldalon.
Három szabály zárja ki: a csatolási pont (`/`, `/boot`, LVM-en át is), az
aktív swap, és ZFS-gyökér esetén a `findmnt` + `zpool list` alapján
azonosított pooltagok. Ez utóbbi külön szabály, mert egy `zfs_member`
partíciónak nincs csatolási pontja, tehát az első kettő nem fogná ki.

### Diagnosztika

Egy gombbal végigfut minden ellenőrzés a poolokon, bind mountokon,
megosztásokon, diszk-mountokon, systemd egységeken és a lemezalváson. Amit
biztonságosan meg lehet javítani, azt egy gomb el is végzi (a többinél a
kimásolható parancs jelenik meg).

A teljesítménnyel kapcsolatos ellenőrzések, mert ezek nem hibaként
jelentkeznek, hanem csak lassúságként:

- **`cache.files=off`**: a pool gyorsítótár nélkül fut, ami elveszi az mmap
  támogatást és minden apró írást külön oda-vissza úttá tesz
- **A futó mount eltér a beállítottól**: néhány mergerfs opciót
  (írás-összevonás, passthrough) csak felcsatoláskor lehet átvenni, ezért a
  mentés önmagában nem elég. Ez az ellenőrzés a mergerfs xattr-vezérlőjén
  keresztül összeveti a *ténylegesen futó* értékeket a mentettekkel — a
  javítás újracsatolja a poolt, és visszakapcsolja a rá épülő bind
  mountokat (ezek a pool leállításakor magukkal együtt leállnak)
- **Elavult mergerfs**: a kernel tudna IO passthrough-t, a telepített
  mergerfs viszont nem. A disztró csomagja évekkel le szokott maradni
- **Elérhető IO passthrough**: minden adott hozzá, de a poolon nincs
  bekapcsolva. A javítás bekapcsolja, kikapcsolja a vele ütközőket
  (moveonenospc, írás-összevonás), és újracsatol

![Diagnosztika](docs/hu/diag.png)

## Telepítés

Proxmox VE hoszton (vagy bármely Debian-alapú rendszeren, LXC-ben is), rootként:

```bash
git clone https://github.com/kzkz22/proxmox-nas-gui.git
cd proxmox-nas-gui
./deploy/install.sh
```

Ezután a felület a `https://<hoszt-ip>:8481/` címen érhető el, a belépés a
hoszt **root** jelszavával történik. (A tanúsítvány önaláírt, a böngésző
egyszer figyelmeztetni fog.)

A telepítő a mergerfs-t az apt-ból rakja fel, majd — ha a disztró csomagja
régebbi — felülírja az upstream kiadással. A Debian csomagja évekkel le van
maradva (bookworm: 2.33.5, trixie: 2.40.2), és ami hiányzik, az számít: a
`passthrough.io` a 2.41.0-ban jelent meg. A mergerfs saját dokumentációja is
a releases oldalról való telepítést ajánlja. Ha nincs hálózat, vagy nincs
build az adott disztróhoz, az apt-os verzió marad és a telepítés folytatódik.

### Frissítés: változás a pool gyorsítótár-alapértékein

Korábban a GUI minden poolt fixen `cache.files=off,dropcacheonclose=true`
opciókkal csatolt. Ez direct_io-t kényszerít: nincs lapgyorsítótár, és a
FUSE nem tud osztott mmap-ot sem adni — ezért futott hibára (`ENODEV`)
minden mmap-ot használó program, és ezért maradt az apró darabokban író
programok (torrent kliens) írási sebessége a lemez képességének töredékén.
Az alapértelmezés most `cache.files=auto-full`, `dropcacheonclose=false`,
`cache.writeback=false`.

A meglévő poolok a mentett beállításaikat tartják meg, de a fenti három
mezőt még nem tárolták — ezek az új alapértéket kapják, ami a pool
következő mentésekor vagy újracsatolásakor lép életbe. Ha a régi
viselkedés kell (pl. nagyon kevés RAM mellett), a pool szerkesztőjében a
fájl gyorsítótár állítható vissza `off`-ra.

## Hogyan működik?

- A beállítások *forrása* a `/etc/proxmox-nas-gui/state.json`, ebből
  generálódik determinisztikusan a `/etc/samba/proxmox-nas-gui.conf`.
- A meglévő `/etc/samba/smb.conf`-ot csak egyetlen `include` sorral egészíti
  ki (az eredetiről biztonsági mentés készül: `smb.conf.pnas-backup`).
- Minden módosítás előtt `testparm` validálja a teljes új konfigurációt egy
  ideiglenes másolaton — hibás beállítás soha nem kerülhet a futó Samba alá.
- Sikeres validálás után újratöltés `smbcontrol all reload-config`-gal,
  újraindítás nélkül.
- A megosztott mappák adatkezelése Unraid-módra történik: a megosztás
  gyökere a `nobody` felhasználóé (`force user = nobody`), a hozzáférést a
  Samba szabályozza — így a jogosultsági mátrix módosítása sosem igényel
  fájlrendszer-szintű chown/chmod futtatást a meglévő fájlokon.
- Megosztás törlésekor **a lemezen lévő adatok érintetlenek maradnak**.
- A mergerfs poolokat generált **systemd unit** indítja
  (`/etc/systemd/system/pnas-pool-<név>.service`, `RequiresMountsFor`-ral a
  helyes boot-sorrendhez) — szándékosan nem fstab-ból, mert a Debian 12-es
  mergerfs 2.33 mount-helpere elutasítja a generikus fstab-opciókat, a
  bináris közvetlen hívása viszont minden verzión működik. Egy hibás pool
  így sosem viszi emergency módba a hosztot.
- A diszk-mountok az `/etc/fstab`-ba kerülnek (`nofail`-lel), soronként
  `# pnas:disk:<név>` címkével — csak a saját sorainkat módosítjuk, az első
  íráskor biztonsági mentés készül (`fstab.pnas-backup`).
- A bind mountok szintén generált **systemd unitot** kapnak
  (`/etc/systemd/system/pnas-bind-<név>.service`), nem fstab-sort. Az fstab
  `x-systemd.requires-mounts-for=` opciója ugyanis egy `.mount` unitra várna,
  a mergerfs poolt viszont egy *service* csatolja — így nincs mire rendezni.
  A unit ezért közvetlenül a pool service-ét nevezi meg
  (`Requires=`/`After=pnas-pool-<név>.service`), minden más forrásnál pedig
  `RequiresMountsFor=` gondoskodik a sorrendről. Leálláskor a systemd
  fordított sorrendben állít, tehát a bind előbb válik le, mint a mögötte
  lévő tároló.
- **A bind unit legfontosabb sora az őrszem**:
  `ExecStartPre=/usr/bin/mountpoint -q <forrás mögötti mount>`. Enélkül egy
  még fel nem csatolt ZFS/mergerfs forrás esetén a `mount --bind` simán
  sikerülne — az alatta lévő **üres** mappára. A Samba ekkor üres megosztást
  adna, egy arra ráállított szinkron-eszköz pedig törlésként propagálhatná az
  ürességet. Az őrszem inkább nem csatol, mint hogy ez megtörténjen.
- Ezért is: a **Syncthingnek (és minden szinkron-eszköznek) a valós
  forrásútvonalat** add meg (`/mnt/fontos/kz`), ne a bind célját. A bind-elt
  fa a Samba (emberi böngészés) kényelmét szolgálja.
- Bind mount törlésekor csak a megjelenítés szűnik meg; a cél mappa üresen
  ottmarad, a forráson lévő adatok érintetlenek.
- A lemezalvás-figyelő a webalkalmazás folyamatában fut, 30 másodpercenként.
  A tétlenséget a `/proc/diskstats` számlálóiból, az energiaállapotot a
  `hdparm -C`-ből olvassa — egyik sem okoz lemez-I/O-t, tehát a figyelés maga
  soha nem ébreszt fel semmit. Az események
  `/var/lib/proxmox-nas-gui/disk-events.db`-be kerülnek (SQLite), ami a
  rendszerlemezen van.
- Az altatási házirendek a `/dev/disk/by-id` névhez kötődnek, nem a
  `/dev/sdX`-hez: az utóbbi felderítési sorrendben kap nevet, tehát
  újraindítás után más lemezre vonatkozhatna ugyanaz a beállítás.

## Konfiguráció (környezeti változók)

| Változó | Alapértelmezés | Leírás |
|---|---|---|
| `PNAS_ADMIN_USERS` | `root` | GUI-ba beléphető rendszerfelhasználók (vesszővel elválasztva) |
| `PNAS_STATE_DIR` | `/etc/proxmox-nas-gui` | A state.json könyvtára |
| `PNAS_SMB_CONF` | `/etc/samba/smb.conf` | A fő Samba konfig |
| `PNAS_GEN_CONF` | `/etc/samba/proxmox-nas-gui.conf` | A generált konfig helye |
| `PNAS_FSTAB` | `/etc/fstab` | A diszk-mountok fstab fájlja |
| `PNAS_SYSTEMD_DIR` | `/etc/systemd/system` | A generált pool-unitok könyvtára |
| `PNAS_LOG_DB` | `/var/lib/proxmox-nas-gui/disk-events.db` | A lemezállapot-napló adatbázisa |
| `PNAS_SMARTD_CONF` | `/etc/smartd.conf` | A smartd konfig (az alvás-ellenőrzéshez) |
| `PNAS_HD_IDLE_CONF` | `/etc/default/hd-idle` | A hd-idle konfig (átvételkor olvassuk) |
| `PNAS_PVE_STORAGE` | `/etc/pve/storage.cfg` | A Proxmox tárolókonfig (csak olvassuk) |
| `PNAS_UPDATEDB_CONF` | `/etc/updatedb.conf` | Az updatedb konfig (csak olvassuk) |
| `PNAS_CRON_DIR` | `/etc/cron.d` | Cron-bejegyzések könyvtára (csak olvassuk) |
| `PNAS_DISABLE_MONITOR` | – | `1` esetén az alvásfigyelő el sem indul (a tesztek ezt használják) |

A hőmérséklet-mérések ugyanabba az adatbázisba kerülnek, mint az
állapotnapló (`PNAS_LOG_DB`), külön táblába és külön megőrzési idővel
(alapból 1 év, szemben a napló 90 napjával).

## Fejlesztés

```bash
python3 -m venv venv && venv/bin/pip install -r backend/requirements-dev.txt
venv/bin/python -m pytest                   # egységtesztek
cd backend && ../venv/bin/uvicorn app.main:app --reload   # dev szerver
```

A `pytest` a repó gyökeréből fut; a `backend/` könyvtárat a `pyproject.toml`
teszi az import útvonalra.

### Felépítés

A kód három részre oszlik, a backendben és a frontenden ugyanúgy:

| | tartalom |
|---|---|
| `core/` | session, `state.json`, parancsfuttatás, mappa-tallózó, i18n, API-kliens |
| `samba/` | megosztások, felhasználók, csoportok, `smb.conf` generálás |
| `storage/` | mergerfs poolok, diszk-mountok, bind mountok, lemezalvás |

**A `samba/` és a `storage/` sosem importálja egymást, és a `core/` egyiket
sem.** Mindkét felet csak a kompozíciós gyökerek látják: `backend/app/models.py`
(a közös `State`), `routes.py`, `state_view.py`, illetve `frontend/main.js` és
`pages.js`. A `tests/test_layering.py` ezt ellenőrzi is.

A két rendszer két ponton találkozik, és mindkettő szándékosan a gyökerekben
él: a `core/deps.blockers_for_path()` mondja meg, hogy egy pool leválasztását
blokkolja-e valamelyik megosztás — a bind mountokat is végigkövetve, mert egy
megosztás a prezentációs fán keresztül közvetve is függhet egy pooltól —, a
pool-mountpointot és bind-célt jelölő badge-et pedig a `main.js` injektálja a
tallózóba.

A csomagokon belül mindenhol ugyanaz a kettősség: a tiszta, alrendszer nélküli
fél (`poolconf.py`, `bindconf.py`, `sambaconf.py`, `sleepconf.py`) egységben
tesztelhető, a valódi eszközökhöz és fájlokhoz nyúló fél (`pools.py`,
`binds.py`, `service.py`, `disksleep.py`) pedig ezekre épül.

Új oldal felvételéhez elég egy leíró a csomag `pages.js`-ébe; a navigáció és a
routing ebből generálódik.
