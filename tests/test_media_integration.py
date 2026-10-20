"""Optional offline integration with real private tools; never downloads tools."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from app.dependencies import detect_platform
from app.downloader import build_arguments, build_inspection_arguments, parse_output
from app.media import summarize_info
from app.worker import tool_environment


@pytest.mark.parametrize("video_codec,audio_codec,expected,container", [("libx264", "aac", "mp4", "auto"), ("libvpx-vp9", "libopus", "mkv", "auto"), ("libvpx-vp9", "libopus", "webm", "webm"), ("libvpx-vp9", "libopus", "mp4", "mp4"), ("libx264", "aac", "mkv", "mkv")])
def test_best_stream_merge_containers_progress_and_stream_copy(tmp_path, video_codec, audio_codec, expected, container):
    configured = os.environ.get("YT_SLOP_DL_TEST_COMPONENTS")
    if not configured:
        pytest.skip("Set YT_SLOP_DL_TEST_COMPONENTS to the installed private components root")
    root = Path(configured) / detect_platform().key
    suffix = ".exe" if os.name == "nt" else ""
    tools = {name: root / ("ffmpeg" if name == "ffprobe" else name) / (name + suffix) for name in ("yt-dlp", "ffmpeg", "ffprobe", "deno")}
    assert all(path.is_file() for path in tools.values())
    env = tool_environment(Path(configured))

    def run(args):
        result = subprocess.run([str(arg) for arg in args], capture_output=True, text=True, encoding="utf-8", timeout=45, env=env)
        assert result.returncode == 0, result.stderr
        return result

    vext, aext = ("mp4", "m4a") if video_codec == "libx264" else ("webm", "webm")
    high, low, audio = tmp_path / f"high.{vext}", tmp_path / f"low.{vext}", tmp_path / f"audio.{aext}"
    for size, path in ((64, high), (32, low)):
        run([tools["ffmpeg"], "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=black:s={size}x{size}:d=0.4", "-an", "-c:v", video_codec, path])
    run([tools["ffmpeg"], "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.4", "-vn", "-c:a", audio_codec, audio])
    vcodec, acodec = ("avc1.64000A", "mp4a.40.2") if video_codec == "libx264" else ("vp9", "opus")
    info = {
        "id": "abcdefghijk", "title": "Тест відео", "extractor": "generic",
        "webpage_url": "https://www.youtube.com/watch?v=abcdefghijk",
        "formats": [
            {"format_id": name, "url": path.as_uri(), "ext": vext, "vcodec": vcodec, "acodec": "none", "width": size, "height": size, "fps": 25}
            for name, size, path in (("low", 32, low), ("high", 64, high))
        ] + [{"format_id": "audio", "url": audio.as_uri(), "ext": aext, "vcodec": "none", "acodec": acodec, "abr": 128}],
    }
    fixture = tmp_path / "info.json"
    fixture.write_text(json.dumps(info), encoding="utf-8")
    inspection_args = build_inspection_arguments(info["webpage_url"], tools)
    metadata = json.loads(run([tools["yt-dlp"], *inspection_args[:-2], "--enable-file-urls", "--load-info-json", fixture]).stdout)
    summary = summarize_info(metadata)
    assert summary["format_id"] == "high+audio"
    assert not (tmp_path / "out").exists()
    if container != "auto":
        assert container in summary["containers"]
    args = build_arguments(info["webpage_url"], tmp_path / "out", tools, container, summary["format_id"])
    # Replace the YouTube input with local metadata; the production format,
    # merge, remux and progress options remain unchanged.
    result = run([tools["yt-dlp"], *args[:-2], "--enable-file-urls", "--load-info-json", fixture])
    events = [parse_output(line) for line in result.stdout.splitlines()]
    progress = [event for event in events if event["type"] == "progress"]
    assert progress and {event["format"] for event in progress} == {"high", "audio"}
    assert any(event["type"] == "processing" for event in events)
    final = Path(next(event["path"] for event in events if event["type"] == "file"))
    assert final.suffix == "." + expected
    assert "Тест відео" in final.name
    probe = json.loads(run([tools["ffprobe"], "-v", "error", "-show_entries", "stream=codec_name,codec_type,width,height", "-of", "json", final]).stdout)
    assert {stream["codec_name"] for stream in probe["streams"]} == ({"h264", "aac"} if video_codec == "libx264" else {"vp9", "opus"})
    assert next(stream for stream in probe["streams"] if stream["codec_type"] == "video")["width"] == 64

    def packet_hashes(path, stream):
        result = run([tools["ffprobe"], "-v", "error", "-select_streams", stream, "-show_packets", "-show_data_hash", "sha256", "-show_entries", "packet=data_hash", "-of", "json", path])
        return [packet["data_hash"] for packet in json.loads(result.stdout)["packets"]]
    # Identical encoded packet bytes prove the merger did not re-encode.
    assert packet_hashes(high, "v") == packet_hashes(final, "v")
    assert packet_hashes(audio, "a") == packet_hashes(final, "a")


@pytest.fixture(scope='module')
def single_stream_fixture(tmp_path_factory):
    configured = os.environ.get('YT_SLOP_DL_TEST_COMPONENTS')
    if not configured:
        pytest.skip('Private tools not configured')
    root = Path(configured) / detect_platform().key
    suffix = '.exe' if os.name == 'nt' else ''
    tools = {n:root/('ffmpeg' if n=='ffprobe' else n)/(n+suffix) for n in ('yt-dlp','ffmpeg','ffprobe','deno')}
    env = tool_environment(Path(configured))
    def run(args):
        result = subprocess.run([str(a) for a in args],capture_output=True,text=True,encoding='utf-8',timeout=45,env=env)
        assert result.returncode == 0, result.stderr
        return result
    folder = tmp_path_factory.mktemp('stream-fixture')
    for name,size in [('low',32),('high',64)]:
        run([tools['ffmpeg'],'-hide_banner','-loglevel','error','-f','lavfi','-i',f'color=c=black:s={size}x{size}:d=0.4','-an','-c:v','libx264',folder/f'{name}.mp4'])
    for name,codec,ext in [('aac','aac','m4a'),('opus','libopus','webm')]:
        run([tools['ffmpeg'],'-hide_banner','-loglevel','error','-f','lavfi','-i','sine=frequency=440:duration=0.4','-vn','-c:a',codec,folder/f'{name}.{ext}'])
    run([tools['ffmpeg'],'-hide_banner','-loglevel','error','-i',folder/'high.mp4','-i',folder/'aac.m4a','-c','copy',folder/'combined.mp4'])
    formats = [
        {'format_id':name,'url':(folder/f'{name}.mp4').as_uri(),'ext':'mp4','vcodec':'avc1','acodec':'none','width':size,'height':size,'fps':25}
        for name,size in [('low',32),('high',64)]
    ] + [
        {'format_id':'combined','url':(folder/'combined.mp4').as_uri(),'ext':'mp4','vcodec':'avc1','acodec':'mp4a','width':64,'height':64,'fps':25},
        {'format_id':'aac','url':(folder/'aac.m4a').as_uri(),'ext':'m4a','vcodec':'none','acodec':'mp4a','abr':64},
        {'format_id':'audio-opus','url':(folder/'opus.webm').as_uri(),'ext':'webm','vcodec':'none','acodec':'opus','abr':128},
    ]
    info = {'id':'abcdefghijk','title':'Single stream','extractor':'generic','webpage_url':'https://www.youtube.com/watch?v=abcdefghijk','formats':formats}
    return tools,run,folder,info


@pytest.mark.parametrize('quality,video,audio,container,expected',[
    ('low',True,True,'mkv',{'video','audio'}),
    ('low',True,False,'auto',{'video'}),
    ('aac',False,True,'auto',{'audio'}),
    ('opus',False,True,'auto',{'audio'}),
    ('aac',False,True,'m4a',{'audio'}),
    ('opus',False,True,'webm',{'audio'}),
    ('combined',False,True,'auto',{'audio'}),
])
def test_selected_quality_and_only_requested_streams(tmp_path,single_stream_fixture,quality,video,audio,container,expected):
    from app.media import select_streams
    tools,run,folder,info = single_stream_fixture
    # Same ranked metadata that yt-dlp would return after analysis.
    summary = summarize_info(info | {'requested_formats':[info['formats'][1],info['formats'][-1]]})
    if quality == 'combined':
        summary = summarize_info(info['formats'][2] | {'id':info['id']})
    selected = select_streams(summary,'audio-opus' if quality=='opus' else quality,video,audio)
    args = build_arguments(info['webpage_url'],tmp_path/'out',tools,container,selected['format_id'],video=video,audio=audio)
    fixture = tmp_path/'info.json'
    fixture.write_text(json.dumps(info))
    result = run([tools['yt-dlp'],*args[:-2],'--enable-file-urls','--load-info-json',fixture])
    events = [parse_output(line) for line in result.stdout.splitlines()]
    final = Path(next(e['path'] for e in events if e['type']=='file'))
    probe = json.loads(run([tools['ffprobe'],'-v','error','-show_entries','stream=codec_name,codec_type,width,height','-of','json',final]).stdout)
    assert {s['codec_type'] for s in probe['streams']} == expected
    if video:
        assert next(s for s in probe['streams'] if s['codec_type']=='video')['height'] == 32
    def hashes(path,stream):
        data = json.loads(run([tools['ffprobe'],'-v','error','-select_streams',stream,'-show_packets','-show_data_hash','sha256','-show_entries','packet=data_hash','-of','json',path]).stdout)
        return [p['data_hash'] for p in data['packets']]
    if video:
        assert hashes(folder/'low.mp4','v') == hashes(final,'v')
    if audio:
        source = folder/'opus.webm' if video or quality=='opus' else folder/'aac.m4a'
        assert hashes(source,'a') == hashes(final,'a')
    if not video and container=='auto':
        assert final.suffix == ('.opus' if quality=='opus' else '.m4a')


@pytest.mark.parametrize('video', [True, False])
def test_selected_dub_preserves_audio_packets(tmp_path, single_stream_fixture, video):
    from app.media import select_streams
    tools, run, folder, base = single_stream_fixture
    dub = tmp_path/'dub.m4a'
    run([tools['ffmpeg'], '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
         'sine=frequency=880:duration=0.4', '-vn', '-c:a', 'aac', dub])
    formats = [f | ({'language':'en'} if f['acodec'] != 'none' else {}) for f in base['formats']]
    formats.append({'format_id':'dub-ru', 'url':dub.as_uri(), 'ext':'m4a', 'vcodec':'none',
                    'acodec':'mp4a', 'language':'ru', 'abr':128})
    info = base | {'formats':formats}
    summary = summarize_info(info | {'requested_formats':[formats[1], formats[-2]]})
    selected = select_streams(summary, 'low' if video else 'dub-ru', video=video, audio_track_id='ru')
    args = build_arguments(info['webpage_url'], tmp_path/'out', tools, 'mkv' if video else 'auto',
                           selected['format_id'], video=video)
    fixture = tmp_path/'info.json'
    fixture.write_text(json.dumps(info), encoding='utf-8')
    result = run([tools['yt-dlp'], *args[:-2], '--enable-file-urls', '--load-info-json', fixture])
    final = Path(next(event['path'] for line in result.stdout.splitlines()
                      if (event := parse_output(line))['type'] == 'file'))
    def hashes(path):
        data = run([tools['ffprobe'], '-v', 'error', '-select_streams', 'a', '-show_packets',
                    '-show_data_hash', 'sha256', '-show_entries', 'packet=data_hash', '-of', 'json', path])
        return [packet['data_hash'] for packet in json.loads(data.stdout)['packets']]
    assert hashes(final) == hashes(dub)
    assert hashes(final) != hashes(folder/'aac.m4a')
    probe = json.loads(run([tools['ffprobe'], '-v', 'error', '-show_entries', 'stream=codec_type',
                           '-of', 'json', final]).stdout)
    assert [s['codec_type'] for s in probe['streams']].count('audio') == 1
    assert {s['codec_type'] for s in probe['streams']} == ({'video','audio'} if video else {'audio'})


@pytest.mark.parametrize('video,container,primary', [(True,'mkv','ru'), (True,'mp4','en'), (True,'webm','ru'), (False,'auto','ru')])
def test_multiple_tracks_order_default_language_and_stream_copy(tmp_path, single_stream_fixture, video, container, primary):
    from app.media import select_streams
    tools, run, folder, base = single_stream_fixture
    dub = tmp_path/'dub.webm'
    run([tools['ffmpeg'], '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
         'sine=frequency=880:duration=0.4', '-vn', '-c:a', 'libopus', dub])
    # WebM needs VP9/AV1 video; generate a tiny fixture for this case only.
    if container == 'webm':
        v = tmp_path/'video.webm'
        run([tools['ffmpeg'], '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
             'color=c=black:s=32x32:d=0.4', '-an', '-c:v', 'libvpx-vp9', v])
        video_format = {'format_id':'v', 'url':v.as_uri(), 'ext':'webm', 'vcodec':'vp9', 'acodec':'none', 'height':32}
    else:
        video_format = base['formats'][0]
    english = base['formats'][-1] | {'language':'en'}
    russian = {'format_id':'dub-ru','url':dub.as_uri(),'ext':'webm','vcodec':'none','acodec':'opus','language':'ru'}
    info = base | {'formats':[video_format, russian, english]}
    summary = summarize_info(info | {'requested_formats':[video_format, english]})
    order = [primary, 'en' if primary == 'ru' else 'ru']
    quality = video_format['format_id'] if video else 'dub-ru'
    selected = select_streams(summary, quality, video=video, audio_track_ids=order)
    tracks = [s | {'title': 'Русский "дубляж"' if s['language']=='ru' else 'English original'} for s in selected['audio_streams']]
    args = build_arguments(info['webpage_url'], tmp_path/'out', tools, container, selected['format_id'], video=video, audio_tracks=tracks)
    fixture = tmp_path/'info.json'
    fixture.write_text(json.dumps(info), encoding='utf-8')
    result = run([tools['yt-dlp'], *args[:-2], '--enable-file-urls', '--load-info-json', fixture])
    final = Path(next(event['path'] for line in result.stdout.splitlines() if (event := parse_output(line))['type']=='file'))
    probe = json.loads(run([tools['ffprobe'], '-v', 'error', '-show_streams', '-of', 'json', final]).stdout)
    audios = [s for s in probe['streams'] if s['codec_type']=='audio']
    assert len(audios) == 2
    assert [s['tags']['language'] for s in audios] == [('rus' if language=='ru' else 'eng') for language in order]
    assert [s['disposition']['default'] for s in audios] == [1,0]
    assert [s['tags'].get('title') or s['tags'].get('handler_name') for s in audios] == [s['title'] for s in tracks]
    assert {s['codec_type'] for s in probe['streams']} == ({'video','audio'} if video else {'audio'})
    def hashes(path, stream):
        data = json.loads(run([tools['ffprobe'], '-v', 'error', '-select_streams', stream, '-show_packets',
                              '-show_data_hash', 'sha256', '-show_entries', 'packet=data_hash', '-of', 'json', path]).stdout)
        return [p['data_hash'] for p in data['packets']]
    for index, language in enumerate(order):
        assert hashes(final, f'a:{index}') == hashes(dub if language=='ru' else folder/'opus.webm', 'a:0')
