# Tambal sub ID untuk episode yang SUDAH punya master sejati ber-EN saja
# (hasil robot v5.5 / fix_subs gelombang awal): sub ID dibuat dari sub EN
# yang sudah ada di repo dengan terjemahan luring Argos en->id (stempel
# waktu persis), lalu master ditulis ulang menambah trek Indonesian dan
# manifest diperbarui. Tidak menyentuh video/segmen sama sekali.
# Masukan env sama seperti robot: WID, SLUG_ID, ANILIST_ID, TOTAL_EPS,
# TITLE_NAME, GH_TOKEN, GH_REPO. Butuh tools_dl/copy_title.py di samping
# berkas ini (diunduh workflow dari repo yang sama) + model Argos di
# ./argos-model (diunduh workflow) dan paket ctranslate2 + subword-nmt.
import importlib.util, os, subprocess, sys

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

ID_LINE = ('#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="Indonesian",LANGUAGE="id",'
           'DEFAULT=NO,AUTOSELECT=YES,URI="subs-id.m3u8"\n')

def git_show(path):
    r = subprocess.run(['git', '-C', R.REPO_DIR, 'show', f"{R.HEAD_NOW['sha']}:{path}"],
                       capture_output=True, text=True, timeout=300)
    return r.stdout if r.returncode == 0 else None

def playlist_duration(text):
    tot = 0.0
    for l in text.splitlines():
        l = l.strip()
        if l.startswith('#EXTINF:'):
            try: tot += float(l.split(':')[1].rstrip(','))
            except ValueError: pass
    return tot

def master_langs(text):
    langs = []
    for l in text.splitlines():
        if l.startswith('#EXT-X-MEDIA:') and 'TYPE=SUBTITLES' in l:
            if 'LANGUAGE="en"' in l: langs.append('en')
            if 'LANGUAGE="id"' in l: langs.append('id')
    return langs

def fix_episode(ep):
    path = f'{R.WID}/ep{ep}/playlist.m3u8'
    text = git_show(path)
    if text is None:
        log(f'  ep{ep}: playlist tidak ada di repo, dilewati'); return 'missing'
    langs = master_langs(text)
    if not langs:
        log(f'  ep{ep}: belum master subtitle (jalankan fix_subs dulu)'); return 'plain'
    R.EP_SUBS[ep] = sorted(set(langs))
    if 'id' in langs:
        log(f'  ep{ep}: sudah EN+ID'); return 'already'
    if 'en' not in langs:
        log(f'  ep{ep}: hanya ID, EN tidak bisa dibuat di sini'); return 'nosubs'
    en_vtt = git_show(f'{R.WID}/ep{ep}/sub-en.vtt')
    if not en_vtt:
        log(f'  ep{ep}: sub-en.vtt tidak ditemukan di repo'); return 'nosubs'
    id_vtt = translate_vtt_en_to_id(en_vtt)
    if not id_vtt:
        log(f'  ep{ep}: terjemahan EN->ID gagal'); return 'nosubs'
    video = git_show(f'{R.WID}/ep{ep}/video.m3u8') or ''
    dur = playlist_duration(video) or 600.0
    edir = os.path.join(R.OUT, f'ep{ep}')
    os.makedirs(edir, exist_ok=True)
    with open(os.path.join(edir, 'sub-id.vtt'), 'w') as f: f.write(id_vtt)
    with open(os.path.join(edir, 'subs-id.m3u8'), 'w') as f:
        f.write('#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-PLAYLIST-TYPE:VOD\n')
        f.write(f'#EXT-X-TARGETDURATION:{int(dur) + 1}\n#EXT-X-MEDIA-SEQUENCE:0\n')
        f.write(f'#EXTINF:{dur:.3f},\nsub-id.vtt\n#EXT-X-ENDLIST\n')
    lines = text.splitlines(True)
    out, done = [], False
    for l in lines:
        if not done and l.startswith('#EXT-X-STREAM-INF'):
            out.append(ID_LINE); done = True
        out.append(l)
    if not done: out.append(ID_LINE)
    with open(os.path.join(edir, 'playlist.m3u8'), 'w') as f: f.write(''.join(out))
    R.EP_SUBS[ep] = ['en', 'id']
    R.push_episode(ep)
    log(f'  ep{ep}: sub ID ditambahkan dari EN (terjemahan Argos)')
    return 'fixed'

def main():
    R.setup_repo()
    counts = {'fixed': 0, 'already': 0, 'nosubs': 0, 'missing': 0, 'plain': 0}
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
    print(f"FIXSUBSID_SUMMARY fixed={counts['fixed']} already={counts['already']} "
          f"nosubs={counts['nosubs']} missing={counts['missing']} plain={counts['plain']} "
          f"pin={R.HEAD_NOW['sha']}", flush=True)

if __name__ == '__main__':
    main()
