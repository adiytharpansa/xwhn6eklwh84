# Perbaikan subtitle OVERLAY untuk judul di gudang PENSIUN (repo menolak semua
# tulisan kuota): berkas subtitle + playlist master ditaruh di repo AKTIF yang
# menjalankan workflow ini, pada folder <wid>-subsfix/ep<N>/, dan master-nya
# menunjuk varian video secara ABSOLUT ke playlist lama di repo sumber
# (SRC_REPO @ SRC_PIN). Video tidak dipindah sama sekali. Aplikasi membaca
# alamat episode absolut apa adanya (Api.kt absolut()), jadi katalog cukup
# mengarahkan playlist episode ke URL overlay ini. Tata letak berkas per
# episode identik robot v5.5: sub-<lang>.vtt + subs-<lang>.m3u8 +
# playlist.m3u8 (master multivarian sejati). Manifest ditulis sebagai
# manifests/<wid>-subsfix.json (pin overlay + peta subs) oleh push_manifest
# robot. Masukan env: WID, SLUG_ID, ANILIST_ID, TOTAL_EPS, TITLE_NAME,
# GH_TOKEN, GH_REPO (repo AKTIF penampung) + SRC_REPO, SRC_PIN (repo pensiun
# + pin katalog judulnya). Butuh tools_dl/copy_title.py (robot v5.5).
import importlib.util, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('robot', os.path.join(HERE, 'copy_title.py'))
R = importlib.util.module_from_spec(spec); spec.loader.exec_module(R)
log = R.log

# --- Terjemahan cadangan EN->ID (Argos en_id 1.9 via ctranslate2 + BPE) ---
# Dipakai bila sumber hanya menyediakan trek EN: sub ID dibuat dari EN dengan
# stempel waktu persis (pola ongoing_lane yang disetujui user). Model diunduh
# workflow ke ./argos-model (ARGOS_MODEL_DIR). Gagal total -> None (pemanggil
# memutuskan; tidak pernah mengarang teks).
_TR_STATE = {'v': None}
def _load_tr():
    if _TR_STATE['v'] is not None: return _TR_STATE['v'] or None
    _TR_STATE['v'] = False
    try:
        import ctranslate2, glob
        from subword_nmt.apply_bpe import BPE
        roots = [os.environ.get('ARGOS_MODEL_DIR') or '',
                 os.path.join(os.getcwd(), 'argos-model'),
                 os.path.join(os.getcwd(), 'argos-models'),
                 os.path.expanduser('~/workspace/projectx-app/argos-models')]
        base = None
        for root in roots:
            if not root: continue
            for cand in glob.glob(os.path.join(root, '**', 'bpe.model'), recursive=True):
                d = os.path.dirname(cand)
                if os.path.isdir(os.path.join(d, 'model')): base = d; break
            if base: break
        if base:
            bpe = BPE(open(os.path.join(base, 'bpe.model')))
            tr = ctranslate2.Translator(os.path.join(base, 'model'), device='cpu')
            _TR_STATE['v'] = (bpe, tr)
            log(f'terjemahan EN->ID siap (model {base})')
    except Exception as e:
        log(f'terjemahan EN->ID tidak tersedia: {type(e).__name__}: {str(e)[:120]}')
    return _TR_STATE['v'] or None

def translate_vtt_en_to_id(en_text):
    lt = _load_tr()
    if not lt: return None
    bpe, tr = lt
    blocks = en_text.replace('\r\n', '\n').split('\n\n')
    parsed, texts = [], []
    for b in blocks:
        lines = b.split('\n')
        ti = next((i for i, l in enumerate(lines) if '-->' in l), None)
        if ti is None:
            parsed.append((b, None)); continue
        joined = ' '.join(l.strip() for l in lines[ti + 1:] if l.strip())
        parsed.append(('\n'.join(lines[:ti + 1]), joined))
        if joined: texts.append(joined)
    if not texts: return None
    uniq = list(dict.fromkeys(texts))
    seg = [bpe.process_line(t).split() for t in uniq]
    try:
        results = tr.translate_batch(seg, beam_size=4)
    except Exception:
        return None
    def dectok(toks):
        return ''.join(t[:-2] if t.endswith('@@') else t + ' ' for t in toks).strip()
    tmap = {t: dectok(r.hypotheses[0]) for t, r in zip(uniq, results)}
    out = []
    for head, joined in parsed:
        if joined is None: out.append(head)
        elif joined: out.append(head + '\n' + tmap.get(joined, joined))
        else: out.append(head)
    res = '\n\n'.join(out)
    return res if res.lstrip().startswith('WEBVTT') else None

ORIG_WID = R.WID
SRC_REPO = os.environ['SRC_REPO']
SRC_PIN = os.environ['SRC_PIN']

def src_playlist(ep):
    """Teks playlist lama episode ini dari repo sumber (read-only, multi-pintu)."""
    path = f'{ORIG_WID}/ep{ep}/playlist.m3u8'
    urls = [f'https://cdn.jsdelivr.net/gh/{SRC_REPO}@{SRC_PIN}/{path}',
            f'https://gcore.jsdelivr.net/gh/{SRC_REPO}@{SRC_PIN}/{path}',
            f'https://quantil.jsdelivr.net/gh/{SRC_REPO}@{SRC_PIN}/{path}',
            f'https://raw.githubusercontent.com/{SRC_REPO}/{SRC_PIN}/{path}']
    for u in urls:
        raw = R.curl_bytes(u, tries=2)
        if raw:
            text = raw.decode('utf-8', 'replace')
            if text.lstrip().startswith('#EXTM3U'): return text
    return None

def playlist_duration(text):
    tot = 0.0
    for l in text.splitlines():
        l = l.strip()
        if l.startswith('#EXTINF:'):
            try: tot += float(l.split(':')[1].rstrip(','))
            except ValueError: pass
    return tot

def collect_subs(ep):
    """{lang: teks VTT} dari kandidat sumber episode ini ({} bila tak ada)."""
    got = R.resolve_source(R.SLUG_ID, ep)
    if not got: return {}
    _pl, _base, _durs, _segs, ref0, subs0, sub_sources = got
    ordered = [(subs0, ref0)] + list(sub_sources or [])
    subs = {}
    for suris, sref in ordered:
        for lang in ('en', 'id'):
            if lang in subs or lang not in (suris or {}): continue
            vtt = R.fetch_subtitle_vtt(suris[lang], sref)
            if vtt: subs[lang] = vtt
        if len(subs) >= 2: break
    return subs

def estimate_bw(text, ep, dur):
    chunks = [l.strip() for l in text.splitlines() if l.strip() and not l.startswith('#')]
    if not chunks: return 3200000
    data = R.curl_bytes(f'https://cdn.jsdelivr.net/gh/{SRC_REPO}@{SRC_PIN}/{ORIG_WID}/ep{ep}/{chunks[0]}', tries=2)
    if not data: return 3200000
    return max(500000, int(len(data) * len(chunks) * 8 / max(1.0, dur)))

def fix_episode(ep):
    text = src_playlist(ep)
    if text is None:
        log(f'  ep{ep}: playlist sumber tidak terbaca, dilewati'); return 'missing'
    if '#EXT-X-MEDIA:' in text:
        log(f'  ep{ep}: playlist sumber sudah master, dilewati'); return 'already'
    subs = collect_subs(ep)
    if 'en' in subs and 'id' not in subs:
        idt = translate_vtt_en_to_id(subs['en'])
        if idt: subs['id'] = idt; log(f'  ep{ep}: sub ID dibuat dari EN (terjemahan Argos)')
    if not subs:
        log(f'  ep{ep}: sumber tanpa subtitle yang bisa disalin'); return 'nosubs'
    dur = playlist_duration(text)
    if dur <= 0: dur = 600.0
    edir = os.path.join(R.OUT, f'ep{ep}')
    os.makedirs(edir, exist_ok=True)
    for lang, vtt in subs.items():
        with open(os.path.join(edir, f'sub-{lang}.vtt'), 'w') as f: f.write(vtt)
        with open(os.path.join(edir, f'subs-{lang}.m3u8'), 'w') as f:
            f.write('#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-PLAYLIST-TYPE:VOD\n')
            f.write(f'#EXT-X-TARGETDURATION:{int(dur) + 1}\n#EXT-X-MEDIA-SEQUENCE:0\n')
            f.write(f'#EXTINF:{dur:.3f},\nsub-{lang}.vtt\n#EXT-X-ENDLIST\n')
    media = ''
    if 'en' in subs:
        media += ('#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="English",LANGUAGE="en",'
                  'DEFAULT=YES,AUTOSELECT=YES,URI="subs-en.m3u8"\n')
    if 'id' in subs:
        media += ('#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="Indonesian",LANGUAGE="id",'
                  'DEFAULT=NO,AUTOSELECT=YES,URI="subs-id.m3u8"\n')
    bw = estimate_bw(text, ep, dur)
    variant_abs = f'https://cdn.jsdelivr.net/gh/{SRC_REPO}@{SRC_PIN}/{ORIG_WID}/ep{ep}/playlist.m3u8'
    with open(os.path.join(edir, 'playlist.m3u8'), 'w') as f:
        f.write('#EXTM3U\n#EXT-X-VERSION:3\n')
        f.write(media)
        f.write(f'#EXT-X-STREAM-INF:BANDWIDTH={bw},SUBTITLES="subs"\n{variant_abs}\n')
    R.EP_SUBS[ep] = sorted(subs)
    R.push_episode(ep)
    log(f'  ep{ep}: overlay subtitle ditambahkan: {"+".join(sorted(subs))}')
    return 'fixed'

def main():
    # Identitas dorong diganti ke folder overlay SEBELUM setup_repo/push:
    # push_episode menyambung pohon di [WID, ep<N>] pada repo penampung ini.
    R.WID = ORIG_WID + '-subsfix'
    R.OUT = os.path.join(os.getcwd(), 'work', R.WID)
    R.setup_repo()
    counts = {'fixed': 0, 'already': 0, 'nosubs': 0, 'missing': 0}
    for ep in range(1, R.TOTAL + 1):
        try:
            counts[fix_episode(ep)] += 1
        except SystemExit:
            raise
        except Exception as e:
            log(f'  ep{ep}: galat {type(e).__name__}: {str(e)[:160]} — dilewati')
            counts['nosubs'] += 1
    if counts['fixed']:
        R.push_manifest(R.HEAD_NOW['sha'])
    print(f"FIXSUBS_OVERLAY_SUMMARY fixed={counts['fixed']} already={counts['already']} "
          f"nosubs={counts['nosubs']} missing={counts['missing']} pin={R.HEAD_NOW['sha']}", flush=True)

if __name__ == '__main__':
    main()
