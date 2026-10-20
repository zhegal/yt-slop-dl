"""Small metadata summary and lossless container compatibility for YouTube codecs."""


def codec_name(value):
    codec = (value or "none").split(".")[0].lower()
    return {"avc1": "h264", "av01": "av1", "vp09": "vp9", "hvc1": "hevc", "hev1": "hevc",
            "mp4a": "aac", "aacl": "aac", "ec-3": "eac3", "ac-3": "ac3", "vrbs": "vorbis"}.get(codec, codec)


def available_containers(video_codec: str, audio_codec: str) -> list[str]:
    """Common YouTube codecs supported by the FFmpeg MP4/WebM muxers.

    Container choice never changes the selected streams or their quality.
    MKV is the general-purpose fallback; WebM has a restricted codec set.
    """
    video, audio = codec_name(video_codec), codec_name(audio_codec)
    result = ["mkv"]
    if video in {"none", "h264", "hevc", "av1", "vp9"} and audio in {"none", "aac", "opus", "mp3", "ac3", "eac3"}:
        result.append("mp4")
    if video in {"none", "vp8", "vp9", "av1"} and audio in {"none", "opus", "vorbis"}:
        result.append("webm")
    return result


def summarize_info(info: dict) -> dict:
    if info.get("is_live") or info.get("is_upcoming") or info.get("live_status") in {"is_live", "is_upcoming", "post_live"}:
        raise ValueError("Live/upcoming streams are not supported.")
    streams = info.get("requested_formats") or [info]
    video = next((f for f in streams if f.get("vcodec") not in {None, "none"}), None)
    audio = next((f for f in streams if f.get("acodec") not in {None, "none"}), None)
    if not video or not audio:
        raise ValueError("No downloadable video and audio streams were found.")
    ids = [str(f["format_id"]) for f in streams]
    resolutions = sorted({int(f["height"]) for f in info.get("formats", [])
                          if f.get("height") and f.get("vcodec") not in {None, "none", "images"} and not f.get("has_drm")})
    video_options, tracks = stream_options(info, video, audio)
    default_track = next(track for track in tracks if track["id"] == audio_track_key(audio))
    return {
        "video_options": video_options, "audio_options": default_track["options"],
        "audio_tracks": [default_track, *(track for track in tracks if track is not default_track)],
        "default_audio_track": default_track["id"],
        "id": str(info["id"]), "title": str(info.get("title") or info["id"])[:300],
        "duration": info.get("duration"), "channel": str(info.get("channel") or info.get("uploader") or "")[:200],
        "width": video.get("width"), "height": video.get("height"), "fps": video.get("fps"),
        "vcodec": codec_name(video.get("vcodec")), "acodec": codec_name(audio.get("acodec")),
        "format_id": "+".join(ids), "containers": available_containers(video.get("vcodec"), audio.get("acodec")),
        "resolutions": resolutions,
    }


def audio_track_key(stream: dict) -> str:
    track = stream.get("audio_track") or {}
    return str(track.get("id") or stream.get("language") or "und")


def audio_options_for(info: dict, track_id: str | None = None) -> list:
    tracks = info.get("audio_tracks")
    if not tracks:
        return info["audio_options"]
    track_id = track_id if track_id is not None else info["default_audio_track"]
    track = next((track for track in tracks if track["id"] == track_id), None)
    if not track:
        raise ValueError("unavailable_audio_track")
    return track["options"]


def stream_options(info: dict, best_video: dict, best_audio: dict) -> tuple[list, list]:
    # yt-dlp has already ranked formats from worst to best. Preserve its ranking
    # within each resolution/frame-rate group instead of implementing a sorter.
    formats = [f for f in info.get("formats", []) if f.get("format_id") and not f.get("has_drm")]
    def compact(f):
        return {key: f.get(key) for key in ("format_id", "height", "width", "fps", "abr", "ext", "language")} | {
            "vcodec": codec_name(f.get("vcodec")), "acodec": codec_name(f.get("acodec")),
            "track_id": audio_track_key(f),
            "drc": "DRC" in str(f.get("format_note", "")).upper() or str(f.get("format_id", "")).endswith("-drc"),
        }
    groups = {}
    for f in [*formats, best_video]:
        if f.get("vcodec") in {None, "none", "images"} or not f.get("format_id"):
            continue
        key = (f.get("height") or 0, f.get("fps") or 0)
        previous = groups.get(key)
        # Prefer a separate stream when available so video-only really is silent.
        if previous is None or f.get("acodec") == "none" or previous.get("acodec") != "none":
            groups[key] = f
    videos = [compact(groups[key]) for key in sorted(groups, reverse=True)]
    tracks = {}
    for f in [*formats, best_audio]:
        if f.get("acodec") in {None, "none"} or not f.get("format_id"):
            continue
        if f.get("vcodec") != "none" and best_audio.get("vcodec") == "none" and audio_track_key(f) == "und":
            continue
        key = audio_track_key(f)
        track = tracks.setdefault(key, {"id": key, "language": f.get("language"),
                                       "name": (f.get("audio_track") or {}).get("displayName"),
                                       "original": False, "options": {}})
        track["original"] |= "original" in f"{f.get('format_note', '')} {track['name'] or ''}".lower()
        options = track["options"]
        separate = f.get("vcodec") == "none"
        # Combined files are a fallback only when this track has no separate audio.
        if options:
            previous_separate = next(iter(options.values()))["vcodec"] == "none"
            if not separate and previous_separate:
                continue
            if separate != previous_separate or not separate:
                options.clear()
        options.pop(str(f["format_id"]), None)
        options[str(f["format_id"])] = compact(f)
    for track in tracks.values():
        track["options"] = list(track["options"].values())[::-1]
    return videos, list(tracks.values())


def select_streams(info: dict, quality_id: str, video: bool = True, audio: bool = True, *, audio_track_id: str | None = None,
                   audio_track_ids: list[str] | None = None) -> dict:
    if not video and not audio:
        raise ValueError("empty_selection")
    if audio and audio_track_ids is not None:
        if not audio_track_ids:
            raise ValueError("empty_audio_tracks")
        if len(set(audio_track_ids)) != len(audio_track_ids):
            raise ValueError("unavailable_audio_track")
        result = select_streams(info, quality_id, video, audio, audio_track_id=audio_track_ids[0])
        streams = [audio_options_for(info, track_id)[0] for track_id in audio_track_ids]
        if not video:
            streams[0] = next(f for f in audio_options_for(info, audio_track_ids[0]) if str(f["format_id"]) == quality_id)
        if len(streams) > 1:
            selected_video = next((f for f in info["video_options"] if str(f["format_id"]) == quality_id), None) if video else None
            if any(f["vcodec"] != "none" for f in streams) or (selected_video and selected_video["acodec"] != "none"):
                raise ValueError("multitrack_unavailable")
            result["format_id"] = "+".join(([quality_id] if video else []) + [str(f["format_id"]) for f in streams])
            result["containers"] = [name for name in ("mkv", "mp4", "webm")
                                    if all(name in available_containers(result["vcodec"], f["acodec"]) for f in streams)]
        result["audio_streams"] = [{"format_id": str(f["format_id"]), "language": f.get("language")} for f in streams]
        return result
    audio_options = audio_options_for(info, audio_track_id) if audio else []
    options = info["video_options"] if video else audio_options
    selected = next((f for f in options if str(f["format_id"]) == quality_id), None)
    if not selected:
        raise ValueError("unavailable_quality")
    if video and not audio and selected["acodec"] != "none":
        raise ValueError("video_only_unavailable")
    if video and audio and selected["acodec"] != "none":
        wanted = audio_options[0].get("track_id")
        embedded = selected.get("track_id")
        if wanted != embedded and (selected.get("language") or embedded not in {None, "und"}
                                   or audio_track_id is not None):
            raise ValueError("audio_track_unavailable_for_quality")
    audio_stream = audio_options[0] if video and audio and selected["acodec"] == "none" else selected
    ids = [str(selected["format_id"])]
    if video and audio and selected["acodec"] == "none":
        # Avoid adding a second video through a combined fallback.
        if audio_stream["vcodec"] != "none":
            raise ValueError("audio_track_unavailable_for_quality")
        ids.append(str(audio_stream["format_id"]))
    vcodec = selected["vcodec"] if video else "none"
    acodec = audio_stream["acodec"] if audio else "none"
    containers = available_containers(vcodec, acodec)
    if not video:
        if acodec == "aac":
            containers.append("m4a")
        elif acodec == "opus":
            containers.append("opus")
    return {"format_id": "+".join(ids), "containers": containers, "vcodec": vcodec, "acodec": acodec,
            "height": selected.get("height") if video else None, "fps": selected.get("fps") if video else None}


def format_duration(value) -> str:
    if not isinstance(value, (int, float)) or value < 0 or value != value or value == float("inf"):
        return "—"
    hours, rest = divmod(int(value), 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"
