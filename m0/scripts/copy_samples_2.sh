#!/bin/bash
# Copy the second batch of M0 sample files from the NAS (read-only source)
# into m0/nas_sample/. Only reads from /Volumes/music.
set -u
R=/Volumes/music
D="$(cd "$(dirname "$0")/.." && pwd)/nas_sample"

copy_dir() {  # copy_dir <nas-relative dir> <dest name>
  if [ -d "$R/$1" ]; then mkdir -p "$D/$2" && cp -Rp "$R/$1/." "$D/$2/" && echo "ok   $2"
  else echo "MISSING $1"; fi
}
copy_files() {  # copy_files <dest name> <nas-relative dir> <file>...
  local dest="$1" dir="$2"; shift 2
  mkdir -p "$D/$dest"
  for f in "$@"; do
    if [ -f "$R/$dir/$f" ]; then cp -p "$R/$dir/$f" "$D/$dest/" && echo "ok   $dest/$f"
    else echo "MISSING $dir/$f"; fi
  done
}

copy_dir "音乐mp3/林子祥.-.[Lam.2004.XRCD.Special].专辑.MP3" album_garbled_gbk_traditional_xrcd
copy_dir "music_cd_new/劉德華" album_traditional_hk
copy_dir "音乐mp3/蔡琴-老歌(XRCD2) mp3" album_remaster_xrcd2
copy_dir "music_cd_new/2. Various Artists/2005 - 成人兒歌 (HK - 華星唱片 CD-02-1347 - 2005-07-18)" album_various_artists_hk

copy_files singles_live 毛阿敏-单曲 "妈妈的吻 (Live).flac" "常回家看看 (Live).flac"
copy_files singles_live 张也-单曲 "幸福之歌 (Live).mp3" "红墙连万家 (Live).flac"

copy_files singles_with_lrc QQ下载音乐.flac \
  "Lulle Lullay And Lullabye.lrc" "Lulle Lullay And Lullabye.mp3" \
  "Man In The Moon.lrc" "Man In The Moon.flac" \
  "彩虹之上.lrc" "彩虹之上.flac"

# duplicates (N) next to their originals, a blank-album track, a few normal tracks
cd "$R/张也-单曲" || exit 1
sel=()
for f in "万事如意(2).flac" "三杯美酒敬亲人(1).mp3" "枉凝眉(1).flac" "和谐颂(1).mp3"; do
  base="${f%(*}"
  sel+=("$f")
  for o in "$base".mp3 "$base".flac "$base"\(1\).* ; do [ -f "$o" ] && [ "$o" != "$f" ] && sel+=("$o"); done
done
sel+=("万事根本.flac" "一字对花.mp3" "一梦约千年.mp3" "【民间小调】《沂蒙山小调》.mp3" "一朵鲜花鲜又鲜.flac")
copy_files singles_dupes_blank_album 张也-单曲 "${sel[@]}"
du -sh "$D"/* | sort -k2
