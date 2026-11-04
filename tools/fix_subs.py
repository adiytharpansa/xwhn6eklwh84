# Perbaikan subtitle untuk judul yang SUDAH tersalin robot lama (v5.4 ke bawah):
# tanpa menyalin ulang video, tambahkan trek subtitle native (EN + ID bila
# sumber menyediakan) ke tiap episode yang playlist-nya masih polos:
# sub-<lang>.vtt + subs-<lang>.m3u8 + video.m3u8 (isi playlist lama) +
# playlist.m3u8 menjadi master multivarian sejati (format robot v5.5 yang
# terverifikasi tampil di aplikasi). Berkas didorong memakai mesin git
# commit-tree robot (push_episode) dan manifest diperbarui (push_manifest)
# dengan pin baru + peta subs. Episode yang sudah punya trek dilewati;
# episode yang sumbernya kosong/tanpa subtitle dilewati tanpa mengubah apa
# pun. Masukan lewat env sama seperti robot: WID, SLUG_ID, ANILIST_ID,
# TOTAL_EPS, TITLE_NAME, GH_TOKEN, GH_REPO. Butuh tools_dl/copy_title.py
# (robot v5.5) di samping berkas ini — diunduh workflow dari repo yang sama.
import importlib.util, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('robot', os.path.join(HERE, 'copy_title.py'))
R = importlib.util.module_from_spec(spec); spec.loader.exec_module(R)
log = R.log

def git_show(path):
    r = subprocess.run(['git', '-C', R.REPO_DIR, 'show', f"{R.HEAD_NOW['sha']}:{path}"],
                       capture_output=True, text=True, timeout=300)
    return r.stdout if r.returncode == 0 else None

def ep_chunk_bytes(ep):
    r = R.git('ls-tree', '-l', '-z', f"{R.HEAD_NOW['sha']}:{R.WID}/ep{ep}", check=False)
    total = 0
    if r.returncode == 0:
        for rec in r.stdout.split('\0'):
            if not rec or '\t' not in rec: continue
            meta, name = rec.split('\t', 1)
            parts = meta.split()
            if len(parts) >= 4 and name.startswith('chunk-'):
                try: total += int(parts[3])
                except ValueError: pass
    return total

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

def fix_episode(ep):
    path = f'{R.WID}/ep{ep}/playlist.m3u8'
    text = git_show(path)
    if text is None:
        log(f'  ep{ep}: playlist tidak ada di repo, dilewati'); return 'missing'
    if '#EXT-X-MEDIA' in text:
        log(f'  ep{ep}: sudah punya trek subtitle, dilewati'); return 'already'
    subs = collect_subs(ep)
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
    with open(os.path.join(edir, 'video.m3u8'), 'w') as f: f.write(text)
    media = ''
    if 'en' in subs:
        media += ('#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="English",LANGUAGE="en",'
                  'DEFAULT=YES,AUTOSELECT=YES,URI="subs-en.m3u8"\n')
    if 'id' in subs:
        media += ('#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="Indonesian",LANGUAGE="id",'
                  'DEFAULT=NO,AUTOSELECT=YES,URI="subs-id.m3u8"\n')
    total_bytes = ep_chunk_bytes(ep)
    bw = max(500000, int(total_bytes * 8 / max(1.0, dur))) if total_bytes else 3200000
    with open(os.path.join(edir, 'playlist.m3u8'), 'w') as f:
        f.write('#EXTM3U\n#EXT-X-VERSION:3\n')
        f.write(media)
        f.write(f'#EXT-X-STREAM-INF:BANDWIDTH={bw},SUBTITLES="subs"\nvideo.m3u8\n')
    R.EP_SUBS[ep] = sorted(subs)
    R.push_episode(ep)
    log(f'  ep{ep}: subtitle ditambahkan: {"+".join(sorted(subs))}')
    return 'fixed'

def main():
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
    print(f"FIXSUBS_SUMMARY fixed={counts['fixed']} already={counts['already']} "
          f"nosubs={counts['nosubs']} missing={counts['missing']} pin={R.HEAD_NOW['sha']}", flush=True)

if __name__ == '__main__':
    main()
