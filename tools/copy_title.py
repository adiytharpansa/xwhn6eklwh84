# Robot penyalin judul untuk GitHub Actions (berjalan di runner, token repo sendiri).
# v5.5 (5 Nov 2026): IKUT MENYALIN SUBTITLE sumber — trek EN + ID dari master
# sumber (dari master HLS DAN field 'tracks' respons API sumber — robot lama
# mengabaikan field itu seluruhnya) diambil sebagai berkas VTT datar
# (sub-en.vtt/sub-id.vtt) + playlist
# pembungkus (subs-en.m3u8/subs-id.m3u8), dan playlist.m3u8 episode menyatakan
# #EXT-X-MEDIA TYPE=SUBTITLES NAME="English"/"Indonesian" LANGUAGE en/id
# (pola yang sama dengan ongoing_lane yang terbukti di aplikasi). Tanpa
# subtitle sumber, tata letak identik v5.4 (degradasi anggun). Selebihnya v5.4:
# v5.4 (31 Okt 2026): push_episode DIPECAH per potongan (maks ~250 MB per push,
# playlist.m3u8 SELALU di potongan terakhir) — satu push tunggal film utuh
# (166-172 potongan, >2 GB) ditolak GitHub HTTP 500/RPC failed 5/5 (terbukti
# Scarlet & Neon Genesis Evangelion: The End of Evangelion). Push terpotong
# aman dilanjutkan: entri episode yang sudah ada di repo dibaca dulu (resume).
# v5.3 (31 Okt 2026): rotasi kandidat menembus kegagalan segmen (percobaan
# ulang episode memakai kandidat terverifikasi berikutnya), probing kandidat
# paralel (maks 6), putaran ambil segmen 3 -> 4. Selebihnya identik v5.2:
# v5.2 (31 Okt 2026): unggah via GIT PUSH dengan commit-tree — pohon baru dibangun
# hanya di jalur berkas yang berubah (hash-object + mktree + commit-tree), TANPA
# indeks repo. Pelajaran probe v5 (30 Okt 2026): git add/commit di atas indeks
# clone parsial repo raksasa (80+ GB) menggantung >30 mnt sampai timeout, sesudah
# unduhan sukses — v5.1 membuang langkah indeks itu seluruhnya.
# v5 (30 Okt 2026): unggah via git push dari clone parsial (--filter=blob:none,
# --depth 1, tanpa checkout), menggantikan Git Data API yang ditolak 403
# (batas sekunder) di langkah tree/commit/ref. Unduhan & resolusi identik v4:
# v4 (29 Okt 2026 malam): ambil playlist LANGSUNG dulu (Referer/UA), proxy Worker
# cadangan; v3: resume per episode; v2: coba-ulang per segmen, rotasi kandidat.
# Masukan lewat env: WID, SLUG_ID, ANILIST_ID, TOTAL_EPS, TITLE_NAME, GH_TOKEN, GH_REPO.
import json, os, shutil, subprocess, sys, time, urllib.parse
from concurrent.futures import ThreadPoolExecutor

WORKER = 'https://my-anime-app.adiyth-arpansa.workers.dev'
UA = 'Mozilla/5.0 (Linux; Android 14; SM-A155F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36'
WID = os.environ['WID']; SLUG_ID = os.environ['SLUG_ID']
TOTAL = int(os.environ['TOTAL_EPS']); NAME = os.environ.get('TITLE_NAME', WID)
ANILIST = int(os.environ.get('ANILIST_ID') or 0)
TOKEN = os.environ['GH_TOKEN']; REPO = os.environ['GH_REPO']
OUT = os.path.join(os.getcwd(), 'work', WID)
REPO_DIR = os.path.join(os.getcwd(), 'repo')
DIRECT_OK = {'v': None}  # None=belum tahu, True=langsung bisa, False=wajib proxy
HEAD_NOW = {'sha': None}  # commit induk berjalan; diperbarui sesudah tiap push sukses
EP_SUBS = {}  # v5.5: {nomor_ep: ['en','id']} bahasa subtitle yang tersalin

def log(*a): print(time.strftime('%H:%M:%S'), *a, flush=True)

def scrub(s):
    return (s or '').replace(TOKEN, '***')

def curl_bytes(url, tries=4, timeout=120, ref=None):
    cmd = ['curl', '-s', '-f', '--max-time', str(timeout), '-A', UA]
    if ref: cmd += ['-H', 'Referer: ' + ref]
    cmd.append(url)
    for t in range(tries):
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode == 0 and r.stdout: return r.stdout
    return None

def prox(u, ref): return WORKER + '/api/proxy?u=' + urllib.parse.quote(u, safe='') + '&ref=' + urllib.parse.quote(ref, safe='')

def anidap_json(url):
    for t in range(4):
        r = subprocess.run(['curl', '-s', '--max-time', '60', '-A', UA, '-H', 'Accept: application/json',
                            '-H', 'Referer: https://anidap.lol/home', url], capture_output=True, text=True)
        try: return json.loads(r.stdout)
        except Exception: time.sleep(3)
    return None

def parse_variant(master, stream_url, ref):
    """Pilih varian bandwidth tertinggi; kembalikan (teks_playlist, url_dasar, daftar_segmen)."""
    lines = [l.strip() for l in master.splitlines() if l.strip()]
    best, bestbw = None, -1
    for i, l in enumerate(lines):
        if l.startswith('#EXT-X-STREAM-INF'):
            bw = 0
            for part in l.split(','):
                if part.strip().startswith('BANDWIDTH='): bw = int(part.strip().split('=')[1])
            nxt = lines[i + 1] if i + 1 < len(lines) else None
            if nxt and not nxt.startswith('#') and bw > bestbw: bestbw, best = bw, nxt
    if best is None:
        pl, pl_base = master, stream_url
    else:
        v_abs = resolve_ref(stream_url, best)
        pl, _ = fetch_playlist(v_abs, ref)
        if not pl: return None
        pl_base = v_abs
    durs, segs = [], []
    pending = None
    for l in pl.splitlines():
        l = l.strip()
        if l.startswith('#EXTINF'): pending = float(l.split(':')[1].rstrip(','))
        elif l and not l.startswith('#'):
            durs.append(pending or 0.0); segs.append(l)
    if not segs: return None
    return (pl, pl_base, durs, segs)

def norm_lang(v):
    v = (v or '').strip().lower().replace('_', '-')
    base = v.split('-')[0]
    if base in ('en', 'eng'): return 'en'
    if base in ('id', 'ind', 'in'): return 'id'
    return None

def parse_subtitles(master, stream_url):
    """Trek subtitle dari master sumber: {lang: url_absolut} untuk en/id.
    Bahasa dari atribut LANGUAGE; nama trek (mis. 'Indonesian') jadi cadangan."""
    out = {}
    for l in master.splitlines():
        l = l.strip()
        if not l.startswith('#EXT-X-MEDIA:') or 'TYPE=SUBTITLES' not in l: continue
        attrs = {}
        for part in l.split(':', 1)[1].split(','):
            if '=' in part:
                k, v = part.split('=', 1)
                attrs[k.strip()] = v.strip().strip('"')
        uri = attrs.get('URI')
        if not uri: continue
        lang = norm_lang(attrs.get('LANGUAGE'))
        if not lang:
            nm = (attrs.get('NAME') or '').lower()
            if nm.startswith('english'): lang = 'en'
            elif nm.startswith('indones'): lang = 'id'
        if lang and lang not in out:
            out[lang] = resolve_ref(stream_url, uri)
    return out

def srt_to_vtt(text):
    lines = ['WEBVTT', '']
    for l in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        if '-->' in l: l = l.replace(',', '.')
        lines.append(l)
    return '\n'.join(lines)

def fetch_subtitle_vtt(uri, ref):
    """Ambil satu trek subtitle sumber -> teks VTT tunggal tervalidasi / None.
    URI bisa menunjuk berkas .vtt tunggal, daftar putar .vtt tersegmentasi,
    atau (jarang) .srt."""
    raw = fetch_raw_smart(uri, ref)
    if not raw: return None
    text = raw.decode('utf-8-sig', 'replace')
    if text.lstrip().startswith('#EXTM3U'):
        parts, pending = [], None
        for l in text.splitlines():
            l = l.strip()
            if l and not l.startswith('#'): parts.append(l)
        if not parts: return None
        bodies = []
        for p in parts:
            r2 = fetch_raw_smart(resolve_ref(uri, p), ref)
            if not r2: return None
            t2 = r2.decode('utf-8-sig', 'replace')
            if t2.lstrip().startswith('WEBVTT'):
                # buang blok header (WEBVTT + metadata s.d. baris kosong pertama)
                idx = t2.find('\n\n')
                t2 = t2[idx + 2:] if idx >= 0 else ''
            bodies.append(t2.strip())
        text = 'WEBVTT\n\n' + '\n\n'.join(b for b in bodies if b) + '\n'
    elif not text.lstrip().startswith('WEBVTT'):
        if '-->' in text and ',' in text.split('-->')[0][-14:]:
            text = srt_to_vtt(text)
        else:
            return None
    if text.count('-->') < 10: return None
    return text

def parse_tracks(tracks):
    """Field 'tracks' respons API sumber -> {lang: url} untuk en/id.
    Trek 'thumbnails' disaring (itu pratinjau gambar, bukan subtitle)."""
    out = {}
    for t in tracks or []:
        url = t.get('url') or ''
        if not url or 'thumbnail' in url.lower(): continue
        if (t.get('kind') or '').lower() == 'thumbnails': continue
        raw = (t.get('lang') or '').strip().lower()
        label = (t.get('label') or '').strip().lower()
        lang = None
        if raw.startswith('english') or norm_lang(raw): lang = norm_lang(raw) or 'en'
        if raw.startswith('indones') or raw.startswith('bahasa'): lang = 'id'
        if not lang:
            if label.startswith('english'): lang = 'en'
            elif label.startswith('indones'): lang = 'id'
        if lang and lang not in out: out[lang] = url
    return out

def fetch_segment(s_abs, ref):
    if s_abs.startswith(WORKER): return curl_bytes(s_abs, tries=2)
    data = curl_bytes(s_abs, tries=2, ref=ref)
    if is_ts(data): return data
    alt = curl_bytes(prox(s_abs, ref))
    return alt if is_ts(alt) else data


def fetch_raw_smart(u, ref, tries=2):
    """Ambil URL sumber langsung dulu (dengan Referer), proxy Worker cadangan.
    Kembalikan bytes mentah atau None; penelepon memvalidasi isinya."""
    raw = curl_bytes(u, tries=tries, ref=ref)
    if raw: return raw
    return curl_bytes(prox(u, ref), tries=tries)

def resolve_source(slug_id, num, skip=0):
    srv = anidap_json(f'https://chad.anidap.lol/rest/api/servers?id={urllib.parse.quote(slug_id)}&epNum={num}')
    if not srv: return None
    provs = sorted(srv.get('subProviders') or [], key=lambda p: 0 if p.get('default') else 1)
    cands = []
    for prov in provs:
        s = anidap_json(f'https://chad.anidap.lol/rest/api/sources?id={urllib.parse.quote(slug_id)}&epNum={num}&type=sub&providerId={urllib.parse.quote(prov["id"])}')
        if not s: continue
        ref = (s.get('headers') or {}).get('Referer') or 'https://megaplay.buzz/'
        trk = parse_tracks(s.get('tracks'))
        for x in (s.get('sources') or []):
            if x.get('url'): cands.append((x['url'], ref, trk))
    cands.sort(key=lambda c: 0 if 'nexabloom' in c[0] else 1)
    def probe(cand):
        u, ref = cand[0], cand[1]
        raw = fetch_raw_smart(u, ref, tries=2)
        if not raw or not raw.decode('utf-8', 'replace').lstrip().startswith('#EXTM3U'):
            return None
        master = raw.decode('utf-8', 'replace')
        parsed = parse_variant(master, u, ref)
        if not parsed: return None
        pl, pl_base, durs, segs = parsed
        if 0 < sum(durs) < 600: return None  # playlist pendek (cuplikan), bukan episode utuh
        # Uji segmen pertama & tengah: kandidat hanya diterima bila segmennya
        # benar-benar bisa diambil (playlist saja tidak cukup — kasus AoT).
        for idx in {0, len(segs) // 2}:
            s_abs = resolve_ref(pl_base, segs[idx])
            if not is_ts(fetch_segment(s_abs, ref)): return None
        subs = dict(cand[2])
        subs.update(parse_subtitles(master, u))
        return (pl, pl_base, durs, segs, ref, subs)
    # Probing paralel; ex.map menjaga urutan hasil = urutan kandidat (deterministik).
    with ThreadPoolExecutor(max_workers=6) as ex:
        probed = list(ex.map(probe, cands))
    cands_ok = [p for p in probed if p]
    if not cands_ok: return None
    chosen = cands_ok[skip % len(cands_ok)]
    sub_sources = [(p[5], p[4]) for p in cands_ok if p[5]]
    return chosen + (sub_sources,)

def fetch_playlist(url, ref):
    if url.startswith(WORKER):
        raw = curl_bytes(url)
        return (raw.decode('utf-8', 'replace') if raw else None), url
    raw = fetch_raw_smart(url, ref)
    return (raw.decode('utf-8', 'replace') if raw else None), url

def resolve_ref(base_url, line):
    if line.startswith('http'): return line
    if line.startswith('/'): return WORKER + line
    return urllib.parse.urljoin(base_url, line)

def is_ts(data): return bool(data) and len(data) > 188 and data[0] == 0x47

def download_episode(slug_id, num, outdir, start_skip=0):
    os.makedirs(outdir, exist_ok=True)
    prev_base = None
    results = None; durs = None; segs = None
    for cand_try in range(3):
        got = resolve_source(slug_id, num, skip=start_skip + cand_try)
        if not got:
            log(f'  ep{num}: resolusi kosong (coba {cand_try + 1}/3)'); time.sleep(10)
            continue
        pl, pl_base, durs, segs, referer, _cand_subs, sub_sources = got
        if prev_base is not None and pl_base != prev_base:
            # Ganti kandidat = segmentasi berbeda; buang sisa segmen kandidat lama.
            for fn in os.listdir(outdir):
                if fn.startswith('seg-'):
                    try: os.unlink(os.path.join(outdir, fn))
                    except OSError: pass
        prev_base = pl_base
        def grab(i):
            path = os.path.join(outdir, f'seg-{i:03d}.ts')
            if os.path.exists(path) and os.path.getsize(path) > 0:
                with open(path, 'rb') as f: head = f.read(1)
                if head == b'\x47': return True
            s_abs = resolve_ref(pl_base, segs[i])
            for rnd in range(4):
                data = fetch_segment(s_abs, referer)
                if is_ts(data):
                    with open(path, 'wb') as f: f.write(data)
                    return True
                time.sleep(2 + rnd * 3)
            return False
        with ThreadPoolExecutor(max_workers=16) as ex:
            results = list(ex.map(grab, range(len(segs))))
        if all(results): break
        log(f'  ep{num}: segmen gagal {results.count(False)} (kandidat {cand_try + 1}/3)')
        results = None
    if results is None: return False
    if sum(durs) < 600:
        log(f'  ep{num}: durasi hanya {sum(durs):.0f} dtk, ditolak'); return False
    # Potongan besar untuk runner: s.d. 45 dtk / 18 MB per berkas.
    groups, cur, cur_dur, cur_size = [], [], 0.0, 0
    for i in range(len(segs)):
        sz = os.path.getsize(os.path.join(outdir, f'seg-{i:03d}.ts'))
        if cur and (cur_dur + durs[i] > 45.0 or cur_size + sz > 18_000_000):
            groups.append(cur); cur, cur_dur, cur_size = [], 0.0, 0
        cur.append(i); cur_dur += durs[i]; cur_size += sz
    if cur: groups.append(cur)
    chunk_durs = []
    for gi, g in enumerate(groups):
        with open(os.path.join(outdir, f'chunk-{gi:03d}.ts'), 'wb') as out:
            for i in g:
                with open(os.path.join(outdir, f'seg-{i:03d}.ts'), 'rb') as f: out.write(f.read())
        chunk_durs.append(sum(durs[i] for i in g))
    for i in range(len(segs)):
        try: os.unlink(os.path.join(outdir, f'seg-{i:03d}.ts'))
        except OSError: pass
    target = int(max(chunk_durs)) + 1 if chunk_durs else 10
    with open(os.path.join(outdir, 'playlist.m3u8'), 'w') as f:
        f.write('#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-PLAYLIST-TYPE:VOD\n')
        f.write(f'#EXT-X-TARGETDURATION:{target}\n#EXT-X-MEDIA-SEQUENCE:0\n')
        for i, dd in enumerate(chunk_durs): f.write(f'#EXTINF:{dd:.3f},\nchunk-{i:03d}.ts\n')
        f.write('#EXT-X-ENDLIST\n')
    # v5.5: salin subtitle sumber (EN + ID bila ada) sebagai trek native.
    subs = {}
    for suris, sref in (sub_sources or []):
        for lang in ('en', 'id'):
            if lang in subs or lang not in suris: continue
            vtt = fetch_subtitle_vtt(suris[lang], sref)
            if vtt: subs[lang] = vtt
        if len(subs) >= 2: break
    if subs:
        dur = sum(chunk_durs)
        for lang, vtt in subs.items():
            with open(os.path.join(outdir, f'sub-{lang}.vtt'), 'w') as f: f.write(vtt)
            with open(os.path.join(outdir, f'subs-{lang}.m3u8'), 'w') as f:
                f.write('#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-PLAYLIST-TYPE:VOD\n')
                f.write(f'#EXT-X-TARGETDURATION:{int(dur) + 1}\n#EXT-X-MEDIA-SEQUENCE:0\n')
                f.write(f'#EXTINF:{dur:.3f},\nsub-{lang}.vtt\n#EXT-X-ENDLIST\n')
        # Master MULTIVARIAN SEJATI (revisi 5 Nov 03:0x): playlist.m3u8 menjadi
        # master yang menunjuk video.m3u8 — pola hybrid (MEDIA di dalam media
        # playlist) tidak terbaca sebagai trek oleh pemutar di HP user.
        media = ''
        if 'en' in subs:
            media += ('#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="English",LANGUAGE="en",'
                      'DEFAULT=YES,AUTOSELECT=YES,URI="subs-en.m3u8"\n')
        if 'id' in subs:
            media += ('#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="Indonesian",LANGUAGE="id",'
                      'DEFAULT=NO,AUTOSELECT=YES,URI="subs-id.m3u8"\n')
        p = os.path.join(outdir, 'playlist.m3u8')
        video_text = open(p).read()
        with open(os.path.join(outdir, 'video.m3u8'), 'w') as f: f.write(video_text)
        total_bytes = sum(os.path.getsize(os.path.join(outdir, f'chunk-{i:03d}.ts'))
                          for i in range(len(chunk_durs)))
        bw = max(500000, int(total_bytes * 8 / max(1.0, dur)))
        with open(p, 'w') as f:
            f.write('#EXTM3U\n#EXT-X-VERSION:3\n')
            f.write(media)
            f.write(f'#EXT-X-STREAM-INF:BANDWIDTH={bw},SUBTITLES="subs"\nvideo.m3u8\n')
        EP_SUBS[num] = sorted(subs)
        log(f'  ep{num}: subtitle tersalin: {"+".join(sorted(subs))}')
    else:
        log(f'  ep{num}: sumber tanpa subtitle yang bisa disalin')
    log(f'  ep{num}: {len(groups)} potongan besar, {sum(chunk_durs):.0f} dtk OK')
    return True

# ---------- unggah via git push + commit-tree (v5.1) ----------

def git(*args, check=True, timeout=600, input_text=None):
    r = subprocess.run(['git', '-C', REPO_DIR] + list(args), input=input_text,
                       capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        log('git gagal:', args[0], scrub((r.stderr or r.stdout or ''))[-300:])
    return r

def setup_repo():
    """Clone parsial repo gudang: hanya commit+pohon, tanpa blob, tanpa checkout, tanpa indeks."""
    if os.path.exists(REPO_DIR):
        shutil.rmtree(REPO_DIR)
    url = f'https://x-access-token:{TOKEN}@github.com/{REPO}.git'
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0')
    r = subprocess.run(['git', 'clone', '--filter=blob:none', '--depth', '1',
                        '--no-checkout', '--branch', 'main', url, REPO_DIR],
                       capture_output=True, text=True, timeout=1800, env=env)
    if r.returncode != 0:
        log('clone parsial gagal:', scrub(r.stderr or '')[-300:]); sys.exit(4)
    git('config', 'user.email', 'github-actions[bot]@users.noreply.github.com')
    git('config', 'user.name', 'github-actions[bot]')
    HEAD_NOW['sha'] = git('rev-parse', 'HEAD').stdout.strip()
    log('repo siap (clone parsial; unggah via commit-tree, tanpa indeks)')

def existing_eps():
    """Episode yang sudah terunggah di repo (untuk resume), dari pohon lokal."""
    r = git('ls-tree', '--name-only', HEAD_NOW['sha'] or 'HEAD', '--', WID + '/', check=False)
    out = set()
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            nm = line.strip().rsplit('/', 1)[-1]
            if nm.startswith('ep') and nm[2:].isdigit(): out.add(int(nm[2:]))
    return out

def ls_entries(treeish):
    """Entri satu pohon/commit: [{mode, type, sha, name}]."""
    if not treeish: return []
    r = git('ls-tree', '-z', treeish, check=False)
    out = []
    if r.returncode != 0: return out
    for rec in r.stdout.split('\0'):
        if not rec: continue
        meta, name = rec.split('\t', 1)
        mode, typ, sha = meta.split(' ')
        out.append({'mode': mode, 'type': typ, 'sha': sha, 'name': name})
    return out

def mktree(entries):
    def key(e): return e['name'] + ('/' if e['type'] == 'tree' else '')
    inp = ''.join(f"{e['mode']} {e['type']} {e['sha']}\t{e['name']}\n"
                  for e in sorted(entries, key=key))
    r = git('mktree', '--missing', input_text=inp)  # --missing wajib: clone blobless tak punya blob lama lokal; mktree tanpa itu menolak entri blob yang dirujuk pohon lama (terbukti di uji lokal v5.1)
    if r.returncode != 0:
        log('mktree gagal'); sys.exit(4)
    return r.stdout.strip()

def splice(treeish, parts, leaf):
    """Ganti/tambah entri di jalur parts dalam pohon treeish; kembalikan sha pohon baru."""
    entries = ls_entries(treeish)
    head, rest = parts[0], parts[1:]
    entries = [e for e in entries if e['name'] != head]
    if not rest:
        leaf = dict(leaf); leaf['name'] = head
        entries.append(leaf)
        return mktree(entries)
    sub_sha = next((e['sha'] for e in ls_entries(treeish)
                    if e['name'] == head and e['type'] == 'tree'), None)
    new_sub = splice(sub_sha, rest, leaf)
    entries.append({'mode': '040000', 'type': 'tree', 'sha': new_sub, 'name': head})
    return mktree(entries)

def push_commit(new_root_tree, msg):
    parent = HEAD_NOW['sha']
    r = git('commit-tree', new_root_tree, '-p', parent, '-m', msg)
    if r.returncode != 0:
        log('commit-tree gagal'); sys.exit(4)
    commit = r.stdout.strip()
    for t in range(5):
        rp = git('push', 'origin', f'{commit}:refs/heads/main', check=False, timeout=1800)
        if rp.returncode == 0:
            HEAD_NOW['sha'] = commit
            return commit
        log(f'  push gagal (coba {t + 1}/5):', scrub(rp.stderr or '')[-200:])
        time.sleep(20 * (t + 1))
    log('push gagal total'); sys.exit(4)

def push_episode(ep):
    """Satu episode = commit-tree + push, DIPECAH per potongan bila besar:
    maks ~250 MB per push dan playlist.m3u8 selalu di potongan terakhir,
    supaya episode film raksasa tidak ditolak GitHub (HTTP 500 push tunggal)
    dan episode setengah jalan tidak pernah memajang playlist yatim."""
    edir = os.path.join(OUT, f'ep{ep}')
    files = [(fn, os.path.getsize(os.path.join(edir, fn))) for fn in sorted(os.listdir(edir))]
    seg_files = [(fn, sz) for fn, sz in files if fn != 'playlist.m3u8']
    pl_files = [fn for fn, sz in files if fn == 'playlist.m3u8']
    # Entri episode yang SUDAH ada di repo (resume sesudah push terpotong).
    acc = {}
    r = git('ls-tree', '-z', f"{HEAD_NOW['sha']}:{WID}/ep{ep}", check=False)
    if r.returncode == 0:
        for rec in r.stdout.split('\0'):
            if not rec: continue
            meta, name = rec.split('\t', 1)
            mode, typ, sha = meta.split(' ')
            acc[name] = {'mode': mode, 'type': typ, 'sha': sha, 'name': name}
    BATCH_MAX = 250 * 1024 * 1024
    batches, cur, cur_sz = [], [], 0
    for fn, sz in seg_files:
        if fn in acc: continue
        if cur and cur_sz + sz > BATCH_MAX:
            batches.append(cur); cur, cur_sz = [], 0
        cur.append(fn); cur_sz += sz
    if cur or not batches: batches.append(cur)
    commit = HEAD_NOW['sha']
    for bi, batch in enumerate(batches):
        last = bi == len(batches) - 1
        todo = list(batch) + (pl_files if last else [])
        if not todo and acc: continue
        for fn in todo:
            r = git('hash-object', '-w', os.path.join(edir, fn), timeout=900)
            if r.returncode != 0:
                log('hash-object gagal', fn); sys.exit(4)
            acc[fn] = {'mode': '100644', 'type': 'blob', 'sha': r.stdout.strip(), 'name': fn}
        ep_tree = mktree(list(acc.values()))
        root = splice(HEAD_NOW['sha'], [WID, f'ep{ep}'],
                      {'mode': '040000', 'type': 'tree', 'sha': ep_tree})
        commit = push_commit(root, f'{WID} episode {ep} (segmen HLS)')
        log(f'  ep{ep} ter-push potongan {bi + 1}/{len(batches)} ({commit[:7]})')
    shutil.rmtree(edir, ignore_errors=True)  # hemat disk runner
    return commit

def push_manifest(pin):
    manifest = {'wid': WID, 'anilistId': ANILIST, 'name': NAME, 'totalEpisodes': TOTAL,
                'repo': REPO, 'pin': pin,
                'subs': {str(k): v for k, v in sorted(EP_SUBS.items())}}
    data = json.dumps(manifest, indent=1).encode()
    r = subprocess.run(['git', '-C', REPO_DIR, 'hash-object', '-w', '--stdin'],
                       input=data, capture_output=True, timeout=120)
    if r.returncode != 0:
        log('hash-object manifest gagal'); sys.exit(4)
    blob = r.stdout.decode().strip()
    root = splice(HEAD_NOW['sha'], ['manifests', f'{WID}.json'],
                  {'mode': '100644', 'type': 'blob', 'sha': blob})
    push_commit(root, f'manifest {WID}')
    print('MANIFEST_PIN', pin, flush=True)

def main():
    setup_repo()
    have = existing_eps()
    if have: log(f'resume: {len(have)} episode sudah ada di repo, dilanjutkan')
    pin = HEAD_NOW['sha']
    for ep in range(1, TOTAL + 1):
        if ep in have: continue
        edir = os.path.join(OUT, f'ep{ep}')
        ok = False
        for attempt in range(6):
            if download_episode(SLUG_ID, ep, edir, start_skip=attempt): ok = True; break
            log(f'  ep{ep}: unduh belum utuh (percobaan {attempt + 1}/6)'); time.sleep(10)
        if not ok:
            log(f'EP{ep} GAGAL — berhenti (episode yang sudah selesai aman tersimpan di repo)'); sys.exit(3)
        pin = push_episode(ep)
    push_manifest(pin)
    log(f'SELESAI {WID}: pin {pin[:7]} — menunggu diterbitkan pengawas katalog')

if __name__ == '__main__':
    main()
