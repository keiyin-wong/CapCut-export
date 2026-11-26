#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
capcut-export : independent renderer for a CapCut / 剪映 draft.

It parses a draft_content.json and re-renders the edit from YOUR OWN local
source media using FFmpeg. It does NOT touch, patch, or bypass CapCut itself.

Scope of this v1 (covers the "末世 EP01" draft):
  - video tracks: trim each segment by its source_timerange, composite them
                  at their draft times and layer positions on the canvas.
  - text track  : white centered overlays via drawtext, positioned from the
                  segment transform, shown for their timerange.
  - audio       : the video segments' own audio + available draft audio
                  mixed at its timeline offset.

Usage:
  python render.py "<draft folder>" -o out.mp4
  python render.py "<draft folder>" -o out.mp4 --music mytrack.mp3:19.47
"""
import argparse, atexit, json, math, os, re, subprocess, sys, tempfile, shutil, threading

US = 1_000_000  # CapCut times are microseconds

for stream in (sys.stdout, sys.stderr):
    # Chinese draft and file names must survive a pipe to the GUI.
    if stream and hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

APP_DIR = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_tool(name):
    """FFmpeg next to the program first, so a copied folder works as is."""
    exe = name + (".exe" if os.name == "nt" else "")
    folders = [os.environ.get("CAPCUT_EXPORT_FFMPEG", ""),
               os.path.join(APP_DIR, "ffmpeg", "bin"), os.path.join(APP_DIR, "ffmpeg"), APP_DIR]
    for folder in folders:
        if folder and os.path.isfile(os.path.join(folder, exe)):
            return os.path.join(folder, exe)
    found = shutil.which(name)
    if found:
        return found
    sys.exit(f"[fatal] 找不到 {name}。请把 {exe} 放到程序目录的 ffmpeg 文件夹里，"
             "或安装 FFmpeg 并加入 PATH。")


FFMPEG = find_tool("ffmpeg")
FFPROBE = find_tool("ffprobe")


def find_font():
    fonts = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    # Single-face CJK fonts first: drawtext reads the first face of a .ttc.
    for name in ("simhei.ttf", "msyh.ttc", "Deng.ttf", "simsun.ttc", "arial.ttf"):
        if os.path.isfile(os.path.join(fonts, name)):
            return os.path.join(fonts, name)
    return None


FONT = find_font()
FONT_FILTER = FONT.replace("\\", "/").replace(":", "\\:") if FONT else None

_working_encoders = {}
ENC = None  # chosen in main(); probing costs a few seconds


def encoder_works(encoder):
    """A listed hardware encoder can still fail without its GPU or driver."""
    if encoder not in _working_encoders:
        probe = [FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=256x144:r=30:d=0.2",
                 *venc_args(23, encoder), "-pix_fmt", "yuv420p", "-f", "null", "-"]
        try:
            ok = subprocess.run(probe, capture_output=True, timeout=30,
                                creationflags=NO_WINDOW).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        _working_encoders[encoder] = ok
    return _working_encoders[encoder]


def pick_encoder(codec="h264"):
    """The first H.264 / HEVC encoder that actually encodes on this machine."""
    out = subprocess.run([FFMPEG, "-hide_banner", "-encoders"], capture_output=True,
                         text=True, creationflags=NO_WINDOW).stdout
    software = {"h264": "libx264", "hevc": "libx265"}[codec]
    # nvenc last: a newer NVIDIA driver can reject an older FFmpeg's API.
    for encoder in (software, *(f"{codec}_{suffix}" for suffix in ("qsv", "mf", "amf", "nvenc"))):
        if re.search(rf"\s{encoder}\s", out) and encoder_works(encoder):
            return encoder
    return None


def venc_args(crf, encoder=None):
    """Rate-control flags per encoder."""
    encoder = encoder or ENC
    if encoder in ("libx264", "libx265"):
        return ["-c:v", encoder, "-preset", "medium", "-crf", str(crf)]
    if encoder.endswith("_qsv"):
        return ["-c:v", encoder, "-global_quality", str(crf)]
    if encoder.endswith("_mf"):
        return ["-c:v", encoder, "-rate_control", "quality", "-quality", str(crf)]
    if encoder.endswith("_amf"):
        return ["-c:v", encoder, "-rc", "cqp", "-qp_i", str(crf), "-qp_p", str(crf)]
    if encoder.endswith("_nvenc"):
        return ["-c:v", encoder, "-rc", "vbr", "-cq", str(crf), "-b:v", "0"]
    return ["-c:v", encoder, "-q:v", "3"]


def run(cmd, progress=None, **kw):
    """Run FFmpeg; show its last error lines on failure.

    With progress=(base, span, seconds), report out_time as overall progress
    between base and base + span for the GUI's progress bar.
    """
    print("  $", " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd[:6]), "...", flush=True)
    kw.pop("stdout", None)
    kw.pop("stderr", None)
    if progress:
        cmd = [cmd[0], "-nostats", "-progress", "pipe:1", *cmd[1:]]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE if progress else subprocess.DEVNULL,
                            stderr=subprocess.PIPE, creationflags=NO_WINDOW, **kw)
    errors = []
    reader = threading.Thread(target=lambda: errors.extend(proc.stderr.read().decode(
        "utf-8", "replace").splitlines()[-15:]), daemon=True)
    reader.start()
    if progress:
        base, span, seconds = progress
        last = -1
        for raw in proc.stdout:
            line = raw.decode("ascii", "replace").strip()
            if line.startswith("out_time_us=") and line[12:].isdigit() and seconds > 0:
                done = min(1.0, int(line[12:]) / US / seconds)
                percent = int((base + span * done) * 100)
                if percent != last:
                    print(f"[progress] {percent}", flush=True)
                    last = percent
    code = proc.wait()
    reader.join()
    if code != 0:
        print("\n".join(errors), flush=True)
        sys.exit(f"[fatal] FFmpeg 执行失败（退出码 {code}）")


def report(fraction):
    print(f"[progress] {int(fraction * 100)}", flush=True)


def resolve_media(path, folder):
    """Find a draft's media when the draft was copied from another computer.

    CapCut stores absolute paths. When one is missing, map the part after the
    draft's own folder name onto this folder, then look for the file name in
    the draft folder.
    """
    if not path:
        return path
    path = path.replace("/", os.sep)
    if os.path.exists(path):
        return path
    folder = os.path.abspath(folder)
    parts = re.split(r"[\\/]+", path)
    name = os.path.basename(folder)
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] == name or parts[i].startswith("##_draftpath_placeholder"):
            candidate = os.path.join(folder, *parts[i + 1:])
            if os.path.exists(candidate):
                return candidate
    # Stickers, effects and library music live in the editor's own cache.
    local = os.environ.get("LOCALAPPDATA", "")
    for i in range(len(parts) - 2):
        if parts[i].casefold() in ("capcut", "jianyingpro") and parts[i + 1].casefold() == "user data":
            candidate = os.path.join(local, parts[i], *parts[i + 1:])
            if local and os.path.exists(candidate):
                return candidate
    if not os.path.isabs(path) and os.path.exists(os.path.join(folder, path)):
        return os.path.join(folder, path)
    if folder not in _file_index:
        index = _file_index[folder] = {}
        for root, _dirs, files in os.walk(folder):
            for file in files:
                index.setdefault(file.casefold(), os.path.join(root, file))
    return _file_index[folder].get(parts[-1].casefold(), path)


_file_index = {}


def load_draft(folder):
    p = os.path.join(folder, "draft_content.json")
    if not os.path.isfile(p):
        sys.exit(f"[fatal] 这个文件夹里没有 draft_content.json：{folder}")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def audio_tempo(speed, reverse):
    filters = ["areverse"] if reverse else []
    while speed > 2:
        filters.append("atempo=2")
        speed /= 2
    while speed < 0.5:
        filters.append("atempo=0.5")
        speed *= 2
    if speed != 1:
        filters.append(f"atempo={speed:.12g}")
    return filters


def clip_keyframes(segment, source_start=0, speed=1):
    channels = {}
    for channel in segment.get("common_keyframes") or []:
        prop = channel.get("property_type")
        if not prop:
            continue
        # Native movie scale and media gain use the source clock.
        source_clock = prop in ("KFTypeScaleX", "KFTypeScaleY", "KFTypeVolume")
        points = {}
        for point in channel.get("keyframe_list") or []:
            values = point.get("values") or []
            offset = point.get("time_offset")
            if (not values or not isinstance(values[0], (int, float)) or
                    not isinstance(offset, (int, float)) or
                    not math.isfinite(values[0]) or not math.isfinite(offset)):
                continue
            seconds = offset / US
            if source_clock:
                seconds = (seconds - source_start) / speed
            curve = str(point.get("curveType") or "Line").casefold()
            points[seconds] = (values[0], curve)
        if points:
            channels[prop] = sorted(points.items())
    return channels


def keyframe_expr(channels, prop, default, time):
    points = channels.get(prop)
    if not points:
        return f"{default:.12g}"
    expression = f"{points[-1][1][0]:.12g}"
    for (start, (value, curve)), (end, (next_value, _)) in reversed(list(zip(points, points[1:]))):
        # ACT: Interpolate non-linear draft curves linearly until their graph
        # and handle units are available; keep held values constant.
        piece = f"{value:.12g}"
        if curve not in ("hold", "constant") and value != next_value:
            piece += f"+({next_value - value:.12g})*(({time})-({start:.12g}))/({end - start:.12g})"
        expression = f"if(lt({time},{end:.12g}),{piece},{expression})"
    first, (initial, _) = points[0]
    return f"if(lt({time},{first:.12g}),{initial:.12g},{expression})" if len(points) > 1 else expression


def fade_filters(fade, duration):
    result = []
    for field, direction in (("fade_in_duration", "in"), ("fade_out_duration", "out")):
        seconds = max(0, min(duration, float(fade.get(field) or 0) / US))
        if seconds:
            start = 0 if direction == "in" else duration - seconds
            result.append(f"afade=t={direction}:st={start:.6f}:d={seconds:.6f}")
    return result


def local_lut(material, folder, tmp, cache):
    path = resolve_media(material.get("path") or "", folder)
    if path and not os.path.isabs(path):
        path = os.path.join(folder, path)
    if os.path.isdir(path):
        config_path = os.path.join(path, "config.json")
        try:
            with open(config_path, encoding="utf-8-sig") as file:
                modules = json.load(file).get("effect", {}).get("Link", [])
        except (OSError, ValueError):
            return None
        # Only use a standalone LUT component, never a file found inside a
        # layered effect that also runs segmentation, shaders or adjustments.
        if len(modules) != 1 or modules[0].get("type") != "AmazingFilter" or modules[0].get("path"):
            return None
        files = [name for name in os.listdir(path) if name.lower().endswith(".cube")]
        if len(files) != 1:
            return None
        path = os.path.join(path, files[0])
    if not path.lower().endswith(".cube") or not os.path.isfile(path):
        return None
    if path not in cache:
        name = f"lut{len(cache)}.cube"
        shutil.copyfile(path, os.path.join(tmp, name))
        cache[path] = name
    strength = max(0.0, min(1.0, float(material.get("value", 1))))
    return cache[path], strength


def apply_lut(graph, source, output, name, strength, interval=None):
    graph.extend([
        f"[{source}]format=yuv420p,split[{output}Original][{output}Input]",
        f"[{output}Input]lut3d=file={name}:interp=trilinear,format=yuv420p[{output}Graded]",
    ])
    amount = f"{strength:.9g}"
    if interval:
        start, end = interval
        amount = f"{amount}*gte(T,{start:.6f})*lt(T,{end:.6f})"
    graph.append(f"[{output}Original][{output}Graded]"
                 f"blend=all_expr='A+(B-A)*({amount})'[{output}]")


def sticker_atlas(folder):
    script_path = os.path.join(folder, "infoSticker.lua")
    if not os.path.isfile(script_path):
        return None
    with open(script_path, encoding="utf-8-sig") as file:
        script = file.read()
    data = re.search(r'\bdata\s*=\s*"([^"]+\.json)"', script, re.I)
    image = re.search(r'\bsource\s*=\s*"([^"]+\.png)"', script, re.I)
    rate = re.search(r':setFps\(\s*(\d+)\s*\)', script)
    if not (data and image and rate and "materialType.autoAtlas" in script):
        return None
    root = os.path.realpath(folder)
    paths = [os.path.realpath(os.path.join(root, match[1])) for match in (data, image)]
    if any(not os.path.isfile(p) or not os.path.normcase(p).startswith(os.path.normcase(root + os.sep))
           for p in paths):
        return None
    try:
        with open(paths[0], encoding="utf-8-sig") as file:
            config = json.load(file)
        frames = config["frames"]
        width, height = (int(frames[0]["frame"][axis]) for axis in ("w", "h"))
        atlas_width, atlas_height = (int(config["meta"]["size"][axis]) for axis in ("w", "h"))
        if not 1 <= len(frames) <= 512 or min(width, height, int(rate[1])) <= 0:
            return None
        positions = []
        for frame in frames:
            rectangle = frame["frame"]
            x, y = (int(rectangle[axis]) for axis in ("x", "y"))
            if (frame.get("rotated") or frame.get("trimmed") or
                    rectangle["w"] != width or rectangle["h"] != height or
                    not (0 <= x <= atlas_width - width and 0 <= y <= atlas_height - height)):
                return None
            positions.append((x, y))
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        return None
    return paths[1], int(rate[1]), width, height, positions


def atlas_crop(width, height, positions, loop):
    frame = f"mod(n,{len(positions)})" if loop else f"min(n,{len(positions)-1})"
    offsets = []
    for axis in range(2):
        expression = str(positions[-1][axis])
        for i in range(len(positions) - 2, -1, -1):
            expression = f"if(eq({frame},{i}),{positions[i][axis]},{expression})"
        offsets.append(expression)
    return f"crop=w={width}:h={height}:x='{offsets[0]}':y='{offsets[1]}':exact=1"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", help="draft folder (contains draft_content.json)")
    ap.add_argument("-o", "--out", default="out.mp4")
    ap.add_argument("--music", action="append", default=[],
                    help="FILE[:offset_sec] your own licensed music (repeatable)")
    ap.add_argument("--crf", type=int, default=18)
    ap.add_argument("--no-text", action="store_true")
    ap.add_argument("--resolution", type=int, choices=(480, 720, 1080, 1440, 2160),
                    help="output short side in pixels (default: draft canvas)")
    ap.add_argument("--fps", type=int, choices=(24, 25, 30, 48, 50, 60))
    ap.add_argument("--codec", choices=("auto", "h264", "hevc"), default="auto")
    ap.add_argument("--bitrate", type=int, help="target video bitrate in kbps")
    ap.add_argument("--start", type=float, default=0.0, help="export start in seconds")
    ap.add_argument("--end", type=float, help="export end in seconds")
    args = ap.parse_args()

    output_path = os.path.abspath(args.out)
    extension = os.path.splitext(output_path)[1].lower()
    audio_codecs = {".mp3": "libmp3lame", ".wav": "pcm_s16le", ".flac": "flac", ".aac": "aac"}
    audio_only = extension in audio_codecs
    if not audio_only and extension not in (".mp4", ".mov"):
        ap.error("output format must be MP4, MOV, MP3, WAV, FLAC or AAC")
    if args.bitrate is not None and args.bitrate <= 0:
        ap.error("--bitrate must be greater than zero")
    global ENC
    encoder = None
    if not audio_only:
        ENC = pick_encoder("h264") or "mpeg4"
        encoder = ENC if args.codec == "auto" else pick_encoder(args.codec)
        if not encoder:
            ap.error(f"这台电脑上没有可用的 {args.codec.upper()} 编码器，请改用 H.264")
    if not FONT and not args.no_text:
        print("[warn] 没有找到字幕字体，跳过字幕")
        args.no_text = True

    d = load_draft(args.folder)
    cv = d["canvas_config"]; W, H = cv["width"], cv["height"]
    fps = float(d.get("fps", 30.0))
    total = d["duration"] / US
    end_time = total if args.end is None else args.end
    if not all(map(math.isfinite, (args.start, end_time))) or not 0 <= args.start < end_time <= total:
        ap.error(f"导出区间必须在草稿时长之内（0 到 {total:.2f} 秒），且起点早于终点")
    mats = d["materials"]
    vids = {m["id"]: m for m in mats["videos"]}
    auds = {m["id"]: m for m in mats["audios"]}
    txts = {m["id"]: m for m in mats["texts"]}
    transitions = {m["id"]: m for m in (mats.get("transitions") or [])}
    speeds = {m["id"]: m for m in (mats.get("speeds") or [])}
    audio_fades = {m["id"]: m for m in (mats.get("audio_fades") or [])}
    stickers = {m["id"]: m for m in (mats.get("stickers") or [])}
    animations = {m["id"]: m for m in (mats.get("material_animations") or [])}
    transition_filters = {
        "mix": "fade", "cross fade": "fade", "crossfade": "fade", "叠化": "fade",
        "black fade": "fadeblack", "fade to black": "fadeblack", "黑场淡出": "fadeblack",
        "white fade": "fadewhite", "fade to white": "fadewhite", "白场淡出": "fadewhite",
        "dissolve": "dissolve",
        **{f"wipe {direction}": f"wipe{direction}" for direction in ("left", "right", "up", "down")},
        **{f"slide {direction}": f"slide{direction}" for direction in ("left", "right", "up", "down")},
    }
    effects = {m["id"]: m for key in ("video_effects", "effects", "filters")
               for m in (mats.get(key) or []) if "id" in m}
    effect_filters = {
        "blur": "gblur=sigma=8", "gaussian blur": "gblur=sigma=8", "高斯模糊": "gblur=sigma=8",
        "black and white": "hue=s=0", "grayscale": "hue=s=0", "黑白": "hue=s=0",
        "negative": "negate", "反色": "negate",
        "vignette": "vignette=PI/5", "暗角": "vignette=PI/5",
    }

    print(f"[info] canvas {W}x{H} @ {fps}fps  total {total:.2f}s")
    print(f"[info] ffmpeg: {FFMPEG}")
    print(f"[info] video encoder: {encoder or '-'}")
    # Clips well outside the export range are skipped; the margin keeps the
    # neighbours a transition near either end of the range needs.
    margin = 2.0

    def in_range(segment):
        timerange = segment.get("target_timerange") or {}
        begin = timerange.get("start", 0) / US
        return begin < end_time + margin and begin + timerange.get("duration", 0) / US > args.start - margin

    jobs = sum(1 for tk in d["tracks"] if tk.get("type") in ("video", "audio")
               for s in tk.get("segments", []) if in_range(s))
    jobs_done = 0
    report(0)

    tmp = tempfile.mkdtemp(prefix="ccx_")
    atexit.register(shutil.rmtree, tmp, True)  # also after a failed step
    print(f"[temp] {tmp}", flush=True)  # the GUI removes it after a cancel
    video_tracks = {}
    video_count = 0
    audio_streams = {}
    lut_cache = {}
    text_cmds = []
    timed_effects = []
    audio_inputs = []   # (path, delay_seconds, volume)

    # ---- walk tracks ----
    for track_index, tk in enumerate(d["tracks"]):
        typ = tk.get("type")
        for s in tk.get("segments", []):
            if not in_range(s):
                continue
            tt = s.get("target_timerange", {})
            t0 = tt.get("start", 0) / US
            dur = tt.get("duration", 0) / US
            st = s.get("source_timerange") or {}
            refs = s.get("extra_material_refs") or []
            if typ in ("video", "audio"):
                speed_info = next((speeds[ref] for ref in refs if ref in speeds), {})
                speed = float(speed_info.get("speed") or s.get("speed") or 1)
                if speed_info.get("curve_speed"):
                    print("[warn] speed curve rendered at its average speed")
                    speed = (st.get("duration", dur * US) / US) / dur if dur > 0 else 1
                if not math.isfinite(speed) or speed <= 0:
                    sys.exit(f"[fatal] invalid clip speed: {speed}")
                reverse = bool(s.get("reverse"))
            else:
                speed = 1
            keyframes = clip_keyframes(s, st.get("start", 0) / US, speed)
            if any(curve not in ("line", "linear", "hold", "constant")
                   for points in keyframes.values() for _, (_, curve) in points):
                print(f"[warn] easing approximated with linear interpolation: {s.get('id')}")

            if typ == "video":
                m = vids.get(s["material_id"])
                if not m:
                    continue
                src = resolve_media(m["path"], args.folder)
                reverse_path = resolve_media(m.get("reverse_path"), args.folder)
                if reverse and reverse_path and os.path.isfile(reverse_path):
                    src = reverse_path
                    reverse = False
                if not os.path.isfile(src):
                    print(f"[warn] 找不到视频素材，已跳过：{src}")
                    continue
                s0 = st.get("start", 0) / US
                sdur = st.get("duration", dur) / US
                if dur <= 0 or sdur <= 0 or s.get("visible") is False:
                    continue
                outp = os.path.join(tmp, f"seg{video_count:02d}.mp4")
                video_count += 1
                vf = ",".join([
                    *(["reverse"] if reverse else []),
                    *([f"setpts=(PTS-STARTPTS)/{speed:.12g}"] if speed != 1 else []),
                    f"scale={W}:{H}:force_original_aspect_ratio=decrease",
                    f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black",
                    f"fps={fps}", "setsar=1", "format=yuv420p",
                ])
                clip_luts = []
                if not audio_only:
                    for ref in refs:
                        effect = effects.get(ref)
                        if effect:
                            name = effect.get("name") or ""
                            effect_filter = effect_filters.get(name.strip().casefold())
                            lut = local_lut(effect, args.folder, tmp, lut_cache) if effect.get("path") else None
                            if lut:
                                clip_luts.append(lut)
                            elif effect_filter:
                                vf += "," + effect_filter
                            else:
                                print(f"[warn] unsupported clip effect: {name or ref!r}")
                if src not in audio_streams:
                    probe = subprocess.check_output(
                        [FFPROBE, "-v", "error", "-select_streams", "a:0",
                         "-show_entries", "stream=index", "-of", "csv=p=0", src],
                        text=True, creationflags=NO_WINDOW
                    )
                    audio_streams[src] = bool(probe.strip())
                has_audio = audio_streams[src] and m.get("has_audio") is not False
                if audio_only and not has_audio:
                    continue
                cmd = [FFMPEG, "-y", "-ss", f"{s0:.6f}", "-t", f"{sdur:.6f}", "-i", src]
                if audio_only:
                    outp = os.path.splitext(outp)[0] + ".wav"
                    cmd += ["-vn"]
                else:
                    cmd += ["-map", "0:v:0", "-vf", vf, *venc_args(16)]
                volume = keyframe_expr(keyframes, "KFTypeVolume", float(s.get("volume", 1)), "t")
                if has_audio:
                    fade = next((audio_fades[ref] for ref in refs if ref in audio_fades), m.get("audio_fade") or {})
                    audio_filters = [
                        *audio_tempo(speed, reverse), "aresample=async=1:first_pts=0",
                        *fade_filters(fade, dur),
                    ]
                    cmd += ["-map", "0:a:0", "-ar", "48000", "-ac", "2",
                            "-c:a", "pcm_s16le" if audio_only else "aac",
                            "-af", ",".join(audio_filters)]
                run([*cmd, outp])
                jobs_done += 1
                report(0.4 * jobs_done / max(1, jobs))
                transition =next((transitions[ref] for ref in refs if ref in transitions), None)
                if audio_only:
                    audio_inputs.append((outp, t0, volume))
                    continue
                clip = s.get("clip") or {}
                layer = s.get("track_render_index", 0)
                video_tracks.setdefault((False, layer, track_index), []).append(dict(
                    path=outp, start=t0, duration=dur, clip=clip,
                    transition=transition, volume=volume if has_audio else None,
                    keyframes=keyframes, luts=clip_luts,
                    uniform_scale=bool((s.get("uniform_scale") or {}).get("on")),
                    source=os.path.abspath(src), source_start=s0, source_duration=sdur,
                    media_duration=float(m.get("duration") or 0) / US,
                    source_filter=vf if speed == 1 and not reverse else None,
                ))

            elif typ == "audio":
                m = auds.get(s["material_id"])
                if not m:
                    continue
                src = resolve_media(m["path"], args.folder)
                if not os.path.isfile(src):
                    print(f"[warn] 找不到音频素材，已跳过：{src}")
                    continue
                s0 = st.get("start", 0) / US
                sdur = st.get("duration", dur) / US
                trimmed = os.path.join(tmp, f"aud{len(audio_inputs):02d}.wav")
                if dur <= 0 or sdur <= 0:
                    continue
                cmd = [FFMPEG, "-y", "-ss", f"{s0:.6f}", "-t", f"{sdur:.6f}", "-i", src,
                       "-vn", "-ar", "48000", "-ac", "2"]
                fade = next((audio_fades[ref] for ref in refs if ref in audio_fades), {})
                audio_filters = [*audio_tempo(speed, reverse), *fade_filters(fade, dur)]
                if audio_filters:
                    cmd += ["-af", ",".join(audio_filters)]
                run([*cmd, trimmed])
                jobs_done += 1
                report(0.4 * jobs_done / max(1, jobs))
                volume = keyframe_expr(keyframes, "KFTypeVolume", float(s.get("volume", 1)), "t")
                audio_inputs.append((trimmed, t0, volume))

            elif typ in ("sticker", "image_sticker") and not audio_only:
                if dur <= 0 or s.get("visible") is False:
                    continue
                material = stickers.get(s["material_id"], {})
                src = resolve_media(material.get("path") or "", args.folder)
                atlas = sticker_atlas(src) if src and os.path.isdir(src) else None
                if atlas:
                    src = atlas[0]
                suffix = os.path.splitext(src)[1].lower()
                if suffix not in (".png", ".jpg", ".jpeg", ".gif", ".apng", ".webp") or not os.path.isfile(src):
                    name = material.get("name") or material.get("resource_id") or s["material_id"]
                    print(f"[warn] unsupported sticker package: {name!r}")
                    continue
                left_reveal = None
                for ref in refs:
                    for animation in animations.get(ref, {}).get("animations") or []:
                        if (animation.get("type") == "in" and animation.get("resource_id") == "7597700024860036353"
                                and animation.get("duration", 0) > 0):
                            left_reveal = (animation.get("start", 0) / US, animation["duration"] / US)
                        else:
                            print(f"[warn] unsupported sticker animation: {animation.get('name')!r}")
                layer = s.get("track_render_index", 0)
                video_tracks.setdefault((True, layer, track_index), []).append(dict(
                    path=os.path.abspath(src), start=t0, duration=dur, clip=s.get("clip") or {},
                    transition=None, volume=None, sticker=True, keyframes=keyframes,
                    atlas=atlas, atlas_loop=material.get("cycle_setting", True), left_reveal=left_reveal,
                    uniform_scale=bool((s.get("uniform_scale") or {}).get("on")),
                ))

            elif typ in ("effect", "video_effect", "filter") and not audio_only:
                material_id = s["material_id"]
                effect = effects.get(material_id, {})
                name = effect.get("name") or ""
                effect_filter = effect_filters.get(name.strip().casefold())
                if effect.get("resource_id") == "7399464929830423813":
                    blur = next((p.get("value") for p in effect.get("adjust_params") or []
                                 if p.get("name") == "effects_adjust_blur"), 0.5)
                    # The native shader is a separate implementation; map
                    # its normalized control to the existing Gaussian preset.
                    effect_filter = f"gblur=sigma={16 * max(0, min(1, float(blur))):.6f}"
                lut = local_lut(effect, args.folder, tmp, lut_cache) if effect.get("path") else None
                if lut or effect_filter:
                    timed_effects.append((effect_filter, lut, t0, t0 + dur))
                else:
                    print(f"[warn] unsupported timeline effect: {name or material_id!r}")

            elif typ == "text" and not args.no_text and not audio_only:
                m = txts.get(s["material_id"])
                if not m:
                    continue
                try:
                    content = json.loads(m["content"])
                    text = content.get("text", "")
                    styles = content.get("styles", [{}])
                    size = styles[0].get("size", 6)
                    col = styles[0].get("fill", {}).get("content", {}).get(
                        "solid", {}).get("color", [1, 1, 1])
                except Exception:
                    text, size, col = m.get("content", ""), 6, [1, 1, 1]
                if not text:
                    continue
                clip = s.get("clip", {})
                tr = clip.get("transform", {})
                nx, ny = tr.get("x", 0.0), tr.get("y", 0.0)
                font_px = max(12, round(H * size / 100.0))
                # normalized transform -> pixel center (y+ is up in CapCut)
                text_time = f"(t-{t0:.9f})"
                nx = keyframe_expr(keyframes, "KFTypePositionX", float(nx), text_time)
                ny = keyframe_expr(keyframes, "KFTypePositionY", float(ny), text_time)
                cx = f"{W / 2}*(1+({nx}))"
                cy = f"{H / 2}*(1-({ny}))"
                alpha = keyframe_expr(keyframes, "KFTypeAlpha", float(clip.get("alpha", 1)), text_time)
                r_, g_, b_ = [int(round(c * 255)) for c in col[:3]]
                color = f"0x{r_:02X}{g_:02X}{b_:02X}"
                text_cmds.append(dict(
                    text=text, font_px=font_px, color=color,
                    cx=cx, cy=cy, alpha=alpha, t0=t0, t1=t0 + dur))

    if not video_tracks and not audio_only:
        sys.exit("[fatal] 这个区间里没有能渲染的视频片段（素材可能找不到）")

    # ---- external user music ----
    for spec in args.music:
        if ":" in spec and not re.match(r"^[A-Za-z]:\\", spec):
            path, off = spec.rsplit(":", 1); off = float(off)
        elif spec.count(":") >= 2:
            # drive letter present, last colon is offset
            path, off = spec.rsplit(":", 1); off = float(off)
        else:
            path, off = spec, 0.0
        if not os.path.isfile(path):
            sys.exit(f"[fatal] 找不到音乐文件：{path}")
        audio_inputs.append((path, off, 1.0))

    # ---- composite layers on the draft timeline, then mix and encode ----
    inputs = ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    mix_inputs = []
    for idx, (p, delay, volume) in enumerate(audio_inputs, start=1):
        inputs += ["-i", os.path.abspath(p)]
        mix_inputs.append((idx, delay, volume))

    filt = []
    if not audio_only:
        filt.append(f"color=c=black:s={W}x{H}:r={fps}[canvas]")
        last = "canvas"
        input_index = len(audio_inputs) + 1
        effect_boundary = (True, -math.inf, -1)
        for track in sorted([*video_tracks, effect_boundary]):
            if track == effect_boundary:
                # Video effects render below the sticker and caption tracks.
                for i, (effect_filter, lut, start, end) in enumerate(timed_effects):
                    output = f"effect{i}"
                    if lut:
                        apply_lut(filt, last, output, *lut, interval=(start, end))
                    else:
                        separator = ":" if "=" in effect_filter else "="
                        filt.append(f"[{last}]{effect_filter}{separator}"
                                    f"enable='gte(t,{start:.6f})*lt(t,{end:.6f})'[{output}]")
                    last = output
                continue
            clips = sorted(video_tracks[track], key=lambda clip: clip["start"])
            for clip in clips:
                idx = input_index
                input_index += 1
                if clip.get("atlas"):
                    inputs += ["-loop", "1", "-framerate", str(clip["atlas"][1]), "-t", f"{clip['duration']:.6f}"]
                elif clip.get("sticker") and os.path.splitext(clip["path"])[1].lower() in (".gif", ".apng"):
                    inputs += ["-ignore_loop", "0", "-t", f"{clip['duration']:.6f}"]
                inputs += ["-i", clip["path"]]
                if clip["volume"] is not None:
                    mix_inputs.append((idx, clip["start"], clip["volume"]))
                start_frame = round(clip["start"] * fps)
                end_frame = round((clip["start"] + clip["duration"]) * fps)
                count = max(1, end_frame - start_frame)
                start, end = start_frame / fps, max(end_frame, start_frame + 1) / fps
                transform = clip["clip"].get("transform") or {}
                scale = clip["clip"].get("scale") or {}
                scale_x, scale_y = float(scale.get("x", 1)), float(scale.get("y", 1))
                x = float(transform.get("x", 0))
                y = float(transform.get("y", 0))
                rotation = float(clip["clip"].get("rotation") or 0)
                alpha = float(clip["clip"].get("alpha", 1))
                flip = clip["clip"].get("flip") or {}
                keys = clip["keyframes"]
                clock = f"(t-{start:.9f})"
                x_expr = keyframe_expr(keys, "KFTypePositionX", x, clock)
                y_expr = keyframe_expr(keys, "KFTypePositionY", y, clock)
                sx = keyframe_expr(keys, "KFTypeScaleX", scale_x, "t")
                sy = keyframe_expr(keys, "KFTypeScaleY", scale_y, "t")
                if clip["uniform_scale"] and "KFTypeScaleX" in keys and "KFTypeScaleY" not in keys:
                    sy = sx
                chain = []
                if clip.get("atlas"):
                    _, _, width, height, positions = clip["atlas"]
                    chain += [atlas_crop(width, height, positions, clip["atlas_loop"]),
                              f"scale={max(2, round(width * W / 1440) * 2)}:"
                              f"{max(2, round(height * W / 1440) * 2)}"]
                if clip.get("sticker"):
                    chain += [f"fps={fps}", "setsar=1", "format=yuva420p"]
                chain += [f"tpad=stop_mode=clone:stop={count}", f"trim=end_frame={count}",
                          "setpts=PTS-STARTPTS"]
                if flip.get("horizontal"):
                    chain.append("hflip")
                if flip.get("vertical"):
                    chain.append("vflip")
                if rotation or alpha != 1 or "KFTypeAlpha" in keys or "KFTypeRotation" in keys:
                    chain.append("format=yuva420p")
                # Keep geq before the dynamic scale: it needs fixed input dimensions.
                if "KFTypeAlpha" in keys or clip.get("left_reveal"):
                    gain = f"clip({keyframe_expr(keys, 'KFTypeAlpha', alpha, 'T')},0,1)"
                    if clip.get("left_reveal"):
                        offset, duration = clip["left_reveal"]
                        # The preset animates a right-to-left alpha mask with
                        # quadratic ease-out and a 1% smoothstep edge.
                        progress = f"clip((T-{offset:.9f})/{duration:.9f},0,1)"
                        edge = f"clip((X/W-pow(1-({progress}),2))/0.01,0,1)"
                        gain = (f"st(0,{edge});({gain})*"
                                f"if(gte(T,{offset + duration:.9f}),1,ld(0)*ld(0)*(3-2*ld(0)))")
                    chain.append("geq=lum='lum(X,Y)':cb='cb(X,Y)':cr='cr(X,Y)':"
                                 f"a='({gain})*alpha(X,Y)'")
                elif alpha != 1:
                    chain.append(f"colorchannelmixer=aa={alpha}")
                if "KFTypeRotation" in keys:
                    angle = keyframe_expr(keys, "KFTypeRotation", rotation, "t")
                    chain.append(f"rotate=angle='({angle})*PI/180':ow=ceil(hypot(iw,ih)/2)*2:"
                                 "oh=ceil(hypot(iw,ih)/2)*2:fillcolor=none")
                if scale_x != 1 or scale_y != 1 or "KFTypeScaleX" in keys or "KFTypeScaleY" in keys:
                    chain.append(f"scale=w='max(2,trunc(iw*({sx})/2)*2)':"
                                 f"h='max(2,trunc(ih*({sy})/2)*2)':eval=frame")
                if rotation and "KFTypeRotation" not in keys:
                    angle = math.radians(rotation)
                    chain.append(f"rotate={angle}:ow=rotw({angle}):oh=roth({angle}):fillcolor=none")
                clip["transformed"] = bool(x or y or rotation or alpha != 1 or
                                           scale_x != 1 or scale_y != 1 or any(flip.values()) or
                                           keys.keys() & {"KFTypePositionX", "KFTypePositionY", "KFTypeScaleX",
                                                          "KFTypeScaleY", "KFTypeRotation", "KFTypeAlpha"})
                if clip["transformed"]:
                    clip["transition_chain"] = ",".join(chain)
                    clip["transition_x"] = keyframe_expr(keys, "KFTypePositionX", x, "t")
                    clip["transition_y"] = keyframe_expr(keys, "KFTypePositionY", y, "t")
                chain.append(f"setpts=PTS-STARTPTS+{start:.9f}/TB")
                visual_input = f"{idx}:v"
                for lut_index, (name, strength) in enumerate(clip.get("luts", [])):
                    output = f"lutClip{idx}_{lut_index}"
                    apply_lut(filt, visual_input, output, name, strength)
                    visual_input = output
                filt.append(f"[{visual_input}]{','.join(chain)}[clip{idx}]")
                clip.update(index=idx, x_expr=x_expr, y_expr=y_expr, window=(start, end), holes=[])

            blends = []
            for left, right in zip(clips, clips[1:]):
                transition = left["transition"]
                if not transition:
                    continue
                name = transition.get("name") or ""
                transition_filter = transition_filters.get(name.strip().casefold())
                if transition.get("resource_id") == "6724845717472416269":
                    transition_filter = "fade"
                if not transition_filter:
                    print(f"[warn] unsupported transition: {name or transition['id']!r}")
                    continue
                if abs(left["start"] + left["duration"] - right["start"]) >= 0.5 / fps:
                    print(f"[warn] {name} transition requires adjacent video clips")
                    continue
                transformed = left["transformed"] or right["transformed"]
                frame_count = round(transition.get("duration", 0) / US * fps)
                left_frames, right_frames = (round(c["duration"] * fps) for c in (left, right))
                before = min(frame_count // 2, left_frames)
                after = min(frame_count - before, right_frames)
                if before < 1 or after < 1:
                    continue
                blend_frames = before + after
                start = (round(right["start"] * fps) - before) / fps
                i = input_index
                input_index += 2
                # Use real frames around the cut when the unretimed source
                # has extra footage, then hold edges for any missing frames.
                left_filter = f"trim=start_frame={left_frames - before}:end_frame={left_frames}"
                right_filter = f"trim=end_frame={after}"
                right_hold = before
                real_frames = not transformed and left["source_filter"] and right["source_filter"]
                if real_frames:
                    left_end = left["source_start"] + left["source_duration"]
                    left_extra = min(after, max(0, math.floor((left["media_duration"] - left_end) * fps + 0.0001)))
                    right_extra = min(before, max(0, math.floor(right["source_start"] * fps + 0.0001)))
                    left_start = max(left["source_start"], left_end - before / fps)
                    right_start = right["source_start"] - right_extra / fps
                    inputs += ["-ss", f"{left_start:.6f}", "-t", f"{(before + left_extra) / fps:.6f}",
                               "-i", left["source"],
                               "-ss", f"{right_start:.6f}", "-t", f"{(after + right_extra) / fps:.6f}",
                               "-i", right["source"]]
                    # The source windows already start at the blend frames.
                    left_filter = right_filter = "null"
                    right_hold -= right_extra
                else:
                    inputs += ["-i", left["path"], "-i", right["path"]]
                side_inputs = []
                for cut_input, clip in ((i, left), (i + 1, right)):
                    label = f"{cut_input}:v"
                    if real_frames:
                        filt.append(f"[{label}]{clip['source_filter']}[source{cut_input}]")
                        label = f"source{cut_input}"
                    # Grade before the transform, as the timeline layer does.
                    for lut_index, (lut_name, strength) in enumerate(clip.get("luts", [])):
                        output = f"lutCut{cut_input}_{lut_index}"
                        apply_lut(filt, label, output, lut_name, strength)
                        label = output
                    if clip["transformed"]:
                        # Keep lower tracks visible around a moved or scaled
                        # clip. ACT: This FFmpeg drops the alpha when it
                        # overlays at a negative y onto a transparent canvas,
                        # so draw on opaque canvases and merge a coverage mask.
                        cut = f"cut{cut_input}"
                        x, y = clip["transition_x"], clip["transition_y"]
                        place = (f"overlay=x='(main_w-overlay_w)/2+({x})*main_w/2':"
                                 f"y='(main_h-overlay_h)/2-({y})*main_h/2':eof_action=pass:repeatlast=0")
                        canvas = f"color=c=black:s={W}x{H}:r={fps}:d={clip['duration']:.9f}"
                        filt.extend([
                            f"{canvas}[{cut}Canvas]",
                            f"{canvas},lutyuv=y=0[{cut}MaskCanvas]",
                            f"[{label}]{clip['transition_chain']},split[{cut}Source][{cut}Shape]",
                            f"[{cut}Shape]lutyuv=y=maxval:u=128:v=128[{cut}White]",
                            f"[{cut}Canvas][{cut}Source]{place}[{cut}Picture]",
                            f"[{cut}MaskCanvas][{cut}White]{place},extractplanes=y[{cut}Mask]",
                            f"[{cut}Picture][{cut}Mask]alphamerge[{cut}]",
                        ])
                        label = cut
                    side_inputs.append(label)
                left_input, right_input = side_inputs
                # ACT: tpad gives its first cloned frame the previous frame's
                # timestamp here, so number the frames again after padding.
                frame_clock = f"settb=1/1000000,setpts=N/({fps})/TB"
                filt.extend([
                    f"[{left_input}]{left_filter},setpts=PTS-STARTPTS,"
                    f"tpad=stop_mode=clone:stop={blend_frames},trim=end_frame={blend_frames},"
                    f"{frame_clock},format=yuva444p[mixLeft{i}]",
                    f"[{right_input}]{right_filter},setpts=PTS-STARTPTS,"
                    f"tpad=start_mode=clone:start={right_hold}:stop_mode=clone:stop={blend_frames},"
                    f"trim=end_frame={blend_frames},{frame_clock},format=yuva444p[mixRight{i}]",
                    f"[mixLeft{i}][mixRight{i}]xfade=transition={transition_filter}:"
                    f"duration={blend_frames / fps:.6f},"
                    # A quarter frame early, so microsecond rounding cannot
                    # make the overlay pick the previous blend frame, and one
                    # frame longer, so its last frame lasts a full frame.
                    f"trim=end_frame={blend_frames},tpad=stop_mode=clone:stop=1,{frame_clock},"
                    f"setpts=PTS+{start - 0.25 / fps:.9f}/TB[blend{i}]",
                ])
                # The blend replaces both clips in its window, so their own
                # overlays must not show through a partly transparent blend.
                # Switch at half frames so rounded times never leave a frame
                # with neither the clips nor the blend, or with both.
                window = (start - 0.5 / fps, start + (blend_frames - 0.5) / fps)
                left["holes"].append(window)
                right["holes"].append(window)
                blends.append((i, window))

            for clip in clips:
                start, end = clip["window"]
                enable = f"gte(t,{start:.9f})*lt(t,{end:.9f})" + "".join(
                    f"*(1-gte(t,{a:.9f})*lt(t,{b:.9f}))" for a, b in clip["holes"])
                idx = clip["index"]
                filt.append(
                    f"[{last}][clip{idx}]overlay=x='(main_w-overlay_w)/2+({clip['x_expr']})*main_w/2':"
                    f"y='(main_h-overlay_h)/2-({clip['y_expr']})*main_h/2':eof_action=pass:repeatlast=0:"
                    f"enable='{enable}'[layer{idx}]")
                last = f"layer{idx}"
            for i, (a, b) in blends:
                filt.append(f"[{last}][blend{i}]overlay=eof_action=pass:repeatlast=0:"
                            f"enable='gte(t,{a:.9f})*lt(t,{b:.9f})'[mix{i}]")
                last = f"mix{i}"

        for i, tc in enumerate(text_cmds):
            text_file = f"text{i}.txt"
            with open(os.path.join(tmp, text_file), "w", encoding="utf-8", newline="\n") as f:
                f.write(tc["text"])
            o = f"vt{i}"
            filt.append(
                f"[{last}]drawtext=fontfile='{FONT_FILTER}':textfile={text_file}:expansion=none:"
                f"fontsize={tc['font_px']}:fontcolor={tc['color']}:"
                f"borderw=2:bordercolor=black@0.8:"
                f"x='{tc['cx']}-text_w/2':y='{tc['cy']}-text_h/2':"
                f"alpha='clip({tc['alpha']},0,1)':"
                f"enable='between(t,{tc['t0']:.3f},{tc['t1']:.3f})'[{o}]")
            last = o

        if args.resolution:
            factor = args.resolution / min(W, H)
            out_width, out_height = (round(side * factor / 2) * 2 for side in (W, H))
            filt.append(f"[{last}]scale={out_width}:{out_height},setsar=1[scaled]")
            last = "scaled"
        if args.fps:
            filt.append(f"[{last}]fps={args.fps}[rated]")
            last = "rated"

    # ACT: The bundled FFmpeg always averages amix inputs. Pad every input
    # with silence so the divisor stays fixed, then undo the averaging.
    filt.append("[0:a]apad[baseAudio]")
    amix_labels = ["[baseAudio]"]
    for idx, delay, vol in mix_inputs:
        lbl = f"a{idx}"
        ms = int(delay * 1000)
        filt.append(f"[{idx}:a]volume='max(0,{vol})':eval=frame,adelay={ms}|{ms},apad[{lbl}]")
        amix_labels.append(f"[{lbl}]")
    n = len(amix_labels)
    filt.append(f"{''.join(amix_labels)}amix=inputs={n},volume={n},"
                "alimiter=limit=0.95:attack=1:level=0[aout]")

    filter_path = os.path.join(tmp, "timeline.txt")
    with open(filter_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(";\n".join(filt))
    cmd = [os.path.abspath(FFMPEG), "-y", *inputs, "-filter_complex_script", filter_path,
           "-ss", f"{args.start:.6f}", "-t", f"{end_time - args.start:.6f}"]
    if audio_only:
        cmd += ["-map", "[aout]", "-c:a", audio_codecs[extension]]
        if extension in (".mp3", ".aac"):
            cmd += ["-b:a", "192k"]
    else:
        video_args = (["-c:v", encoder, "-b:v", f"{args.bitrate}k"] if args.bitrate
                      else venc_args(args.crf, encoder))
        cmd += ["-map", f"[{last}]", "-map", "[aout]", *video_args,
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
        if args.codec == "hevc":
            cmd += ["-tag:v", "hvc1"]
    cmd.append(output_path)
    print("[info] final render ...")
    run(cmd, progress=(0.4, 0.6, end_time - args.start), cwd=tmp)
    report(1)
    print(f"\n[done] -> {output_path}")
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
